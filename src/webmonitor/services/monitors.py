"""The sole monitor publication path: approval, idempotency and quota in one transaction."""
import hashlib
from uuid import UUID
from sqlalchemy import func, select, text

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog, Membership, User, Workspace
from webmonitor.db.models.conversations import Draft, IdempotencyRecord
from webmonitor.db.models.monitoring import Monitor, MonitorVersion
from webmonitor.db.models.notifications import MonitorNotificationRoute
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import CreateMonitorResult, DraftSpec, content_hash
from webmonitor.security.egress import validate_target
from webmonitor.services.approvals import validate_approval_for_creation


async def create_monitor_task(session, principal: Principal, *, draft_id: UUID,
                              confirmed_revision: int, confirmation_token: str,
                              idempotency_key: str) -> CreateMonitorResult:
    """Flush only; caller commits response and conversation events atomically."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise DomainError("invalid_idempotency_key", 422)
    membership = await session.scalar(select(Membership).join(User, User.id == Membership.user_id).where(Membership.workspace_id == principal.workspace_id, Membership.user_id == principal.user_id, Membership.active.is_(True), User.active.is_(True)).with_for_update(of=Membership))
    if membership is None:
        raise DomainError("unauthenticated",401)
    principal = Principal(user_id=principal.user_id, workspace_id=principal.workspace_id, role=membership.role)
    lock_key = int.from_bytes(hashlib.sha256(f"create:{principal.workspace_id}:{principal.user_id}:{idempotency_key}".encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    request_hash = content_hash({"draft_id":str(draft_id),"confirmed_revision":confirmed_revision,"approval_digest":hashlib.sha256(confirmation_token.encode()).hexdigest()})
    previous = await session.scalar(select(IdempotencyRecord).where(IdempotencyRecord.workspace_id == principal.workspace_id, IdempotencyRecord.user_id == principal.user_id, IdempotencyRecord.idempotency_key == idempotency_key))
    if previous:
        if previous.request_hash != request_hash:
            raise DomainError("idempotency_conflict",409)
        return CreateMonitorResult.model_validate(previous.result)
    draft = await session.scalar(select(Draft).where(Draft.id == draft_id, Draft.workspace_id == principal.workspace_id).with_for_update())
    if draft is None:
        raise DomainError("not_found",404)
    if principal.role != "admin" and draft.user_id != principal.user_id:
        raise DomainError("forbidden",403)
    route_revision = await session.scalar(select(AuditLog.id).where(AuditLog.workspace_id == principal.workspace_id, AuditLog.resource_id == draft.id, AuditLog.action == "monitor.routes.preview_requested").limit(1))
    if route_revision:
        raise DomainError("route_revision_invalid",409)
    if draft.current_revision != confirmed_revision:
        raise DomainError("stale_revision",409)
    existing = await session.scalar(select(Monitor).where(Monitor.draft_id == draft.id))
    if existing is not None:
        raise DomainError("already_published",409,details={"task_id":str(existing.id)})
    approval, revision, preview, routing = await validate_approval_for_creation(session, principal,
        draft=draft, revision=confirmed_revision, confirmation_token=confirmation_token)
    spec = DraftSpec.model_validate(revision.spec)
    await validate_target(spec.url)
    # Workspace row serializes quota decisions across drafts and owners.
    await session.execute(select(Workspace.id).where(Workspace.id == principal.workspace_id).with_for_update())
    count = await session.scalar(select(func.count()).select_from(Monitor).where(Monitor.workspace_id == principal.workspace_id, Monitor.status == "active"))
    if count >= 100:
        raise DomainError("quota_exceeded",409)
    monitor = Monitor(workspace_id=principal.workspace_id,created_by_user_id=principal.user_id,draft_id=draft.id,
        name=spec.name,url=spec.url,status="active",baseline_status="pending_first_success",next_run_at=utc_now())
    session.add(monitor)
    await session.flush()
    version = MonitorVersion(workspace_id=principal.workspace_id,monitor_id=monitor.id,version=1,
        created_by_user_id=principal.user_id,approval_id=approval.id,spec=revision.spec,spec_hash=revision.spec_hash,
        collection_mode=spec.collection_mode,interval_seconds=spec.schedule.interval_seconds,timezone=spec.schedule.timezone)
    session.add(version)
    await session.flush()
    monitor.current_version_id = version.id
    for group_id in spec.notification_group_ids:
        for event_type,binding in spec.template_bindings.items():
            session.add(MonitorNotificationRoute(workspace_id=principal.workspace_id,monitor_version_id=version.id,group_id=group_id,event_type=event_type,template_id=binding.template_id))
    approval.consumed_at = utc_now()
    draft.status = "published"
    result = CreateMonitorResult(task_id=monitor.id,monitor_version_id=version.id,status="active",baseline_status="pending_first_success",next_run_at=monitor.next_run_at,warnings=preview.warnings)
    session.add(IdempotencyRecord(workspace_id=principal.workspace_id,user_id=principal.user_id,idempotency_key=idempotency_key,
        request_hash=request_hash,draft_id=draft.id,confirmed_revision=confirmed_revision,task_id=monitor.id,result=result.model_dump(mode="json")))
    session.add(AuditLog(workspace_id=principal.workspace_id,actor_user_id=principal.user_id,action="monitor.created",resource_type="monitor",resource_id=monitor.id,details={"revision":confirmed_revision,"approval_id":str(approval.id)}))
    await session.flush()
    return result
