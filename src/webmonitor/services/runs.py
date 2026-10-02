"""Production scheduling and fenced commits. Every helper flushes; the caller commits.

Lock order is Monitor -> Run -> Attempt/CollectionJob. Network and evidence upload
must finish outside these transactions. Workers renew every LEASE_RENEW_SECONDS.
"""
import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select, text

from webmonitor.api.errors import DomainError
from webmonitor.collection.diff import evaluate_rules
from webmonitor.collection.validation import validate_collection
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog, Workspace
from webmonitor.db.models.monitoring import Attempt, CollectionJob, Event, Evidence, Monitor, MonitorVersion, Run, Snapshot
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.security.authorization import require_owner_or_admin
from webmonitor.services.event_delivery import enqueue_event_deliveries

LEASE_SECONDS = 90
LEASE_RENEW_SECONDS = 15
ACTIVE = ("queued", "running")

async def browser_capacity_available(session) -> bool:
    # One transaction lock serializes production and preview claims across processes.
    await session.execute(text("SELECT pg_advisory_xact_lock(774431083)"))
    active = await session.scalar(select(func.count()).select_from(CollectionJob).where(
        CollectionJob.collection_mode == "browser", CollectionJob.status == "running",
        CollectionJob.lease_expires_at > utc_now()))
    return active < 2


async def _monitor(session, monitor_id, *, skip_locked=False):
    return await session.scalar(select(Monitor).where(Monitor.id == monitor_id)
        .with_for_update(skip_locked=skip_locked).execution_options(populate_existing=True))


async def _locked_run(session, run_id):
    monitor_id = await session.scalar(select(Run.monitor_id).where(Run.id == run_id))
    if monitor_id is None:
        return None, None
    monitor = await _monitor(session, monitor_id)
    run = await session.scalar(select(Run).where(Run.id == run_id).with_for_update()
        .execution_options(populate_existing=True))
    return monitor, run


async def _new_run(session, monitor, *, trigger, scheduled_at=None, user_id=None):
    run = Run(workspace_id=monitor.workspace_id, monitor_id=monitor.id,
        monitor_version_id=monitor.current_version_id, baseline_snapshot_id=monitor.baseline_snapshot_id,
        requested_by_user_id=user_id, trigger=trigger, scheduled_at=scheduled_at,
        status="queued", available_at=utc_now(), attempt_count=0, attempt_generation=0)
    session.add(run)
    await session.flush()
    return run


async def schedule_due(session, *, limit=100) -> list[Run]:
    now = utc_now()
    monitors = (await session.scalars(select(Monitor).where(
        Monitor.status == "active", Monitor.next_run_at <= now,
    ).order_by(Monitor.next_run_at, Monitor.id).limit(limit)
      .with_for_update(skip_locked=True).execution_options(populate_existing=True))).all()
    created = []
    for monitor in monitors:
        version = await session.get(MonitorVersion, monitor.current_version_id)
        active = await session.scalar(select(Run.id).where(Run.monitor_id == monitor.id, Run.status.in_(ACTIVE)))
        if active is None:
            created.append(await _new_run(session, monitor, trigger="scheduled", scheduled_at=monitor.next_run_at))
        # Late ticks merge, and an overlapping tick is deliberately skipped.
        monitor.next_run_at = now + timedelta(seconds=version.interval_seconds)
    await session.flush()
    return created


async def _attempt_job(session, run):
    attempt = await session.scalar(select(Attempt).where(Attempt.run_id == run.id,
        Attempt.generation == run.attempt_generation).with_for_update()
        .execution_options(populate_existing=True))
    job = await session.scalar(select(CollectionJob).where(CollectionJob.run_id == run.id,
        CollectionJob.generation == run.attempt_generation).with_for_update()
        .execution_options(populate_existing=True))
    return attempt, job


def _live(run, generation, now):
    return (run is not None and run.status == "running" and run.attempt_generation == generation
        and run.cancel_requested_at is None and run.lease_expires_at is not None
        and run.lease_expires_at > now)


async def _finish_attempt(session, run, status, now, *, code=None, message=None, retryable=False, details=None):
    attempt, job = await _attempt_job(session, run)
    if attempt is not None:
        attempt.status, attempt.finished_at = status, now
        attempt.error_code, attempt.error_message = code, message
        attempt.retryable, attempt.details = retryable, details or {}
    if job is not None:
        job.status, job.finished_at = status, now
        job.error_code, job.error_message = code, message
        job.lease_owner = job.lease_expires_at = None
    run.lease_owner = run.lease_expires_at = None
    return attempt, job


