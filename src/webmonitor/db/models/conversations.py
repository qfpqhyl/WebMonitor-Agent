"""Durable conversation execution and version-bound user approvals."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from webmonitor.db.base import Base, Timestamped, UUIDPrimaryKey, WorkspaceScoped, member_fk, scoped_fk, utc_now, workspace_identity


def revision_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", "draft_id", "revision"],
        ["draft_revisions.workspace_id", "draft_revisions.draft_id", "draft_revisions.revision"],
        ondelete="RESTRICT",
    )


class Conversation(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "conversations"
    __table_args__ = (workspace_identity(), member_fk("user_id"), CheckConstraint("last_seq >= 0", name="last_seq"))
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    session_lease_owner: Mapped[str | None] = mapped_column(String(200))
    session_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Message(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "messages"
    __table_args__ = (
        workspace_identity(), scoped_fk("conversation_id", "conversations"), scoped_fk("agent_run_id", "agent_runs"), member_fk("user_id"),
        UniqueConstraint("workspace_id", "conversation_id", "client_message_id"),
        CheckConstraint("role IN ('user', 'assistant', 'system')", name="role"),
        Index("ix_messages_conversation_created", "workspace_id", "conversation_id", "created_at", "id"),
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_run_id: Mapped[UUID | None] = mapped_column()
    user_id: Mapped[UUID | None] = mapped_column()
    client_message_id: Mapped[UUID | None] = mapped_column()
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class AgentRun(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        workspace_identity(), scoped_fk("conversation_id", "conversations"), member_fk("user_id"),
        CheckConstraint("status IN ('queued', 'running', 'waiting_approval', 'completed', 'failed', 'cancelled')", name="status"),
        CheckConstraint("generation >= 0", name="generation"),
        Index("uq_agent_runs_active_user", "workspace_id", "user_id", unique=True, postgresql_where=text("status IN ('queued', 'running', 'waiting_approval')")),
        Index("uq_agent_runs_active_conversation", "workspace_id", "conversation_id", unique=True, postgresql_where=text("status IN ('queued', 'running', 'waiting_approval')")),
        Index("ix_agent_runs_claimable", "status", "available_at", postgresql_where=text("status IN ('queued', 'running')")),
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    client_message_id: Mapped[UUID] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AgentCheckpoint(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        workspace_identity(), scoped_fk("agent_run_id", "agent_runs"),
        UniqueConstraint("workspace_id", "agent_run_id", "sequence"),
        CheckConstraint("sequence > 0 AND generation >= 0", name="sequence_generation"),
    )
    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    sdk_version: Mapped[str] = mapped_column(String(40))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    state_json: Mapped[str] = mapped_column(Text)
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class AgentSessionItem(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "agent_session_items"
    __table_args__ = (
        workspace_identity(), scoped_fk("conversation_id", "conversations"),
        UniqueConstraint("workspace_id", "conversation_id", "sequence"),
        UniqueConstraint("workspace_id", "conversation_id", "item_id"),
        CheckConstraint("sequence > 0", name="sequence"),
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger)
    item_id: Mapped[str] = mapped_column(String(200))
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    item: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class ConversationEvent(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "conversation_events"
    __table_args__ = (
        workspace_identity(), scoped_fk("conversation_id", "conversations"), scoped_fk("agent_run_id", "agent_runs"),
        UniqueConstraint("workspace_id", "conversation_id", "seq"),
        CheckConstraint("seq > 0 AND schema_version = 1", name="sequence_schema"),
        CheckConstraint("type IN ('message.delta', 'message.completed', 'tool.started', 'tool.completed', 'tool.failed', 'approval.required', 'run.completed', 'run.failed', 'run.cancelled')", name="type"),
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_run_id: Mapped[UUID] = mapped_column(nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    type: Mapped[str] = mapped_column(String(40))
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class Draft(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "drafts"
    __table_args__ = (
        workspace_identity(), scoped_fk("conversation_id", "conversations"), member_fk("user_id"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "current_revision"],
            ["draft_revisions.workspace_id", "draft_revisions.draft_id", "draft_revisions.revision"],
            use_alter=True, name="fk_drafts_current_revision", ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('draft', 'confirmed', 'published', 'discarded')", name="status"),
        CheckConstraint("current_revision IS NULL OR current_revision > 0", name="current_revision"),
    )
    conversation_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    current_revision: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")


class DraftRevision(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "draft_revisions"
    __table_args__ = (
        workspace_identity(), scoped_fk("draft_id", "drafts"), member_fk("created_by_user_id"),
        UniqueConstraint("workspace_id", "draft_id", "revision"),
        CheckConstraint("revision > 0", name="revision"),
        CheckConstraint("length(spec_hash) = 64", name="spec_hash_length"),
    )
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class Preview(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "previews"
    __table_args__ = (
        workspace_identity(), revision_fk(), scoped_fk("collection_job_id", "collection_jobs"), member_fk("user_id"),
        UniqueConstraint("workspace_id", "draft_id", "revision", "id"),
        CheckConstraint("length(spec_hash) = 64 AND length(preview_hash) = 64", name="hash_lengths"),
        Index("ix_previews_revision", "workspace_id", "draft_id", "revision", "created_at"),
    )
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer)
    collection_job_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    spec_hash: Mapped[str] = mapped_column(String(64))
    preview_hash: Mapped[str] = mapped_column(String(64))
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    rule_simulation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rendered_emails: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    recipient_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    template_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Approval(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "approvals"
    __table_args__ = (
        workspace_identity(), revision_fk(), member_fk("user_id"), scoped_fk("agent_run_id", "agent_runs"),
        ForeignKeyConstraint(
            ["workspace_id", "draft_id", "revision", "preview_id"],
            ["previews.workspace_id", "previews.draft_id", "previews.revision", "previews.id"], ondelete="RESTRICT",
        ),
        CheckConstraint("length(preview_hash) = 64", name="preview_hash_length"),
        Index("uq_approvals_unconsumed_draft", "workspace_id", "draft_id", unique=True, postgresql_where=text("consumed_at IS NULL AND invalidated_at IS NULL")),
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer)
    preview_id: Mapped[UUID] = mapped_column(nullable=False)
    preview_hash: Mapped[str] = mapped_column(String(64))
    agent_run_id: Mapped[UUID | None] = mapped_column()
    tool_call_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdempotencyRecord(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        workspace_identity(), member_fk("user_id"), scoped_fk("draft_id", "drafts"), scoped_fk("task_id", "monitors"),
        UniqueConstraint("workspace_id", "user_id", "idempotency_key"),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
    )
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    confirmed_revision: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[UUID] = mapped_column(nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
