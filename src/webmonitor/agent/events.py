"""Events become visible only with the transaction that changed execution state."""
from sqlalchemy import select

from webmonitor.api.errors import DomainError
from webmonitor.db.models.conversations import Conversation, ConversationEvent
from webmonitor.schemas.conversations import EventEnvelope


async def append_event(session, run, kind, payload, tool_call_id=None):
    conversation = await session.scalar(select(Conversation).where(
        Conversation.id == run.conversation_id,
        Conversation.workspace_id == run.workspace_id,
    ).with_for_update().execution_options(populate_existing=True))
    if conversation is None:
        raise DomainError("not_found", 404)
    conversation.last_seq += 1
    event = ConversationEvent(workspace_id=run.workspace_id,
        conversation_id=conversation.id, agent_run_id=run.id,
        seq=conversation.last_seq, schema_version=1, type=kind,
        payload=payload, tool_call_id=tool_call_id)
    session.add(event)
    await session.flush()
    return event


def event_envelope(event):
    return EventEnvelope(schema_version=event.schema_version, event_id=event.id,
        seq=event.seq, conversation_id=event.conversation_id,
        agent_run_id=event.agent_run_id, type=event.type, payload=event.payload,
        tool_call_id=event.tool_call_id)