async def _event(session, monitor, run, event_type, *, before, after, summary, fields, snapshot_id=None, differences=None, fingerprint=None):
    event = Event(id=uuid4(), workspace_id=monitor.workspace_id, monitor_id=monitor.id,
        monitor_version_id=run.monitor_version_id, run_id=run.id, snapshot_id=snapshot_id,
        type=event_type, detected_at=utc_now(), failure_fingerprint=fingerprint,
        change={"before": before, "after": after, "summary": summary,
                "differences": differences or []}, payload={})
    session.add(event)
    await session.flush()
    await enqueue_event_deliveries(session, monitor, event, fields)
    return event


async def _failure(session, monitor, run, *, now, error_code, error_message, retryable, details=None):
    # A caller cannot turn extraction/egress/contract rejection into retries.
    transient = error_code in {
        "lease_expired", "collection_timeout", "target_unavailable",
        "storage_unavailable", "browser_collection_failed",
    } or (error_code == "target_http_error" and
          int((details or {}).get("status_code", 0)) >= 500)
    retryable = retryable and transient
    await _finish_attempt(session, run, "failed", now, code=error_code,
        message=error_message, retryable=retryable, details=details)
    run.error_code, run.error_message = error_code, error_message
    if retryable and run.attempt_count < 3:
        run.status = "queued"
        run.available_at = now + timedelta(seconds=(1 if run.attempt_count == 1 else 5))
    else:
        run.status, run.finished_at = "failed", now
        fingerprint = hashlib.sha256(f"{monitor.id}:{error_code}".encode()).hexdigest()
        if monitor.health_status != "failed" or monitor.failure_fingerprint != fingerprint:
            baseline = await session.get(Snapshot, monitor.baseline_snapshot_id) if monitor.baseline_snapshot_id else None
            fields = baseline.extracted_data if baseline else {}
            await _event(session, monitor, run, "run_failed", before=fields or None,
                after=fields, summary=f"Collection failed: {error_code}", fields=fields,
                fingerprint=fingerprint)
        monitor.health_status, monitor.failure_fingerprint = "failed", fingerprint
        monitor.last_failure_at = now
    await session.flush()


async def claim_run(session, mode: str, worker_id: str) -> tuple[Run, Attempt, CollectionJob] | None:
    if mode not in ("http", "browser"):
        raise ValueError("invalid collection mode")
    if not worker_id or len(worker_id) > 200:
        raise ValueError("invalid worker ID")
    if mode == "browser" and not await browser_capacity_available(session):
        return None
    now = utc_now()
    claimable = or_(and_(Run.status == "queued", Run.available_at <= now),
        and_(Run.status == "running", Run.lease_expires_at <= now))
    # Lock monitors, not runs, first; concurrent claimers skip an occupied monitor.
    monitors = (await session.scalars(select(Monitor).join(Run, Run.monitor_id == Monitor.id)
        .join(MonitorVersion, MonitorVersion.id == Run.monitor_version_id)
        .where(claimable, MonitorVersion.collection_mode == mode)
        .order_by(Run.available_at, Monitor.id).limit(100)
        .with_for_update(of=Monitor, skip_locked=True)
        .execution_options(populate_existing=True))).all()
    for monitor in monitors:
        run = await session.scalar(select(Run).where(Run.monitor_id == monitor.id,
            claimable).with_for_update().execution_options(populate_existing=True))
        if run is None:
            continue
        if monitor.status != "active" or monitor.current_version_id != run.monitor_version_id or monitor.baseline_snapshot_id != run.baseline_snapshot_id:
            await _discard(session, run, now)
            continue
        if run.status == "running":
            await _failure(session, monitor, run, now=now, error_code="lease_expired",
                error_message="Worker lease expired", retryable=True)
            continue  # Recovery observes the same 1/5 second retry backoff.
        run.attempt_count += 1
        run.attempt_generation += 1
        run.status, run.lease_owner = "running", worker_id
        run.heartbeat_at, run.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
        run.started_at = run.started_at or now
        run.error_code = run.error_message = None
        version = await session.get(MonitorVersion, run.monitor_version_id)
        attempt = Attempt(id=uuid4(), workspace_id=run.workspace_id, run_id=run.id,
            attempt_number=run.attempt_count, generation=run.attempt_generation,
            worker_id=worker_id, status="running", started_at=now, retryable=False, details={})
        session.add(attempt)
        await session.flush()
        job = CollectionJob(id=uuid4(), workspace_id=run.workspace_id, kind="production",
            collection_mode=mode, status="running", run_id=run.id, attempt_id=attempt.id,
            url=monitor.url, spec=version.spec, available_at=now, started_at=now,
            lease_owner=worker_id, lease_expires_at=run.lease_expires_at,
            heartbeat_at=now, generation=run.attempt_generation)
        session.add(job)
        await session.flush()
        return run, attempt, job
    await session.flush()
    return None


