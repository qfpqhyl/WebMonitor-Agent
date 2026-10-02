"""Conversation commands share durable state and events in caller-owned transactions."""
import hashlib
from uuid import UUID

from sqlalchemy import select, text, update

from webmonitor.agent.events import append_event
from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.conversations import (AgentCheckpoint, AgentRun, Approval, Conversation,
    Draft, DraftRevision, IdempotencyRecord, Message, Preview)
from webmonitor.db.models.monitoring import CollectionJob, Monitor
from webmonitor.schemas.conversations import (ApprovalView, ConversationSnapshot, DraftView,
    MessageView, PreviewView, RunResponse, RunView)
from webmonitor.schemas.monitors import CreateMonitorResult
from webmonitor.services.approvals import approval_token, confirm_preview
from webmonitor.services.drafts import revalidate_principal
from webmonitor.services.monitors import create_monitor_task

ACTIVE = ("queued", "running", "waiting_approval")


async def require_conversation(session, principal, conversation_id, *, lock=False):
    statement = select(Conversation).where(Conversation.id == conversation_id,
        Conversation.workspace_id == principal.workspace_id)
    if lock:
        statement = statement.with_for_update()
    conversation = await session.scalar(statement.execution_options(populate_existing=True))
    if conversation is None:
        raise DomainError("not_found", 404)
    if conversation.user_id != principal.user_id:
        raise DomainError("forbidden", 403)
    return conversation


async def create_conversation(session, principal, title=None):
    await revalidate_principal(session, principal)
    conversation = Conversation(workspace_id=principal.workspace_id,
        user_id=principal.user_id, title=title)
    session.add(conversation)
    await session.flush()
    return await snapshot(session, principal, conversation.id)


async def snapshot(session, principal, conversation_id):
    # Block event publication while reading its committed resource projection.
    conversation = await require_conversation(session, principal, conversation_id, lock=True)
    scope = (AgentRun.workspace_id == principal.workspace_id,
        AgentRun.conversation_id == conversation.id)
    runs = (await session.scalars(select(AgentRun).where(*scope).order_by(AgentRun.created_at, AgentRun.id))).all()
    messages = (await session.scalars(select(Message).where(Message.workspace_id == principal.workspace_id,
        Message.conversation_id == conversation.id).order_by(Message.created_at, Message.id))).all()
    drafts = (await session.scalars(select(Draft).where(Draft.workspace_id == principal.workspace_id,
        Draft.conversation_id == conversation.id).order_by(Draft.created_at, Draft.id))).all()
    resources = []
    for draft in drafts:
        revision = await session.scalar(select(DraftRevision).where(DraftRevision.workspace_id == principal.workspace_id,
            DraftRevision.draft_id == draft.id, DraftRevision.revision == draft.current_revision))
        preview = await session.scalar(select(Preview).where(Preview.workspace_id == principal.workspace_id,
            Preview.draft_id == draft.id, Preview.revision == draft.current_revision).order_by(Preview.created_at.desc(), Preview.id.desc()).limit(1))
        approval = await session.scalar(select(Approval).where(Approval.workspace_id == principal.workspace_id,
            Approval.draft_id == draft.id, Approval.revision == draft.current_revision).order_by(Approval.created_at.desc(), Approval.id.desc()).limit(1))
        task_id = await session.scalar(select(Monitor.id).where(Monitor.workspace_id == principal.workspace_id, Monitor.draft_id == draft.id))
        resources.append(DraftView(id=draft.id, status=draft.status, revision=draft.current_revision,
            spec=revision.spec if revision else None, spec_hash=revision.spec_hash if revision else None,
            preview=PreviewView.model_validate(preview) if preview else None,
            approval=ApprovalView.model_validate(approval) if approval else None, task_id=task_id))
    run_views = []
    for run in runs:
        view = RunView.model_validate(run)
        if run.status != "cancelled":
            from webmonitor.agent.checkpoints import checkpoint_create_arguments
            from webmonitor.schemas.conversations import PendingApprovalView
            checkpoint = await session.scalar(select(AgentCheckpoint).where(
                AgentCheckpoint.workspace_id == principal.workspace_id,
                AgentCheckpoint.agent_run_id == run.id).order_by(AgentCheckpoint.sequence.desc()).limit(1))
            try:
                if checkpoint is None:
                    if run.status == "waiting_approval":
                        raise DomainError("checkpoint_incompatible", 409)
                    run_views.append(view)
                    continue
                arguments = checkpoint_create_arguments(checkpoint)
                resource = next((item for item in resources if str(item.id) == str(arguments["draft_id"])), None)
                preview_id = resource.preview.id if resource and resource.preview else None
                view.pending_approval = PendingApprovalView(**arguments, preview_id=preview_id)
            except DomainError as exc:
                view.checkpoint_error = exc.code
        run_views.append(view)
    return ConversationSnapshot(id=conversation.id, title=conversation.title,
        created_at=conversation.created_at, last_seq=conversation.last_seq,
        messages=[MessageView.model_validate(item) for item in messages],
        runs=run_views, drafts=resources)


