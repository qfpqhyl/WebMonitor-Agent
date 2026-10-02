"""Strict public notification contracts; templates are server-owned versions."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StrictBool, field_validator

EventType = Literal['price_changed', 'list_changed', 'content_changed', 'run_failed', 'recovered']
EVENT_TYPES = frozenset(('price_changed', 'list_changed', 'content_changed', 'run_failed', 'recovered'))


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TemplateBinding(Contract):
    template_id: UUID
    version: int = Field(gt=0, strict=True)


class NotificationGroupMemberSpec(Contract):
    email: EmailStr
    active: StrictBool = True

    @field_validator('email', mode='before')
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        return value.strip().casefold() if isinstance(value, str) else value


class NotificationGroupSpec(Contract):
    name: str = Field(min_length=1, max_length=200)
    enabled: StrictBool = True
    members: list[NotificationGroupMemberSpec]

    @field_validator('members')
    @classmethod
    def unique_members(cls, value: list[NotificationGroupMemberSpec]) -> list[NotificationGroupMemberSpec]:
        if len({str(member.email) for member in value}) != len(value):
            raise ValueError('duplicate recipient email')
        return value


class PayloadMonitor(Contract):
    id: UUID
    name: str
    url: str


class PayloadEvent(Contract):
    id: UUID
    type: EventType
    detected_at: datetime


class PayloadChange(Contract):
    before: dict[str, Any] | None
    after: dict[str, Any]
    summary: str


class NotificationPayload(Contract):
    monitor: PayloadMonitor
    event: PayloadEvent
    change: PayloadChange
    fields: dict[str, Any]
    evidence_url: str
