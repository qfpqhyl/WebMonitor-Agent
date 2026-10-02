"""Public conversation contracts; private SDK state and approval tokens stay server-side."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from webmonitor.schemas.monitors import CreateMonitorResult

RunStatus = Literal["queued", "running", "waiting_approval", "completed", "failed", "cancelled"]
EventType = Literal["message.delta", "message.completed", "tool.started", "tool.completed", "tool.failed", "approval.required", "run.completed", "run.failed", "run.cancelled"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ConversationCreate(Contract):
    title: str | None = Field(default=None, max_length=200)


class MessageRequest(Contract):
    content: str = Field(min_length=1, max_length=16000)
    client_message_id: UUID

    @field_validator("content")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must contain text")
        return value


class ConfirmRequest(Contract):
    draft_id: UUID
    revision: StrictInt = Field(gt=0)
    preview_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class CreateRequest(Contract):
    draft_id: UUID
    confirmed_revision: StrictInt = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=200)


class CancelRequest(Contract):
    agent_run_id: UUID


class PendingApprovalView(Contract):
    draft_id: UUID
    confirmed_revision: int
    idempotency_key: str
    tool_call_id: str
    preview_id: UUID | None


class RunView(Contract):
    id: UUID
    status: RunStatus
    client_message_id: UUID
    created_at: datetime
    finished_at: datetime | None
    error_code: str | None
    error_message: str | None
    result: dict[str, Any] | None
    pending_approval: PendingApprovalView | None = None
    checkpoint_error: str | None = None


class MessageView(Contract):
    id: UUID
    agent_run_id: UUID | None
    client_message_id: UUID | None
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime


class PreviewView(Contract):
    id: UUID
    revision: int
    spec_hash: str
    preview_hash: str
    extracted_data: dict[str, Any]
    coverage: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    rule_simulation: dict[str, Any]
    rendered_emails: list[dict[str, Any]]
    recipient_snapshot: list[dict[str, Any]]
    template_snapshot: list[dict[str, Any]]
    warnings: list[str]
    created_at: datetime
    expires_at: datetime
    invalidated_at: datetime | None


class ApprovalView(Contract):
    id: UUID
    revision: int
    preview_id: UUID
    agent_run_id: UUID | None
    tool_call_id: str | None
    expires_at: datetime
    consumed_at: datetime | None
    invalidated_at: datetime | None


class DraftView(Contract):
    id: UUID
    status: str
    revision: int | None
    spec: dict[str, Any] | None
    spec_hash: str | None
    preview: PreviewView | None
    approval: ApprovalView | None
    task_id: UUID | None


class ConversationSnapshot(Contract):
    id: UUID
    title: str | None
    created_at: datetime
    last_seq: int
    messages: list[MessageView]
    runs: list[RunView]
    drafts: list[DraftView]


class RunResponse(Contract):
    agent_run_id: UUID
    status: RunStatus
    task_id: UUID | None = None
    result: CreateMonitorResult | None = None


class EventEnvelope(Contract):
    schema_version: Literal[1] = 1
    event_id: UUID
    seq: int
    conversation_id: UUID
    agent_run_id: UUID
    type: EventType
    payload: dict[str, Any]
    tool_call_id: str | None = None
