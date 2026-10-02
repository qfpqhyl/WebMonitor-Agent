"""PostgreSQL SDK history, fenced by the execution and conversation leases."""
import hashlib
import json
from uuid import UUID

from agents.models.fake_id import FAKE_RESPONSES_ID
from sqlalchemy import delete, func, select

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.conversations import AgentRun, AgentSessionItem, Conversation
from webmonitor.db.session import get_session_factory


async def lock_live_run(session, run_id: UUID, generation: int, worker_id: str):
    run = await session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
    if (run is None or run.status != "running" or run.generation != generation
            or run.lease_owner != worker_id or run.lease_expires_at is None
            or run.lease_expires_at <= utc_now() or run.cancel_requested_at is not None):
        raise DomainError("execution_superseded", 409)
    return run


class PostgreSQLSession:
    """The SDK adapter never exposes database handles to model context."""

    session_settings = None

    def __init__(self, conversation_id: UUID, run_id: UUID, generation: int, worker_id: str):
        self.session_id = str(conversation_id)
        self.conversation_id = conversation_id
        self.run_id = run_id
        self.generation = generation
        self.worker_id = worker_id

    async def _lock(self, session):
        run = await lock_live_run(session, self.run_id, self.generation, self.worker_id)
        conversation = await session.scalar(select(Conversation).where(
            Conversation.id == self.conversation_id,
            Conversation.workspace_id == run.workspace_id,
        ).with_for_update())
        if (conversation is None or conversation.session_lease_owner != self.worker_id
                or conversation.session_generation != self.generation
                or conversation.session_lease_expires_at is None
                or conversation.session_lease_expires_at <= utc_now()):
            raise DomainError("execution_superseded", 409)
        return run

    async def get_items(self, limit: int | None = None) -> list[dict]:
        async with get_session_factory()() as session, session.begin():
            await self._lock(session)
            query = select(AgentSessionItem).where(AgentSessionItem.conversation_id == self.conversation_id)
            if limit is not None:
                if limit <= 0:
                    return []
                rows = (await session.scalars(query.order_by(AgentSessionItem.sequence.desc()).limit(limit))).all()
                return [row.item for row in reversed(rows)]
            return list((await session.scalars(query.with_only_columns(AgentSessionItem.item).order_by(AgentSessionItem.sequence))).all())

    async def add_items(self, items: list[dict]) -> None:
        if not items:
            return
        async with get_session_factory()() as session, session.begin():
            run = await self._lock(session)
            sequence = await session.scalar(select(func.coalesce(func.max(AgentSessionItem.sequence), 0)).where(
                AgentSessionItem.conversation_id == self.conversation_id))
            occurrences = {}
            for item in items:
                canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                occurrence = occurrences.get(digest, 0)
                occurrences[digest] = occurrence + 1
                identity = item.get("id")
                if not identity or identity == FAKE_RESPONSES_ID:
                    call_id = item.get("call_id")
                    identity = (f"{item.get('type')}:{call_id}" if call_id else
                        hashlib.sha256(f"{self.run_id}:{digest}:{occurrence}".encode()).hexdigest())
                identity = str(identity)
                if len(identity) > 200:
                    identity = hashlib.sha256(identity.encode()).hexdigest()
                existing = await session.scalar(select(AgentSessionItem).where(
                    AgentSessionItem.conversation_id == self.conversation_id, AgentSessionItem.item_id == identity))
                if existing is not None:
                    if existing.item != item:
                        raise DomainError("session_item_conflict", 409)
                    continue
                sequence += 1
                session.add(AgentSessionItem(workspace_id=run.workspace_id,
                    conversation_id=self.conversation_id, sequence=sequence, item_id=identity,
                    tool_call_id=item.get("call_id"), item=item))

    async def pop_item(self) -> dict | None:
        async with get_session_factory()() as session, session.begin():
            await self._lock(session)
            row = await session.scalar(select(AgentSessionItem).where(
                AgentSessionItem.conversation_id == self.conversation_id).order_by(AgentSessionItem.sequence.desc()).limit(1))
            if row is None:
                return None
            item = row.item
            await session.delete(row)
            return item

    async def clear_session(self) -> None:
        async with get_session_factory()() as session, session.begin():
            await self._lock(session)
            await session.execute(delete(AgentSessionItem).where(AgentSessionItem.conversation_id == self.conversation_id))
