"""Immutable monitor drafts; caller owns the transaction and commit."""
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Membership, User
from webmonitor.db.models.conversations import Approval, Conversation, Draft, DraftRevision, Preview
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import DraftSpec, content_hash


async def revalidate_principal(session: AsyncSession, principal: Principal) -> Principal:
    """Recheck live membership; callers needing revocation serialization lock first."""
    membership = await session.scalar(
        select(Membership).join(User, User.id == Membership.user_id).where(
            Membership.workspace_id == principal.workspace_id,
            Membership.user_id == principal.user_id,
            Membership.active.is_(True), User.active.is_(True),
        ).execution_options(populate_existing=True)
    )
    if membership is None:
        raise DomainError("forbidden", 403)
    return Principal(user_id=principal.user_id, workspace_id=principal.workspace_id, role=membership.role)


def require_draft_owner(principal: Principal, resource: Draft | Conversation | None) -> None:
    if resource is None or resource.workspace_id != principal.workspace_id:
        raise DomainError("not_found", 404)
    if resource.user_id != principal.user_id and principal.role != "admin":
        raise DomainError("forbidden", 403)


async def save_monitor_draft(
    session: AsyncSession, principal: Principal, *, conversation_id: UUID,
    spec: DraftSpec, draft_id: UUID | None = None,
) -> DraftRevision:
    """Every save creates a revision and invalidates previous preview authority."""
    principal = await revalidate_principal(session, principal)
    spec = DraftSpec.model_validate(spec)
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == conversation_id, Conversation.workspace_id == principal.workspace_id,
    ).with_for_update().execution_options(populate_existing=True))
    require_draft_owner(principal, conversation)
    if draft_id is None:
        draft = Draft(workspace_id=principal.workspace_id, conversation_id=conversation_id,
                      user_id=principal.user_id, current_revision=None, status="draft")
        session.add(draft)
        await session.flush()
    else:
        draft = await session.scalar(select(Draft).where(
            Draft.id == draft_id, Draft.workspace_id == principal.workspace_id,
        ).with_for_update().execution_options(populate_existing=True))
        require_draft_owner(principal, draft)
        if draft.conversation_id != conversation_id:
            raise DomainError("not_found", 404)
        if draft.status in {"published", "discarded"}:
            raise DomainError("draft_not_editable", 409)
    now = utc_now()
    await session.execute(update(Preview).where(
        Preview.workspace_id == principal.workspace_id, Preview.draft_id == draft.id,
        Preview.invalidated_at.is_(None),
    ).values(invalidated_at=now))
    await session.execute(update(Approval).where(
        Approval.workspace_id == principal.workspace_id, Approval.draft_id == draft.id,
        Approval.invalidated_at.is_(None), Approval.consumed_at.is_(None),
    ).values(invalidated_at=now))
    revision = DraftRevision(workspace_id=principal.workspace_id, draft_id=draft.id,
        revision=(draft.current_revision or 0) + 1, created_by_user_id=principal.user_id,
        spec=spec.model_dump(mode="json"), spec_hash=content_hash(spec))
    session.add(revision)
    # The current-revision FK requires the immutable row to exist first.
    await session.flush()
    draft.current_revision = revision.revision
    draft.status = "draft"
    await session.flush()
    return revision