async def renew_lease(session, run_id: UUID, generation: int, worker_id: str) -> bool:
    monitor, run = await _locked_run(session, run_id)
    now = utc_now()
    if not _live(run, generation, now) or run.lease_owner != worker_id:
        return False
    if monitor.status != "active" or monitor.current_version_id != run.monitor_version_id or monitor.baseline_snapshot_id != run.baseline_snapshot_id:
        await _discard(session, run, now)
        return False
    _, job = await _attempt_job(session, run)
    run.heartbeat_at, run.lease_expires_at = now, now + timedelta(seconds=LEASE_SECONDS)
    if job is not None:
        job.heartbeat_at, job.lease_expires_at = now, run.lease_expires_at
    await session.flush()
    return True


async def _discard(session, run, now):
    await _finish_attempt(session, run, "discarded", now)
    run.status, run.finished_at = "discarded", now
    await session.flush()


async def lock_run_result(session, run_id: UUID, generation: int) -> bool:
    """Fence and lock parents before an Evidence INSERT obtains foreign-key locks."""
    monitor, run = await _locked_run(session, run_id)
    now = utc_now()
    if not _live(run, generation, now):
        return False
    if monitor.status != "active" or monitor.current_version_id != run.monitor_version_id or monitor.baseline_snapshot_id != run.baseline_snapshot_id:
        await _discard(session, run, now)
        return False
    await _attempt_job(session, run)
    return True


