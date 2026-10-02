"""Global built-in templates and workspace-owned notification delivery records."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from webmonitor.db.base import Base, Timestamped, UUIDPrimaryKey, WorkspaceScoped, member_fk, scoped_fk, utc_now, workspace_identity


class EmailTemplate(UUIDPrimaryKey, Base):
    """Immutable global catalog; only server-owned packaged templates are permitted."""
    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("event_type", "version"),
        UniqueConstraint("id", "event_type"),
        UniqueConstraint("id", "version"),
        CheckConstraint("event_type IN ('price_changed', 'list_changed', 'content_changed', 'run_failed', 'recovered')", name="event_type"),
        CheckConstraint("version > 0", name="version"),
        Index("uq_email_templates_default_event", "event_type", unique=True, postgresql_where=text("is_default = true")),
    )
    event_type: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(200))
    subject_template: Mapped[str] = mapped_column(Text)
    html_template: Mapped[str] = mapped_column(Text)
    text_template: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class NotificationGroup(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "notification_groups"
    __table_args__ = (
        workspace_identity(), member_fk("created_by_user_id"),
        Index("uq_notification_groups_enabled_name", "workspace_id", "name", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )
    name: Mapped[str] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationGroupMember(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "notification_group_members"
    __table_args__ = (
        workspace_identity(), scoped_fk("group_id", "notification_groups"),
        UniqueConstraint("workspace_id", "group_id", "email"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="normalized_email"),
    )
    group_id: Mapped[UUID] = mapped_column(nullable=False)
    email: Mapped[str] = mapped_column(String(320))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class MonitorNotificationRoute(UUIDPrimaryKey, WorkspaceScoped, Base):
    """Immutable route selection belongs to a specific monitor version."""
    __tablename__ = "monitor_notification_routes"
    __table_args__ = (
        workspace_identity(), scoped_fk("monitor_version_id", "monitor_versions"), scoped_fk("group_id", "notification_groups"),
        UniqueConstraint("workspace_id", "monitor_version_id", "group_id", "event_type"),
        ForeignKeyConstraint(["template_id", "event_type"], ["email_templates.id", "email_templates.event_type"], ondelete="RESTRICT"),
        CheckConstraint("event_type IN ('price_changed', 'list_changed', 'content_changed', 'run_failed', 'recovered')", name="event_type"),
    )
    monitor_version_id: Mapped[UUID] = mapped_column(nullable=False)
    group_id: Mapped[UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(32))
    template_id: Mapped[UUID] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class EmailDelivery(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "email_deliveries"
    __table_args__ = (
        workspace_identity(), scoped_fk("event_id", "events"),
        UniqueConstraint("event_id", "recipient", "channel"),
        UniqueConstraint("workspace_id", "event_id", "id"),
        ForeignKeyConstraint(["template_id", "template_version"], ["email_templates.id", "email_templates.version"], ondelete="RESTRICT"),
        CheckConstraint("channel = 'email'", name="channel"),
        CheckConstraint("recipient = lower(btrim(recipient)) AND recipient <> ''", name="normalized_recipient"),
        CheckConstraint("status IN ('queued', 'sending', 'retrying', 'sent', 'failed', 'cancelled')", name="status"),
        CheckConstraint("attempt_count BETWEEN 0 AND 3", name="attempt_count"),
        Index("ix_email_deliveries_workspace_created", "workspace_id", "created_at", "id"),
    )
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    recipient: Mapped[str] = mapped_column(String(320))
    channel: Mapped[str] = mapped_column(String(16), default="email", server_default="email")
    template_id: Mapped[UUID] = mapped_column(nullable=False)
    template_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    recipient_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    message_id: Mapped[str] = mapped_column(String(320), unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rendered_subject: Mapped[str | None] = mapped_column(Text)
    rendered_html: Mapped[str | None] = mapped_column(Text)
    rendered_text: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    smtp_response_code: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)


class Outbox(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    """One leased send job per deduplicated email delivery, committed with its event."""
    __tablename__ = "outbox"
    __table_args__ = (
        workspace_identity(), scoped_fk("event_id", "events"),
        ForeignKeyConstraint(["workspace_id", "event_id", "delivery_id"], ["email_deliveries.workspace_id", "email_deliveries.event_id", "email_deliveries.id"], ondelete="RESTRICT"),
        UniqueConstraint("delivery_id"),
        CheckConstraint("status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')", name="status"),
        CheckConstraint("generation >= 0", name="generation"),
        Index("ix_outbox_claimable", "available_at", "id", postgresql_where=text("status IN ('pending', 'processing')")),
    )
    event_id: Mapped[UUID] = mapped_column(nullable=False)
    delivery_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
