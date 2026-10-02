"""One-second production scheduler; safe to run multiple independent processes."""
import asyncio
import logging

from webmonitor.db.session import get_session_factory
from webmonitor.services.runs import schedule_due
from webmonitor.workers import make_worker_id, record_heartbeat

logger = logging.getLogger(__name__)


async def run_scheduler() -> None:
    worker_id = make_worker_id("scheduler")
    factory = get_session_factory()
    while True:
        started = asyncio.get_running_loop().time()
        try:
            async with factory() as session:
                async with session.begin():
                    created = await schedule_due(session)
                    await record_heartbeat(session, "scheduler", worker_id,
                        details={"scheduled_runs": len(created)})
        except asyncio.CancelledError:
            raise
        except Exception:
            # Rollback is automatic. Do not leak SQL parameters/connection secrets.
            logger.error("Scheduler transaction failed")
        await asyncio.sleep(max(0, 1 - (asyncio.get_running_loop().time() - started)))


async def run() -> None:
    await run_scheduler()
