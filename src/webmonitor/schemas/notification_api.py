"""Resource and request contracts for the notification API."""
from datetime import datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from webmonitor.schemas.notifications import (
    Contract, EventType, NotificationGroupSpec, NotificationGroupMemberSpec, TemplateBinding,
)


class Resource(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None


class GroupPatch(Contract):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    enabled: StrictBool | None = None
    members: list[NotificationGroupMemberSpec] | None = None

    @model_validator(mode="after")
    def validate_patch(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("explicit null is not a group update")
        if self.members is not None:
            NotificationGroupSpec(name=self.name or "group", members=self.members)
        return self


class GroupMember(Resource):
    id: UUID
    email: str
    active: bool


class Group(Resource):
    id: UUID
    name: str
    enabled: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
    members: list[GroupMember]


class Template(Resource):
    id: UUID
    event_type: EventType
    version: int
    name: str
    is_default: bool
    created_at: datetime


class RenderedEmail(BaseModel):
    subject: str
    html: str
    text: str
    preview_html: str


class Delivery(Resource):
    id: UUID
    event_id: UUID
    recipient: str
    channel: Literal["email"]
    template_id: UUID
    template_version: int
    payload: dict[str, Any]
    recipient_sources: list[dict[str, Any]]
    status: Literal["queued", "sending", "retrying", "sent", "failed", "cancelled"]
    message_id: str
    attempt_count: int
    rendered_subject: str | None
    rendered_html: str | None
    rendered_text: str | None
    last_attempt_at: datetime | None
    sent_at: datetime | None
    failed_at: datetime | None
    cancelled_at: datetime | None
    smtp_response_code: int | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class RouteSelection(Contract):
    notification_group_ids: list[UUID] = Field(min_length=1)
    template_bindings: dict[EventType, TemplateBinding]
    base_version_id: UUID


class RoutePreviewRequest(RouteSelection):
    draft_id: UUID | None = None


class RouteConfirmRequest(Contract):
    draft_id: UUID
    revision: int = Field(gt=0, strict=True)
    preview_id: UUID
    base_version_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=200)


class RouteView(RouteSelection):
    monitor_id: UUID
    version: int


class RoutePreviewJob(BaseModel):
    monitor_id: UUID
    base_version_id: UUID
    conversation_id: UUID
    draft_id: UUID
    revision: int
    collection_job_id: UUID
    status: str


class RoutePreview(Resource):
    id: UUID
    draft_id: UUID
    revision: int
    preview_hash: str
    extracted_data: dict[str, Any]
    coverage: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    rule_simulation: dict[str, Any]
    rendered_emails: list[dict[str, Any]]
    recipient_snapshot: list[dict[str, Any]]
    template_snapshot: list[dict[str, Any]]
    warnings: list[str]
    expires_at: datetime


class RouteUpdate(BaseModel):
    monitor_id: UUID
    monitor_version_id: UUID
    version: int
    status: str
    baseline_snapshot_id: UUID | None


class RoutePreviewFinish(Contract):
    draft_id: UUID
    revision: int = Field(gt=0, strict=True)


class PreviewJobStatus(BaseModel):
    id: UUID
    status: str
    error_code: str | None
