"""Durable process liveness, separate from collection leases."""
import os
import socket
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import WorkerHeartbeat, Workspace


def make_worker_id(kind: str) -> str:
    return f"{kind}:{socket.gethostname()}:{os.getpid()}:{uuid4()}"[:200]


async def record_heartbeat(session, kind: str, worker_id: str, *, details: dict | None = None) -> None:
    """Flush-only PostgreSQL liveness for every initialized workspace."""
    now = utc_now()
    workspace_ids = (await session.scalars(select(Workspace.id))).all()
    for workspace_id in workspace_ids:
        await session.execute(insert(WorkerHeartbeat).values(
            id=uuid4(), workspace_id=workspace_id, worker_id=worker_id,
            kind=kind, started_at=now, heartbeat_at=now, details=details or {},
        ).on_conflict_do_update(index_elements=["workspace_id", "worker_id"],
            set_={"heartbeat_at": now, "stopped_at": None, "details": details or {}}))
