"""Identity, initialization and process coordination records."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from webmonitor.db.base import Base, Timestamped, UUIDPrimaryKey, WorkspaceScoped, member_fk, utc_now, workspace_identity


class Workspace(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)


class SchemaMetadata(UUIDPrimaryKey, Base):
    __tablename__ = "schema_metadata"
    __table_args__ = (
        CheckConstraint("key = 'application'", name="application_key"),
        CheckConstraint("length(fingerprint) = 64", name="fingerprint_length"),
    )
    key: Mapped[str] = mapped_column(String(40), unique=True, default="application")
    fingerprint: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    initialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class User(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="normalized_email"),
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Membership(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        workspace_identity(),
        UniqueConstraint("workspace_id", "user_id"),
        CheckConstraint("role IN ('admin', 'member')", name="role"),
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    role: Mapped[str] = mapped_column(String(16), default="member")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Session(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "sessions"
    __table_args__ = (
        workspace_identity(), member_fk("user_id"),
        CheckConstraint("length(token_hash) = 64", name="token_hash_length"),
        Index("ix_sessions_live_user", "workspace_id", "user_id", "expires_at", postgresql_where=text("revoked_at IS NULL")),
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "invitations"
    __table_args__ = (
        workspace_identity(), member_fk("created_by_user_id"), member_fk("consumed_by_user_id"),
        CheckConstraint("email = lower(btrim(email)) AND email <> ''", name="normalized_email"),
        CheckConstraint("length(token_hash) = 64", name="token_hash_length"),
        Index("ix_invitations_pending_email", "workspace_id", "email", postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL")),
    )
    email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    consumed_by_user_id: Mapped[UUID | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        workspace_identity(), member_fk("actor_user_id"),
        Index("ix_audit_logs_workspace_created", "workspace_id", "created_at", "id"),
    )
    actor_user_id: Mapped[UUID | None] = mapped_column()
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(80))
    resource_id: Mapped[UUID | None] = mapped_column()
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class LoginRateLimit(UUIDPrimaryKey, Base):
    """One persistent fixed-window counter per email/IP dimension."""
    __tablename__ = "login_rate_limits"
    __table_args__ = (
        UniqueConstraint("dimension", "key_hash", "window_started_at"),
        CheckConstraint("dimension IN ('email', 'ip')", name="dimension"),
        CheckConstraint("attempt_count >= 0", name="attempt_count"),
        Index("ix_login_rate_limits_expiry", "expires_at"),
    )
    dimension: Mapped[str] = mapped_column(String(8))
    key_hash: Mapped[str] = mapped_column(String(64))
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)


class WorkerHeartbeat(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "worker_heartbeats"
    __table_args__ = (
        workspace_identity(), UniqueConstraint("workspace_id", "worker_id"),
        CheckConstraint("kind IN ('agent', 'scheduler', 'http', 'browser', 'mailer')", name="kind"),
    )
    worker_id: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now(), index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
