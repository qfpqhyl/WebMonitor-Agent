"""Real worker preview and one-time approval for existing monitor route versions."""
import hashlib
from uuid import UUID

from sqlalchemy import select, text, update

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog, Membership, User
from webmonitor.db.models.conversations import Conversation, Draft, IdempotencyRecord, Preview
from webmonitor.db.models.monitoring import Attempt, CollectionJob, Monitor, MonitorVersion, Run
from webmonitor.db.models.notifications import MonitorNotificationRoute
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import DraftSpec, content_hash
from webmonitor.schemas.notification_api import RoutePreviewJob, RouteUpdate, RouteView
from webmonitor.security.authorization import require_owner_or_admin, require_workspace
from webmonitor.services.approvals import approval_token, confirm_preview, validate_approval_for_creation
from webmonitor.services.drafts import revalidate_principal, require_draft_owner, save_monitor_draft
from webmonitor.services.previews import build_preview, enqueue_collection
from webmonitor.services.routing import load_routing_snapshot


async def _monitor(session, principal, monitor_id, *, write=False):
    principal = await revalidate_principal(session, principal)
    query = select(Monitor).where(Monitor.id == monitor_id, Monitor.workspace_id == principal.workspace_id)
    if write:
        query = query.with_for_update().execution_options(populate_existing=True)
    monitor = require_workspace(principal, await session.scalar(query))
    if monitor.status == "deleted":
        raise DomainError("not_found", 404)
    if write:
        require_owner_or_admin(principal, monitor)
    version = await session.scalar(select(MonitorVersion).where(MonitorVersion.id == monitor.current_version_id,
        MonitorVersion.workspace_id == principal.workspace_id))
    return principal, monitor, version


async def get_routes(session, principal, monitor_id):
    _, monitor, version = await _monitor(session, principal, monitor_id)
    spec = DraftSpec.model_validate(version.spec)
    return RouteView(monitor_id=monitor.id, base_version_id=version.id, version=version.version,
        notification_group_ids=spec.notification_group_ids, template_bindings=spec.template_bindings)


async def _registered_draft(session, principal, monitor_id, base_version_id, draft_id):
    registration = await session.scalar(select(AuditLog).where(AuditLog.workspace_id == principal.workspace_id,
        AuditLog.resource_id == draft_id, AuditLog.action == "monitor.routes.preview_requested"))
    if registration is None or registration.details != {
        "monitor_id": str(monitor_id), "base_version_id": str(base_version_id)}:
        raise DomainError("not_found", 404)
    draft = await session.scalar(select(Draft).where(Draft.id == draft_id,
        Draft.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
    require_draft_owner(principal, draft)
    return draft


async def request_preview(session, principal, monitor_id, body):
    principal, monitor, version = await _monitor(session, principal, monitor_id, write=True)
    if body.base_version_id != version.id:
        raise DomainError("stale_version", 409)
    data = dict(version.spec)
    data.update(notification_group_ids=[str(value) for value in body.notification_group_ids],
        template_bindings={key: value.model_dump(mode="json") for key, value in body.template_bindings.items()})
    spec = DraftSpec.model_validate(data)
    await load_routing_snapshot(session, principal.workspace_id, spec)
    if body.draft_id is None:
        conversation = Conversation(workspace_id=principal.workspace_id, user_id=principal.user_id,
            title=f"Notification routes: {monitor.name}"[:200])
        session.add(conversation)
        await session.flush()
        revision = await save_monitor_draft(session, principal, conversation_id=conversation.id, spec=spec)
        session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=principal.user_id,
            action="monitor.routes.preview_requested", resource_type="draft", resource_id=revision.draft_id,
            details={"monitor_id": str(monitor.id), "base_version_id": str(version.id)}))
    else:
        draft = await _registered_draft(session, principal, monitor.id, version.id, body.draft_id)
        conversation = await session.get(Conversation, draft.conversation_id)
        revision = await save_monitor_draft(session, principal, conversation_id=conversation.id,
            draft_id=draft.id, spec=spec)
    job = await enqueue_collection(session, principal, url=spec.url, mode=spec.collection_mode,
        spec=spec, draft_id=revision.draft_id, revision=revision.revision)
    await session.flush()
    return RoutePreviewJob(monitor_id=monitor.id, base_version_id=version.id,
        conversation_id=conversation.id, draft_id=revision.draft_id, revision=revision.revision,
        collection_job_id=job.id, status=job.status)


async def finish_preview(session, principal, monitor_id, draft_id, revision, job_id):
    principal, monitor, version = await _monitor(session, principal, monitor_id, write=True)
    draft = await _registered_draft(session, principal, monitor.id, version.id, draft_id)
    if draft.current_revision != revision:
        raise DomainError("stale_revision", 409)
    existing = await session.scalar(select(Preview).where(Preview.workspace_id == principal.workspace_id,
        Preview.draft_id == draft_id, Preview.revision == revision, Preview.collection_job_id == job_id,
        Preview.invalidated_at.is_(None)).order_by(Preview.created_at.desc()))
    if existing is not None:
        if existing.expires_at <= utc_now():
            raise DomainError("preview_expired", 409)
        return existing
    return await build_preview(session, principal, draft_id=draft_id, revision=revision, job_id=job_id)