async def post_message(session, principal, conversation_id, payload):
    await revalidate_principal(session, principal)
    # Serialize all conversations of this user before examining either unique index.
    key = int.from_bytes(hashlib.sha256(f"agent-user:{principal.workspace_id}:{principal.user_id}".encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    await require_conversation(session, principal, conversation_id, lock=True)
    existing = await session.scalar(select(Message).where(Message.workspace_id == principal.workspace_id,
        Message.conversation_id == conversation_id, Message.client_message_id == payload.client_message_id))
    if existing is not None:
        if existing.content != payload.content:
            raise DomainError("idempotency_conflict", 409)
        run = await session.get(AgentRun, existing.agent_run_id)
        return response_for_run(run)
    active = await session.scalar(select(AgentRun).where(AgentRun.workspace_id == principal.workspace_id,
        AgentRun.user_id == principal.user_id, AgentRun.status.in_(ACTIVE)))
    if active is not None:
        code = "run_in_progress" if active.conversation_id == conversation_id else "quota_exceeded"
        raise DomainError(code, 409, details={"agent_run_id": str(active.id)})
    run = AgentRun(workspace_id=principal.workspace_id, user_id=principal.user_id,
        conversation_id=conversation_id, client_message_id=payload.client_message_id, status="queued")
    session.add(run)
    await session.flush()
    message = Message(workspace_id=principal.workspace_id, conversation_id=conversation_id,
        agent_run_id=run.id, user_id=principal.user_id, client_message_id=payload.client_message_id,
        role="user", content=payload.content)
    session.add(message)
    await session.flush()
    await append_event(session, run, "message.completed", {"message_id": str(message.id), "role": "user", "content": message.content})
    return response_for_run(run)


def response_for_run(run):
    result = CreateMonitorResult.model_validate(run.result) if run.result and "task_id" in run.result else None
    return RunResponse(agent_run_id=run.id, status=run.status,
        task_id=result.task_id if result else None, result=result)


async def bound_draft(session, principal, conversation_id, draft_id):
    await require_conversation(session, principal, conversation_id)
    draft = await session.scalar(select(Draft).where(Draft.workspace_id == principal.workspace_id,
        Draft.id == draft_id, Draft.conversation_id == conversation_id).with_for_update().execution_options(populate_existing=True))
    if draft is None:
        raise DomainError("not_found", 404)
    if draft.user_id != principal.user_id:
        raise DomainError("forbidden", 403)
    return draft


async def committed_result(session, principal, draft, revision, key):
    previous = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.workspace_id == principal.workspace_id,
        IdempotencyRecord.user_id == principal.user_id, IdempotencyRecord.idempotency_key == key))
    if previous is not None:
        if previous.draft_id != draft.id or previous.confirmed_revision != revision:
            raise DomainError("idempotency_conflict", 409)
        return CreateMonitorResult.model_validate(previous.result)
    # Publication is a single-use draft fact, including races with cancellation.
    records = await session.scalar(select(IdempotencyRecord).where(
        IdempotencyRecord.workspace_id == principal.workspace_id,
        IdempotencyRecord.user_id == principal.user_id, IdempotencyRecord.draft_id == draft.id))
    if records is not None and records.confirmed_revision == revision:
        return CreateMonitorResult.model_validate(records.result)
    return None


