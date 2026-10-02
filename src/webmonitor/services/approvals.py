"""Version-bound, one-time approvals; tokens never enter persisted model history."""
import base64
import binascii
import hmac
import json
import re
from datetime import timedelta
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.conversations import AgentRun, Approval, Draft, DraftRevision, Preview
from webmonitor.db.models.monitoring import CollectionJob
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import DraftSpec, content_hash
from webmonitor.security.csrf import derive_signing_key
from webmonitor.services.drafts import require_draft_owner, revalidate_principal
from webmonitor.services.routing import RoutingSnapshot, load_routing_snapshot

_TOKEN = re.compile(r"v1\.([A-Za-z0-9_-]+)\.([A-Za-z0-9_-]{43})\Z")


class _ApprovalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: StrictInt = Field(ge=1, le=1)
    approval_id: UUID
    user_id: UUID
    workspace_id: UUID
    draft_id: UUID
    revision: StrictInt = Field(gt=0)
    preview_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime


def _approval_payload(approval: Approval) -> dict:
    return _ApprovalPayload(schema_version=1, approval_id=approval.id,
        user_id=approval.user_id, workspace_id=approval.workspace_id,
        draft_id=approval.draft_id, revision=approval.revision,
        preview_hash=approval.preview_hash, expires_at=approval.expires_at).model_dump(mode="json")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def approval_token(approval: Approval) -> str:
    """Reconstruct the same token from an unconsumed persisted approval."""
    if approval.consumed_at is not None or approval.invalidated_at is not None:
        raise DomainError("confirmation_required", 409)
    if approval.expires_at <= utc_now():
        raise DomainError("preview_expired", 409)
    payload = json.dumps(_approval_payload(approval), sort_keys=True, separators=(",", ":"))
    body = "v1." + _encode(payload.encode("utf-8"))
    return body + "." + _encode(hmac.digest(derive_signing_key("approval"), body.encode("ascii"), "sha256"))


def _verified_payload(token: str, principal: Principal, revision: int) -> _ApprovalPayload:
    if not isinstance(token, str) or len(token) > 4096:
        raise DomainError("confirmation_required", 409)
    match = _TOKEN.fullmatch(token)
    if match is None:
        raise DomainError("confirmation_required", 409)
    body = token.rsplit(".", 1)[0]
    expected = _encode(hmac.digest(derive_signing_key("approval"), body.encode("ascii"), "sha256"))
    if not hmac.compare_digest(match[2], expected):
        raise DomainError("confirmation_required", 409)
    try:
        raw = base64.b64decode(match[1] + "=" * (-len(match[1]) % 4), altchars=b"-_", validate=True)
        if _encode(raw) != match[1]:
            raise ValueError("Noncanonical encoding")
        payload = _ApprovalPayload.model_validate_json(raw)
        # Reject duplicate JSON keys, alternate UUID spellings and alternate JSON
        # encodings even when a correctly signed payload is supplied.
        canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        if raw != canonical.encode("utf-8"):
            raise ValueError("Noncanonical payload")
    except (ValueError, ValidationError, binascii.Error, UnicodeError):
        raise DomainError("confirmation_required", 409) from None
    if payload.user_id != principal.user_id or payload.workspace_id != principal.workspace_id:
        raise DomainError("confirmation_required", 409)
    if payload.revision != revision:
        raise DomainError("stale_revision", 409)
    if payload.expires_at <= utc_now():
        raise DomainError("preview_expired", 409)
    return payload


def verify_approval_token(token: str, principal: Principal, revision: int) -> UUID:
    """Verify the envelope only; create must additionally lock/check its DB row."""
    return _verified_payload(token, principal, revision).approval_id


def compute_preview_hash(*, spec_hash: str, extracted_data: dict, coverage: dict,
    evidence_refs: list[dict], rule_simulation: dict, rendered_emails: list[dict],
    recipient_snapshot: list[dict], template_snapshot: list[dict]) -> str:
    """Hash the complete frozen result, shared with the real preview producer."""
    return content_hash({"spec_hash": spec_hash, "extracted_data": extracted_data,
        "coverage": coverage, "evidence_refs": evidence_refs, "rule_simulation": rule_simulation,
        "rendered_emails": rendered_emails, "recipient_snapshot": recipient_snapshot,
        "template_snapshot": template_snapshot})