async def confirm_routes(session, principal, monitor_id, body):
    membership = await session.scalar(select(Membership).join(User, User.id == Membership.user_id).where(
        Membership.workspace_id == principal.workspace_id, Membership.user_id == principal.user_id,
        Membership.active.is_(True), User.active.is_(True)).with_for_update(of=Membership))
    if membership is None:
        raise DomainError("unauthenticated", 401)
    principal = Principal(user_id=principal.user_id, workspace_id=principal.workspace_id, role=membership.role)
    key = int.from_bytes(hashlib.sha256(
        f"create:{principal.workspace_id}:{principal.user_id}:{body.idempotency_key}".encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    request_hash = content_hash({"operation": "notification_routes", "monitor_id": str(monitor_id),
        **body.model_dump(mode="json")})
    principal, monitor, current = await _monitor(session, principal, monitor_id, write=True)
    previous = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.workspace_id == principal.workspace_id, IdempotencyRecord.user_id == principal.user_id,
        IdempotencyRecord.idempotency_key == body.idempotency_key))
    if previous is not None:
        if previous.request_hash != request_hash:
            raise DomainError("idempotency_conflict", 409)
        return RouteUpdate.model_validate(previous.result)
    if current.id != body.base_version_id:
        raise DomainError("stale_version", 409)
    draft = await _registered_draft(session, principal, monitor.id, current.id, body.draft_id)
    approval = await confirm_preview(session, principal, draft_id=draft.id, revision=body.revision,
        preview_id=body.preview_id)
    approval, revision, _, _ = await validate_approval_for_creation(session, principal,
        draft=draft, revision=body.revision, confirmation_token=approval_token(approval))
    spec = DraftSpec.model_validate(revision.spec)
    # Even a client editing this Draft through another entrypoint cannot change
    # extraction/rules/schedule under a route-only approval.
    old = dict(current.spec)
    new = dict(revision.spec)
    for value in (old, new):
        value.pop("notification_group_ids", None)
        value.pop("template_bindings", None)
    if content_hash(old) != content_hash(new):
        raise DomainError("route_revision_invalid", 409)
    version = MonitorVersion(workspace_id=principal.workspace_id, monitor_id=monitor.id,
        version=current.version + 1, created_by_user_id=principal.user_id, approval_id=approval.id,
        spec=revision.spec, spec_hash=revision.spec_hash, collection_mode=current.collection_mode,
        interval_seconds=current.interval_seconds, timezone=current.timezone)
    session.add(version)
    await session.flush()
    for group_id in spec.notification_group_ids:
        for event_type, binding in spec.template_bindings.items():
            session.add(MonitorNotificationRoute(workspace_id=principal.workspace_id,
                monitor_version_id=version.id, group_id=group_id, event_type=event_type,
                template_id=binding.template_id))
    now = utc_now()
    runs = (await session.scalars(select(Run).where(Run.monitor_id == monitor.id,
        Run.workspace_id == principal.workspace_id, Run.status.in_(["queued", "running"]))
        .with_for_update())).all()
    for run in runs:
        await session.execute(update(Attempt).where(Attempt.run_id == run.id, Attempt.status == "running")
            .values(status="discarded", finished_at=now))
        await session.execute(update(CollectionJob).where(CollectionJob.run_id == run.id,
            CollectionJob.status.in_(["queued", "running"]))
            .values(status="discarded", finished_at=now, generation=CollectionJob.generation + 1))
        run.status, run.finished_at = "discarded", now
        run.attempt_generation += 1
    monitor.current_version_id = version.id
    # Baseline, health, rule state and paused/active status remain unchanged.
    if monitor.status == "active":
        monitor.next_run_at = now
    approval.consumed_at = now
    draft.status = "published"
    result = RouteUpdate(monitor_id=monitor.id, monitor_version_id=version.id, version=version.version,
        status=monitor.status, baseline_snapshot_id=monitor.baseline_snapshot_id)
    session.add(IdempotencyRecord(workspace_id=principal.workspace_id, user_id=principal.user_id,
        idempotency_key=body.idempotency_key, request_hash=request_hash, draft_id=draft.id,
        confirmed_revision=body.revision, task_id=monitor.id, result=result.model_dump(mode="json")))
    session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=principal.user_id,
        action="monitor.routes.updated", resource_type="monitor", resource_id=monitor.id,
        details={"previous_version_id": str(current.id), "version_id": str(version.id),
            "draft_id": str(draft.id), "approval_id": str(approval.id)}))
    await session.flush()
    return result