async def confirm(session, principal, conversation_id, payload):
    from webmonitor.agent.checkpoints import checkpoint_create_arguments
    await revalidate_principal(session, principal)
    await require_conversation(session, principal, conversation_id)
    run = await session.scalar(select(AgentRun).where(AgentRun.workspace_id == principal.workspace_id,
        AgentRun.conversation_id == conversation_id).order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1).with_for_update().execution_options(populate_existing=True))
    if run is None:
        raise DomainError("confirmation_required", 409)
    await require_conversation(session, principal, conversation_id, lock=True)
    draft = await bound_draft(session, principal, conversation_id, payload.draft_id)
    result = await committed_result(session, principal, draft, payload.revision, payload.idempotency_key)
    if result:
        return RunResponse(agent_run_id=run.id, status="completed", task_id=result.task_id, result=result)
    checkpoint = await session.scalar(select(AgentCheckpoint).where(AgentCheckpoint.workspace_id == principal.workspace_id,
        AgentCheckpoint.agent_run_id == run.id).order_by(AgentCheckpoint.sequence.desc()).limit(1))
    if checkpoint is None or checkpoint.tool_call_id is None:
        raise DomainError("checkpoint_incompatible", 409)
    arguments = checkpoint_create_arguments(checkpoint)
    if (str(arguments["draft_id"]) != str(payload.draft_id)
            or arguments["confirmed_revision"] != payload.revision
            or arguments["idempotency_key"] != payload.idempotency_key):
        raise DomainError("confirmation_mismatch", 409)
    existing = await session.scalar(select(Approval).where(Approval.workspace_id == principal.workspace_id,
        Approval.draft_id == draft.id, Approval.agent_run_id == run.id,
        Approval.consumed_at.is_(None), Approval.invalidated_at.is_(None)))
    if existing and run.status in {"queued", "running"}:
        if existing.revision != payload.revision or existing.preview_id != payload.preview_id:
            raise DomainError("stale_revision", 409)
        if existing.expires_at <= utc_now():
            raise DomainError("preview_expired", 409)
        return response_for_run(run)
    if run.status != "waiting_approval":
        raise DomainError("confirmation_required", 409)
    approval = await confirm_preview(session, principal, draft_id=draft.id,
        revision=payload.revision, preview_id=payload.preview_id,
        agent_run_id=run.id, tool_call_id=checkpoint.tool_call_id)
    run.status = "queued"
    run.available_at = utc_now()
    run.lease_owner = None
    run.lease_expires_at = None
    await append_event(session, run, "approval.required", {"approval_id": str(approval.id), "status": "confirmed", "draft_id": str(draft.id), "revision": payload.revision, "preview_id": str(payload.preview_id), "idempotency_key": payload.idempotency_key}, tool_call_id=checkpoint.tool_call_id)
    return response_for_run(run)


