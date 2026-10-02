"""Versioned monitoring, fenced execution and durable collection evidence."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from webmonitor.db.base import Base, Timestamped, UUIDPrimaryKey, WorkspaceScoped, member_fk, scoped_fk, utc_now, workspace_identity


class Monitor(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "monitors"
    __table_args__ = (
        workspace_identity(), member_fk("created_by_user_id"), scoped_fk("draft_id", "drafts"),
        UniqueConstraint("draft_id"),
        ForeignKeyConstraint(
            ["workspace_id", "id", "current_version_id"],
            ["monitor_versions.workspace_id", "monitor_versions.monitor_id", "monitor_versions.id"],
            use_alter=True, name="fk_monitors_current_version", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "id", "baseline_snapshot_id"],
            ["snapshots.workspace_id", "snapshots.monitor_id", "snapshots.id"],
            use_alter=True, name="fk_monitors_baseline_snapshot", ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'paused', 'deleted')", name="status"),
        CheckConstraint("baseline_status IN ('pending_first_success', 'ready')", name="baseline_status"),
        CheckConstraint("health_status IN ('healthy', 'failed')", name="health_status"),
        Index("ix_monitors_due", "next_run_at", "id", postgresql_where=text("status = 'active'")),
        Index("ix_monitors_workspace_created", "workspace_id", "created_at", "id"),
    )
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    draft_id: Mapped[UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    current_version_id: Mapped[UUID | None] = mapped_column()
    baseline_snapshot_id: Mapped[UUID | None] = mapped_column()
    baseline_status: Mapped[str] = mapped_column(String(32), default="pending_first_success", server_default="pending_first_success")
    rule_state: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    health_status: Mapped[str] = mapped_column(String(16), default="healthy", server_default="healthy")
    failure_fingerprint: Mapped[str | None] = mapped_column(String(64))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MonitorVersion(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "monitor_versions"
    __table_args__ = (
        workspace_identity(), scoped_fk("monitor_id", "monitors"), member_fk("created_by_user_id"), scoped_fk("approval_id", "approvals"),
        UniqueConstraint("workspace_id", "monitor_id", "version"),
        UniqueConstraint("workspace_id", "monitor_id", "id"),
        CheckConstraint("version > 0", name="version"),
        CheckConstraint("length(spec_hash) = 64", name="spec_hash_length"),
        CheckConstraint("collection_mode IN ('http', 'browser')", name="collection_mode"),
        CheckConstraint("interval_seconds BETWEEN 60 AND 86400", name="interval_seconds"),
    )
    monitor_id: Mapped[UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer)
    created_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    approval_id: Mapped[UUID] = mapped_column(nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spec_hash: Mapped[str] = mapped_column(String(64))
    collection_mode: Mapped[str] = mapped_column(String(16))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class Run(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    __tablename__ = "runs"
    __table_args__ = (
        workspace_identity(), scoped_fk("monitor_id", "monitors"), member_fk("requested_by_user_id"),
        ForeignKeyConstraint(
            ["workspace_id", "monitor_id", "monitor_version_id"],
            ["monitor_versions.workspace_id", "monitor_versions.monitor_id", "monitor_versions.id"], ondelete="RESTRICT",
        ),
        scoped_fk("baseline_snapshot_id", "snapshots", use_alter=True, name="fk_runs_baseline_snapshot"),
        UniqueConstraint("monitor_id", "scheduled_at"),
        UniqueConstraint("workspace_id", "monitor_id", "id"),
        CheckConstraint("trigger IN ('scheduled', 'manual')", name="trigger"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'discarded')", name="status"),
        CheckConstraint("attempt_generation >= 0 AND attempt_count BETWEEN 0 AND 3", name="attempt_limits"),
        Index("uq_runs_active_monitor", "monitor_id", unique=True, postgresql_where=text("status IN ('queued', 'running')")),
        Index("ix_runs_claimable", "status", "available_at", postgresql_where=text("status IN ('queued', 'running')")),
        Index("ix_runs_monitor_created", "workspace_id", "monitor_id", "created_at", "id"),
    )
    monitor_id: Mapped[UUID] = mapped_column(nullable=False)
    monitor_version_id: Mapped[UUID] = mapped_column(nullable=False)
    baseline_snapshot_id: Mapped[UUID | None] = mapped_column()
    requested_by_user_id: Mapped[UUID | None] = mapped_column()
    trigger: Mapped[str] = mapped_column(String(16), default="scheduled")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_generation: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)


class Attempt(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "attempts"
    __table_args__ = (
        workspace_identity(), scoped_fk("run_id", "runs"),
        UniqueConstraint("workspace_id", "run_id", "attempt_number"),
        UniqueConstraint("workspace_id", "run_id", "generation"),
        CheckConstraint("attempt_number BETWEEN 1 AND 3 AND generation > 0", name="attempt_generation"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed', 'cancelled', 'discarded')", name="status"),
    )
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class Snapshot(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        workspace_identity(), scoped_fk("monitor_id", "monitors"), scoped_fk("monitor_version_id", "monitor_versions"),
        ForeignKeyConstraint(["workspace_id", "monitor_id", "run_id"], ["runs.workspace_id", "runs.monitor_id", "runs.id"], ondelete="RESTRICT"),
        UniqueConstraint("workspace_id", "monitor_id", "id"),
        UniqueConstraint("run_id"),
    )
    monitor_id: Mapped[UUID] = mapped_column(nullable=False)
    monitor_version_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    final_url: Mapped[str] = mapped_column(Text)
    extracted_data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rule_state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_refs: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())


class Event(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "events"
    __table_args__ = (
        workspace_identity(), scoped_fk("monitor_id", "monitors"), scoped_fk("monitor_version_id", "monitor_versions"), scoped_fk("snapshot_id", "snapshots"), member_fk("acknowledged_by_user_id"),
        ForeignKeyConstraint(["workspace_id", "monitor_id", "run_id"], ["runs.workspace_id", "runs.monitor_id", "runs.id"], ondelete="RESTRICT"),
        UniqueConstraint("workspace_id", "run_id", "type"),
        CheckConstraint("type IN ('price_changed', 'list_changed', 'content_changed', 'run_failed', 'recovered')", name="type"),
        Index("uq_events_business_run", "run_id", unique=True, postgresql_where=text("type IN ('price_changed', 'list_changed', 'content_changed')")),
        Index("ix_events_workspace_created", "workspace_id", "created_at", "id"),
    )
    monitor_id: Mapped[UUID] = mapped_column(nullable=False)
    monitor_version_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID] = mapped_column(nullable=False)
    snapshot_id: Mapped[UUID | None] = mapped_column()
    type: Mapped[str] = mapped_column(String(32))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
    change: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    failure_fingerprint: Mapped[str | None] = mapped_column(String(64))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by_user_id: Mapped[UUID | None] = mapped_column()


class CollectionJob(UUIDPrimaryKey, Timestamped, WorkspaceScoped, Base):
    """Analysis/preview jobs cannot acquire or commit production baselines."""
    __tablename__ = "collection_jobs"
    __table_args__ = (
        workspace_identity(),
        scoped_fk("run_id", "runs", use_alter=True, name="fk_collection_jobs_run"),
        scoped_fk("attempt_id", "attempts", use_alter=True, name="fk_collection_jobs_attempt"),
        scoped_fk("agent_run_id", "agent_runs"), member_fk("requested_by_user_id"),
        ForeignKeyConstraint(["workspace_id", "draft_id", "revision"], ["draft_revisions.workspace_id", "draft_revisions.draft_id", "draft_revisions.revision"], ondelete="RESTRICT"),
        CheckConstraint("kind IN ('analyze', 'preview', 'production')", name="kind"),
        CheckConstraint("collection_mode IN ('http', 'browser')", name="collection_mode"),
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'discarded')", name="status"),
        CheckConstraint("generation >= 0", name="generation"),
        CheckConstraint("(kind = 'production' AND run_id IS NOT NULL AND attempt_id IS NOT NULL) OR (kind <> 'production' AND run_id IS NULL AND attempt_id IS NULL)", name="production_owner"),
        CheckConstraint("(draft_id IS NULL) = (revision IS NULL)", name="draft_revision_pair"),
        CheckConstraint("kind <> 'preview' OR draft_id IS NOT NULL", name="preview_revision"),
        Index("uq_collection_jobs_active_run", "run_id", unique=True, postgresql_where=text("status IN ('queued', 'running') AND kind = 'production'")),
        Index("uq_collection_jobs_active_preview", "workspace_id", "draft_id", "revision", unique=True, postgresql_where=text("status IN ('queued', 'running') AND kind = 'preview'")),
        Index("ix_collection_jobs_claimable", "collection_mode", "status", "available_at", postgresql_where=text("status IN ('queued', 'running')")),
    )
    kind: Mapped[str] = mapped_column(String(16))
    collection_mode: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    run_id: Mapped[UUID | None] = mapped_column()
    attempt_id: Mapped[UUID | None] = mapped_column()
    agent_run_id: Mapped[UUID | None] = mapped_column()
    requested_by_user_id: Mapped[UUID | None] = mapped_column()
    draft_id: Mapped[UUID | None] = mapped_column()
    revision: Mapped[int | None] = mapped_column(Integer)
    url: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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


class Evidence(UUIDPrimaryKey, WorkspaceScoped, Base):
    __tablename__ = "evidence"
    __table_args__ = (
        workspace_identity(), scoped_fk("collection_job_id", "collection_jobs"), scoped_fk("run_id", "runs"), scoped_fk("attempt_id", "attempts"), scoped_fk("snapshot_id", "snapshots"), scoped_fk("preview_id", "previews"),
        UniqueConstraint("bucket", "object_key"),
        CheckConstraint("kind IN ('html', 'screenshot', 'response')", name="kind"),
        CheckConstraint("size_bytes >= 0 AND length(sha256) = 64", name="size_hash"),
        Index("ix_evidence_run", "workspace_id", "run_id"),
    )
    collection_job_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[UUID | None] = mapped_column()
    attempt_id: Mapped[UUID | None] = mapped_column()
    snapshot_id: Mapped[UUID | None] = mapped_column()
    preview_id: Mapped[UUID | None] = mapped_column()
    kind: Mapped[str] = mapped_column(String(16))
    bucket: Mapped[str] = mapped_column(String(200))
    object_key: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, server_default=func.now())
