"""Durable agent claiming; waiting approval consumes no worker or lease."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import and_, or_, select

from webmonitor.agent.orchestration import execute_agent_run
from webmonitor.agent.session import lock_live_run
from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.conversations import AgentRun, Conversation
from webmonitor.db.session import get_session_factory
from webmonitor.workers import make_worker_id, record_heartbeat

logger = logging.getLogger(__name__)
LEASE_SECONDS = 90
RENEW_SECONDS = 15


async def claim_agent_run(worker_id):
    async with get_session_factory()() as session, session.begin():
        now = utc_now()
        run = await session.scalar(select(AgentRun).where(
            AgentRun.available_at <= now, AgentRun.cancel_requested_at.is_(None),
            or_(AgentRun.status == "queued", and_(AgentRun.status == "running",
                or_(AgentRun.lease_expires_at.is_(None), AgentRun.lease_expires_at <= now))),
        ).order_by(AgentRun.available_at, AgentRun.id).with_for_update(skip_locked=True).limit(1))
        if run is None:
            return None
        conversation = await session.scalar(select(Conversation).where(
            Conversation.id == run.conversation_id).with_for_update())
        if (conversation.session_lease_owner is not None
                and conversation.session_lease_expires_at is not None
                and conversation.session_lease_expires_at > now
                and conversation.session_lease_owner != worker_id):
            return None
        run.status = "running"
        run.generation += 1
        run.lease_owner = worker_id
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        run.heartbeat_at = now
        run.started_at = run.started_at or now
        conversation.session_generation = run.generation
        conversation.session_lease_owner = worker_id
        conversation.session_lease_expires_at = run.lease_expires_at
        return run.id, run.generation


async def _renew(run_id, generation, worker_id):
    async with get_session_factory()() as session, session.begin():
        run = await lock_live_run(session, run_id, generation, worker_id)
        conversation = await session.scalar(select(Conversation).where(
            Conversation.id == run.conversation_id).with_for_update())
        if conversation.session_generation != generation or conversation.session_lease_owner != worker_id:
            raise DomainError("execution_superseded", 409)
        now = utc_now()
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        conversation.session_lease_expires_at = run.lease_expires_at
        await record_heartbeat(session, "agent", worker_id, details={"agent_run_id": str(run_id)})


async def _watch_execution(run_id, generation, worker_id, task):
    """Poll cancellation every second; renew durable leases every fifteen seconds."""
    last_renew = asyncio.get_running_loop().time()
    while not task.done():
        await asyncio.sleep(1)
        if task.done():
            return
        try:
            async with get_session_factory()() as session:
                run = await session.get(AgentRun, run_id)
                if (run is None or run.status != "running" or run.generation != generation
                        or run.lease_owner != worker_id or run.cancel_requested_at is not None
                        or run.lease_expires_at is None or run.lease_expires_at <= utc_now()):
                    task.cancel()
                    return
            if asyncio.get_running_loop().time() - last_renew >= RENEW_SECONDS:
                await _renew(run_id, generation, worker_id)
                last_renew = asyncio.get_running_loop().time()
        except Exception:
            # A worker unable to verify its fence cannot continue executing tools.
            task.cancel()
            return


async def run_agent_once(worker_id: str) -> bool:
    claimed = await claim_agent_run(worker_id)
    if claimed is None:
        return False
    run_id, generation = claimed
    execution = asyncio.create_task(execute_agent_run(run_id, generation, worker_id))
    watchdog = asyncio.create_task(_watch_execution(run_id, generation, worker_id, execution))
    try:
        await execution
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise
    finally:
        execution.cancel()
        watchdog.cancel()
        await asyncio.gather(execution, watchdog, return_exceptions=True)
    return True


async def run_agent_worker() -> None:
    worker_id = make_worker_id("agent")
    while True:
        try:
            async with get_session_factory()() as session, session.begin():
                await record_heartbeat(session, "agent", worker_id)
            if not await run_agent_once(worker_id):
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Agent worker iteration failed; durable state retained")
            await asyncio.sleep(2)
