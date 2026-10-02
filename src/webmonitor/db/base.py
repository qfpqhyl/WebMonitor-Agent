"""Shared PostgreSQL model foundations.

Timestamps are timezone-aware; application defaults and database defaults both use
UTC instants. Workspace references are composite so tenant isolation is a database
invariant, not merely a query convention.
"""

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, MetaData, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class UUIDPrimaryKey:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now(), onupdate=utc_now
    )


class WorkspaceScoped:
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="RESTRICT"), nullable=False, index=True
    )


def workspace_identity() -> UniqueConstraint:
    """Candidate key used by all references to workspace-owned rows."""
    return UniqueConstraint("workspace_id", "id")


def scoped_fk(
    column: str, table: str, *, use_alter: bool = False, name: str | None = None
) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", column],
        [f"{table}.workspace_id", f"{table}.id"],
        ondelete="RESTRICT",
        use_alter=use_alter,
        name=name,
    )


def member_fk(column: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["workspace_id", column],
        ["memberships.workspace_id", "memberships.user_id"],
        ondelete="RESTRICT",
    )
