"""Workspace monitoring reads and mutations through fenced run services."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.dependencies import current_principal
from webmonitor.api.errors import DomainError
from webmonitor.api.pagination import paginate
from webmonitor.db.base import utc_now
from webmonitor.db.models.monitoring import Attempt, Evidence, Event, Monitor, MonitorVersion, Run, Snapshot
from webmonitor.db.session import session_dependency
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitor_api import (
    AttemptView, EventView, EvidenceView, MonitorDetail, MonitorView, Page,
    RunDetail, RunView, SnapshotView,
)
from webmonitor.security.authorization import require_owner_or_admin, require_workspace
from webmonitor.services import runs

router = APIRouter(prefix="/api/v1", tags=["monitoring"])


async def _read_monitor(session: AsyncSession, principal: Principal, monitor_id: UUID) -> Monitor:
    monitor = require_workspace(principal, await session.get(Monitor, monitor_id))
    if monitor.status == "deleted":
        raise DomainError("not_found", 404)
    return monitor


async def _current_version(session: AsyncSession, monitor: Monitor) -> MonitorVersion:
    version = await session.scalar(select(MonitorVersion).where(
        MonitorVersion.id == monitor.current_version_id,
        MonitorVersion.monitor_id == monitor.id,
        MonitorVersion.workspace_id == monitor.workspace_id,
    ))
    if version is None:
        raise DomainError("monitor_version_unavailable", 503)
    return version


def _monitor_view(monitor: Monitor, version: MonitorVersion) -> MonitorView:
    values = {field: getattr(monitor, field) for field in MonitorView.model_fields if field != "collection_mode"}
    return MonitorView(**values, collection_mode=version.collection_mode)


@router.get("/monitors", response_model=Page[MonitorView])
async def list_monitors(cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    monitors, next_cursor = await paginate(session, select(Monitor).where(
        Monitor.workspace_id == principal.workspace_id, Monitor.status != "deleted"), Monitor, cursor, limit)
    version_ids = [monitor.current_version_id for monitor in monitors]
    versions = {version.id: version for version in (await session.scalars(select(MonitorVersion).where(
        MonitorVersion.workspace_id == principal.workspace_id, MonitorVersion.id.in_(version_ids)))).all()}
    items = []
    for monitor in monitors:
        version = versions.get(monitor.current_version_id)
        if version is None or version.monitor_id != monitor.id:
            raise DomainError("monitor_version_unavailable", 503)
        items.append(_monitor_view(monitor, version))
    return Page[MonitorView](items=items, next_cursor=next_cursor)


@router.get("/monitors/{monitor_id}", response_model=MonitorDetail)
async def read_monitor(monitor_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    monitor = await _read_monitor(session, principal, monitor_id)
    version = await _current_version(session, monitor)
    baseline = None
    if monitor.baseline_snapshot_id is not None:
        baseline = await session.scalar(select(Snapshot).where(
            Snapshot.id == monitor.baseline_snapshot_id, Snapshot.monitor_id == monitor.id,
            Snapshot.workspace_id == principal.workspace_id))
        if baseline is None:
            raise DomainError("baseline_unavailable", 503)
    return MonitorDetail(**_monitor_view(monitor, version).model_dump(), spec=version.spec,
        version=version.version, baseline=SnapshotView.model_validate(baseline) if baseline is not None else None)


@router.get("/monitors/{monitor_id}/runs", response_model=Page[RunView])
async def list_monitor_runs(monitor_id: UUID, cursor: str | None = None,
    limit: int = Query(25, ge=1, le=100), principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    await _read_monitor(session, principal, monitor_id)
    rows, next_cursor = await paginate(session, select(Run).where(
        Run.workspace_id == principal.workspace_id, Run.monitor_id == monitor_id), Run, cursor, limit)
    return Page[RunView](items=[RunView.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def read_run(run_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    run = require_workspace(principal, await session.get(Run, run_id))
    attempts = (await session.scalars(select(Attempt).where(
        Attempt.run_id == run.id, Attempt.workspace_id == principal.workspace_id)
        .order_by(Attempt.attempt_number))).all()
    snapshot = await session.scalar(select(Snapshot).where(
        Snapshot.run_id == run.id, Snapshot.workspace_id == principal.workspace_id))
    evidence = (await session.scalars(select(Evidence).where(
        Evidence.run_id == run.id, Evidence.workspace_id == principal.workspace_id)
        .order_by(Evidence.created_at, Evidence.id))).all()
    return RunDetail(**RunView.model_validate(run).model_dump(),
        attempts=[AttemptView.model_validate(attempt) for attempt in attempts],
        snapshot=SnapshotView.model_validate(snapshot) if snapshot is not None else None,
        evidence=[EvidenceView.model_validate(item) for item in evidence])


@router.post("/monitors/{monitor_id}/pause", response_model=MonitorView)
async def pause_monitor(monitor_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    monitor = await runs.pause_monitor(session, principal, monitor_id)
    result = _monitor_view(monitor, await _current_version(session, monitor))
    await session.commit()
    return result


@router.post("/monitors/{monitor_id}/resume", response_model=MonitorView)
async def resume_monitor(monitor_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    monitor = await runs.resume_monitor(session, principal, monitor_id)
    result = _monitor_view(monitor, await _current_version(session, monitor))
    await session.commit()
    return result


@router.post("/monitors/{monitor_id}/runs", response_model=RunView, status_code=202)
async def start_manual_run(monitor_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    run = await runs.enqueue_manual(session, principal, monitor_id)
    result = RunView.model_validate(run)
    await session.commit()
    return result


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(run_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    run = await runs.cancel_run(session, principal, run_id)
    result = RunView.model_validate(run)
    await session.commit()
    return result


@router.get("/events", response_model=Page[EventView])
async def list_events(cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    rows, next_cursor = await paginate(session, select(Event).where(
        Event.workspace_id == principal.workspace_id), Event, cursor, limit)
    return Page[EventView](items=[EventView.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.post("/events/{event_id}/acknowledge", response_model=EventView)
async def acknowledge_event(event_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    event = require_workspace(principal, await session.scalar(select(Event).where(
        Event.id == event_id, Event.workspace_id == principal.workspace_id)
        .with_for_update().execution_options(populate_existing=True)))
    require_owner_or_admin(principal, await session.get(Monitor, event.monitor_id))
    if event.acknowledged_at is None:
        event.acknowledged_at = utc_now()
        event.acknowledged_by_user_id = principal.user_id
    result = EventView.model_validate(event)
    await session.commit()
    return result