async def create_from_approval(session, principal, conversation_id, payload):
    await revalidate_principal(session, principal)
    await require_conversation(session, principal, conversation_id)
    approval_run_id = await session.scalar(select(Approval.agent_run_id).where(
        Approval.workspace_id == principal.workspace_id, Approval.user_id == principal.user_id,
        Approval.draft_id == payload.draft_id, Approval.revision == payload.confirmed_revision,
    ).order_by(Approval.created_at.desc(), Approval.id.desc()).limit(1))
    run = None
    if approval_run_id:
        run = await session.scalar(select(AgentRun).where(AgentRun.id == approval_run_id,
            AgentRun.workspace_id == principal.workspace_id).with_for_update().execution_options(populate_existing=True))
        if run is None or run.conversation_id != conversation_id:
            raise DomainError("not_found", 404)
    # Match tool publication: AgentRun -> Conversation -> Membership -> Draft.
    conversation = await require_conversation(session, principal, conversation_id, lock=True)
    from webmonitor.db.models.accounts import Membership, User
    membership = await session.scalar(select(Membership).join(User, User.id == Membership.user_id).where(
        Membership.workspace_id == principal.workspace_id, Membership.user_id == principal.user_id,
        Membership.active.is_(True), User.active.is_(True)).with_for_update(of=Membership))
    if membership is None:
        raise DomainError("forbidden", 403)
    draft = await bound_draft(session, principal, conversation_id, payload.draft_id)
    previous = await committed_result(session, principal, draft, payload.confirmed_revision, payload.idempotency_key)
    if previous:
        return previous
    if run and run.status == "cancelled":
        raise DomainError("confirmation_required", 409)
    approval = await session.scalar(select(Approval).where(Approval.workspace_id == principal.workspace_id,
        Approval.user_id == principal.user_id, Approval.draft_id == draft.id,
        Approval.revision == payload.confirmed_revision, Approval.consumed_at.is_(None),
        Approval.invalidated_at.is_(None)).with_for_update())
    if approval is None:
        raise DomainError("confirmation_required", 409)
    result = await create_monitor_task(session, principal, draft_id=draft.id,
        confirmed_revision=payload.confirmed_revision, confirmation_token=approval_token(approval),
        idempotency_key=payload.idempotency_key)
    if run:
        run.status = "completed"
        run.result = result.model_dump(mode="json")
        run.finished_at = utc_now()
        if conversation.session_lease_owner == run.lease_owner and conversation.session_generation == run.generation:
            conversation.session_lease_owner = None
            conversation.session_lease_expires_at = None
        run.generation += 1
        run.lease_owner = None
        run.lease_expires_at = None
        await append_event(session, run, "run.completed", run.result)
    return result


async def cancel(session, principal, conversation_id, run_id):
    await revalidate_principal(session, principal)
    await require_conversation(session, principal, conversation_id)
    run = await session.scalar(select(AgentRun).where(AgentRun.workspace_id == principal.workspace_id,
        AgentRun.conversation_id == conversation_id, AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True))
    if run is None:
        raise DomainError("not_found", 404)
    conversation = await require_conversation(session, principal, conversation_id, lock=True)
    # Monitor publication and cancellation serialize on the AgentRun lock.
    if run.result and "task_id" in run.result:
        return response_for_run(run)
    published = await session.scalar(select(IdempotencyRecord).join(Approval,
        Approval.draft_id == IdempotencyRecord.draft_id).where(
        IdempotencyRecord.workspace_id == principal.workspace_id,
        Approval.workspace_id == principal.workspace_id, Approval.agent_run_id == run.id,
        Approval.consumed_at.is_not(None)))
    if published:
        run.status = "completed"
        run.result = published.result
        run.finished_at = utc_now()
        await append_event(session, run, "run.completed", published.result)
        return response_for_run(run)
    if run.status not in ACTIVE:
        return response_for_run(run)
    now = utc_now()
    run.status = "cancelled"
    run.cancel_requested_at = now
    run.finished_at = now
    if conversation.session_lease_owner == run.lease_owner and conversation.session_generation == run.generation:
        conversation.session_lease_owner = None
        conversation.session_lease_expires_at = None
    run.generation += 1
    run.lease_owner = None
    run.lease_expires_at = None
    await session.execute(update(CollectionJob).where(CollectionJob.workspace_id == principal.workspace_id,
        CollectionJob.agent_run_id == run.id, CollectionJob.status.in_(("queued", "running"))).values(
        status="cancelled", cancel_requested_at=now, finished_at=now,
        generation=CollectionJob.generation + 1, lease_owner=None, lease_expires_at=None))
    await session.execute(update(Approval).where(Approval.workspace_id == principal.workspace_id,
        Approval.agent_run_id == run.id, Approval.consumed_at.is_(None),
        Approval.invalidated_at.is_(None)).values(invalidated_at=now))
    await append_event(session, run, "run.cancelled", {"status": "cancelled"})
    return response_for_run(run)