async def commit_success(session, run_id: UUID, generation: int, result: dict) -> bool:
    monitor, run = await _locked_run(session, run_id)
    now = utc_now()
    if not _live(run, generation, now):
        return False
    if monitor.status != "active" or monitor.current_version_id != run.monitor_version_id or monitor.baseline_snapshot_id != run.baseline_snapshot_id:
        await _discard(session, run, now)
        return False
    version = await session.get(MonitorVersion, run.monitor_version_id)
    spec = DraftSpec.model_validate(version.spec)
    baseline = await session.get(Snapshot, monitor.baseline_snapshot_id) if monitor.baseline_snapshot_id else None
    previous = baseline.extracted_data if baseline else None
    fields, coverage = result["fields"], result["coverage"]
    validate_collection(spec, fields, previous=previous, coverage=coverage)
    diff = evaluate_rules(spec, previous, fields, monitor.rule_state or {})
    # Evidence must already exist, uploaded by the worker, and belong to this attempt.
    refs = result["evidence_refs"]
    if not refs:
        raise DomainError("evidence_required", 422)
    evidence_ids = [UUID(str(ref["id"])) for ref in refs]
    evidence = (await session.scalars(select(Evidence).where(Evidence.id.in_(evidence_ids),
        Evidence.workspace_id == run.workspace_id, Evidence.run_id == run.id,
        Evidence.attempt_id.in_(select(Attempt.id).where(Attempt.run_id == run.id, Attempt.generation == generation)))
        .with_for_update())).all()
    if len(evidence) != len(evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
        raise DomainError("evidence_invalid", 422)
    snapshot = Snapshot(id=uuid4(), workspace_id=run.workspace_id, monitor_id=monitor.id,
        monitor_version_id=version.id, run_id=run.id, final_url=result["final_url"],
        extracted_data=fields, coverage=coverage, rule_state=diff["rule_state"],
        evidence_refs=refs, collected_at=now)
    session.add(snapshot)
    await session.flush()
    for item in evidence:
        item.snapshot_id = snapshot.id
    if monitor.health_status == "failed":
        await _event(session, monitor, run, "recovered", before=previous, after=fields,
            summary="Collection recovered", fields=fields, snapshot_id=snapshot.id,
            fingerprint=monitor.failure_fingerprint)
    if diff["triggered"]:
        summary = "; ".join(f"{item['field']}: {json.dumps(item['before'], ensure_ascii=False)} → {json.dumps(item['after'], ensure_ascii=False)}" for item in diff["differences"])
        await _event(session, monitor, run, diff["event_type"], before=previous, after=fields,
            summary=summary, fields=fields, snapshot_id=snapshot.id, differences=diff["differences"])
    monitor.baseline_snapshot_id, monitor.baseline_status = snapshot.id, "ready"
    monitor.rule_state, monitor.last_success_at = diff["rule_state"], now
    monitor.health_status, monitor.failure_fingerprint = "healthy", None
    run.diff = diff
    run.warnings = list(result.get("warnings", [])) + [json.dumps(warning, sort_keys=True) for warning in diff["warnings"]]
    _, job = await _finish_attempt(session, run, "succeeded", now)
    if job is not None:
        job.result = result
    run.status, run.finished_at = "succeeded", now
    run.error_code = run.error_message = None
    await session.flush()
    return True


async def fail_run(session, run_id: UUID, generation: int, *, error_code: str,
                   error_message: str = "", retryable: bool = False, details: dict | None = None) -> bool:
    monitor, run = await _locked_run(session, run_id)
    now = utc_now()
    if not _live(run, generation, now):
        return False
    if monitor.status != "active" or monitor.current_version_id != run.monitor_version_id or monitor.baseline_snapshot_id != run.baseline_snapshot_id:
        await _discard(session, run, now)
        return False
    await _failure(session, monitor, run, now=now, error_code=error_code,
        error_message=error_message, retryable=retryable, details=details)
    return True


async def enqueue_manual(session, principal, monitor_id: UUID) -> Run:
    monitor = require_owner_or_admin(principal, await _monitor(session, monitor_id))
    if monitor.status != "active":
        raise DomainError("monitor_not_active", 409)
    existing = await session.scalar(select(Run).where(Run.monitor_id == monitor.id, Run.status.in_(ACTIVE)))
    if existing is not None:
        return existing
    return await _new_run(session, monitor, trigger="manual", user_id=principal.user_id)


async def _cancel_locked(session, run, now):
    run.cancel_requested_at = now
    await _finish_attempt(session, run, "cancelled", now)
    run.status, run.finished_at = "cancelled", now
    run.attempt_generation += 1  # Invalidate every holder, including paused work.
    await session.flush()


async def pause_monitor(session, principal, monitor_id: UUID) -> Monitor:
    monitor = require_owner_or_admin(principal, await _monitor(session, monitor_id))
    if monitor.status == "deleted":
        raise DomainError("not_found", 404)
    now = utc_now()
    monitor.status, monitor.paused_at = "paused", now
    runs = (await session.scalars(select(Run).where(Run.monitor_id == monitor.id,
        Run.status.in_(ACTIVE)).with_for_update())).all()
    for run in runs:
        await _cancel_locked(session, run, now)
    session.add(AuditLog(workspace_id=monitor.workspace_id, actor_user_id=principal.user_id,
        action="monitor.paused", resource_type="monitor", resource_id=monitor.id, details={}))
    await session.flush()
    return monitor


async def resume_monitor(session, principal, monitor_id: UUID) -> Monitor:
    # Workspace before Monitor matches create's quota serialization. No other run
    # path takes a Workspace lock after a Monitor lock.
    await session.execute(select(Workspace.id).where(Workspace.id == principal.workspace_id).with_for_update())
    monitor = require_owner_or_admin(principal, await _monitor(session, monitor_id))
    if monitor.status == "deleted":
        raise DomainError("not_found", 404)
    if monitor.status != "active":
        count = await session.scalar(select(func.count()).select_from(Monitor).where(
            Monitor.workspace_id == principal.workspace_id, Monitor.status == "active"))
        if count >= 100:
            raise DomainError("quota_exceeded", 409)
        monitor.status, monitor.paused_at, monitor.next_run_at = "active", None, utc_now()
        session.add(AuditLog(workspace_id=monitor.workspace_id, actor_user_id=principal.user_id,
            action="monitor.resumed", resource_type="monitor", resource_id=monitor.id, details={}))
    await session.flush()
    return monitor


async def cancel_run(session, principal, run_id: UUID) -> Run:
    monitor, run = await _locked_run(session, run_id)
    require_owner_or_admin(principal, monitor)
    if run.status in ACTIVE:
        await _cancel_locked(session, run, utc_now())
    return run