async def validate_preview_for_confirmation(
    session: AsyncSession, principal: Principal, *, draft: Draft, revision: int,
    preview_id: UUID,
) -> tuple[DraftRevision, Preview, RoutingSnapshot]:
    """Caller holds the draft lock, serializing edit/confirm/create."""
    principal = await revalidate_principal(session, principal)
    require_draft_owner(principal, draft)
    if draft.current_revision != revision:
        raise DomainError("stale_revision", 409)
    if draft.status in {"discarded", "published"}:
        raise DomainError("confirmation_required", 409)
    draft_revision = await session.scalar(select(DraftRevision).where(
        DraftRevision.workspace_id == principal.workspace_id, DraftRevision.draft_id == draft.id,
        DraftRevision.revision == revision,
    ).execution_options(populate_existing=True))
    preview = await session.scalar(select(Preview).where(
        Preview.workspace_id == principal.workspace_id, Preview.draft_id == draft.id,
        Preview.revision == revision, Preview.id == preview_id,
    ).with_for_update().execution_options(populate_existing=True))
    if draft_revision is None or preview is None or preview.invalidated_at is not None:
        raise DomainError("confirmation_required", 409)
    if preview.expires_at <= utc_now():
        raise DomainError("preview_expired", 409)
    if preview.expires_at > preview.created_at + timedelta(minutes=15):
        raise DomainError("confirmation_required", 409)
    spec = DraftSpec.model_validate(draft_revision.spec)
    if draft_revision.spec_hash != content_hash(spec) or preview.spec_hash != draft_revision.spec_hash:
        raise DomainError("stale_revision", 409)
    job = await session.scalar(select(CollectionJob).where(
        CollectionJob.id == preview.collection_job_id,
        CollectionJob.workspace_id == principal.workspace_id,
    ).with_for_update(read=True).execution_options(populate_existing=True))
    if (job is None or job.kind != "preview" or job.status != "succeeded"
            or job.draft_id != draft.id or job.revision != revision
            or job.spec is None or content_hash(DraftSpec.model_validate(job.spec)) != preview.spec_hash
            or job.url != spec.url or job.collection_mode != spec.collection_mode):
        raise DomainError("confirmation_required", 409)
    if preview.preview_hash != compute_preview_hash(spec_hash=preview.spec_hash,
            extracted_data=preview.extracted_data, coverage=preview.coverage,
            evidence_refs=preview.evidence_refs, rule_simulation=preview.rule_simulation,
            rendered_emails=preview.rendered_emails, recipient_snapshot=preview.recipient_snapshot,
            template_snapshot=preview.template_snapshot):
        raise DomainError("preview_stale", 409)
    routes = await load_routing_snapshot(session, principal.workspace_id, spec)
    if (routes.recipient_snapshot != preview.recipient_snapshot
            or routes.template_snapshot != preview.template_snapshot):
        raise DomainError("preview_stale", 409, "Notification routes changed; preview again")
    return draft_revision, preview, routes


async def confirm_preview(
    session: AsyncSession, principal: Principal, *, draft_id: UUID, revision: int,
    preview_id: UUID, agent_run_id: UUID | None = None, tool_call_id: str | None = None,
) -> Approval:
    principal = await revalidate_principal(session, principal)
    draft = await session.scalar(select(Draft).where(
        Draft.id == draft_id, Draft.workspace_id == principal.workspace_id,
    ).with_for_update().execution_options(populate_existing=True))
    require_draft_owner(principal, draft)
    _, preview, _ = await validate_preview_for_confirmation(
        session, principal, draft=draft, revision=revision, preview_id=preview_id,
    )
    if tool_call_id is not None and (agent_run_id is None or not 1 <= len(tool_call_id) <= 200):
        raise DomainError("confirmation_required", 409)
    if agent_run_id is not None:
        run = await session.scalar(select(AgentRun).where(
            AgentRun.id == agent_run_id, AgentRun.workspace_id == principal.workspace_id,
        ).with_for_update().execution_options(populate_existing=True))
        if run is None or run.conversation_id != draft.conversation_id:
            raise DomainError("not_found", 404)
        if run.user_id != principal.user_id or run.status != "waiting_approval":
            raise DomainError("confirmation_required", 409)
    existing = await session.scalar(select(Approval).where(
        Approval.workspace_id == principal.workspace_id, Approval.draft_id == draft.id,
        Approval.consumed_at.is_(None), Approval.invalidated_at.is_(None),
    ).with_for_update().execution_options(populate_existing=True))
    now = utc_now()
    if (existing is not None and existing.user_id == principal.user_id
            and existing.revision == revision and existing.preview_id == preview.id
            and existing.preview_hash == preview.preview_hash and existing.expires_at > now
            and existing.agent_run_id == agent_run_id and existing.tool_call_id == tool_call_id):
        return existing
    if existing is not None:
        existing.invalidated_at = now
        # Release the partial unique index before inserting its successor.
        await session.flush()
    approval = Approval(workspace_id=principal.workspace_id, user_id=principal.user_id,
        draft_id=draft.id, revision=revision, preview_id=preview.id, preview_hash=preview.preview_hash,
        agent_run_id=agent_run_id, tool_call_id=tool_call_id, expires_at=preview.expires_at)
    session.add(approval)
    draft.status = "confirmed"
    await session.flush()
    return approval


async def validate_approval_for_creation(
    session: AsyncSession, principal: Principal, *, draft: Draft, revision: int,
    confirmation_token: str,
) -> tuple[Approval, DraftRevision, Preview, RoutingSnapshot]:
    """Caller locks Draft and consumes returned Approval with monitor creation."""
    principal = await revalidate_principal(session, principal)
    require_draft_owner(principal, draft)
    payload = _verified_payload(confirmation_token, principal, revision)
    approval = await session.scalar(select(Approval).where(
        Approval.id == payload.approval_id, Approval.workspace_id == principal.workspace_id,
        Approval.draft_id == draft.id, Approval.user_id == principal.user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if (approval is None or approval.consumed_at is not None or approval.invalidated_at is not None
            or approval.revision != revision or approval.preview_hash != payload.preview_hash
            or approval.draft_id != payload.draft_id or approval.expires_at != payload.expires_at):
        raise DomainError("confirmation_required", 409)
    if approval.expires_at <= utc_now():
        raise DomainError("preview_expired", 409)
    draft_revision, preview, routes = await validate_preview_for_confirmation(
        session, principal, draft=draft, revision=revision, preview_id=approval.preview_id,
    )
    if approval.preview_hash != preview.preview_hash or approval.expires_at > preview.expires_at:
        raise DomainError("confirmation_required", 409)
    return approval, draft_revision, preview, routes
