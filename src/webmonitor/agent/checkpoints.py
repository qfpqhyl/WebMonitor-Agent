"""Trusted, versioned SDK checkpoints with an explicit confirmation contract."""
import json
from importlib.metadata import version

from agents import RunContextWrapper, RunState
from sqlalchemy import func, select

from webmonitor.api.errors import DomainError
from webmonitor.db.models.conversations import AgentCheckpoint

SCHEMA_VERSION = 1
SDK_VERSION = version("openai-agents")


def serialize_context(context):
    return {"user_id": str(context.principal.user_id),
        "workspace_id": str(context.principal.workspace_id),
        "conversation_id": str(context.conversation_id),
        "agent_run_id": str(context.agent_run_id), "generation": context.generation}


def raw_call(item) -> dict:
    raw = item.raw_item
    return raw if isinstance(raw, dict) else raw.model_dump(mode="json")


def create_arguments(item) -> dict:
    raw = raw_call(item)
    if raw.get("name") != "create_monitor_task" or not raw.get("call_id"):
        raise DomainError("model_unavailable", 503)
    try:
        arguments = json.loads(raw["arguments"])
        if set(arguments) != {"draft_id", "confirmed_revision", "idempotency_key"}:
            raise ValueError()
        from uuid import UUID
        UUID(arguments["draft_id"])
        if type(arguments["confirmed_revision"]) is not int or arguments["confirmed_revision"] < 1:
            raise ValueError()
        if not isinstance(arguments["idempotency_key"], str) or not 1 <= len(arguments["idempotency_key"]) <= 200:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise DomainError("model_unavailable", 503) from None
    return {**arguments, "tool_call_id": raw["call_id"]}


def checkpoint_create_arguments(checkpoint) -> dict:
    if checkpoint.schema_version != SCHEMA_VERSION or checkpoint.sdk_version != SDK_VERSION:
        raise DomainError("checkpoint_incompatible", 409)
    try:
        envelope = json.loads(checkpoint.state_json)
        arguments = envelope["pending_create"]
        # Use the same strict parser as a live SDK interruption.
        class Item:
            raw_item = {"name": "create_monitor_task", "call_id": arguments["tool_call_id"],
                "arguments": json.dumps({key: arguments[key] for key in ("draft_id", "confirmed_revision", "idempotency_key")})}
        parsed = create_arguments(Item())
        if parsed != arguments or parsed["tool_call_id"] != checkpoint.tool_call_id:
            raise ValueError()
        return parsed
    except (KeyError, TypeError, ValueError, DomainError):
        raise DomainError("checkpoint_incompatible", 409) from None


async def latest_checkpoint(session, run_id):
    return await session.scalar(select(AgentCheckpoint).where(AgentCheckpoint.agent_run_id == run_id)
        .order_by(AgentCheckpoint.sequence.desc()).limit(1))


async def save_checkpoint(session, run, state, interruption):
    pending = create_arguments(interruption)
    sequence = await session.scalar(select(func.coalesce(func.max(AgentCheckpoint.sequence), 0)).where(
        AgentCheckpoint.agent_run_id == run.id))
    sdk_state = state.to_json(context_serializer=serialize_context, strict_context=True,
        include_tracing_api_key=False)
    checkpoint = AgentCheckpoint(workspace_id=run.workspace_id, agent_run_id=run.id,
        sequence=sequence + 1, generation=run.generation, sdk_version=SDK_VERSION,
        schema_version=SCHEMA_VERSION, tool_call_id=pending["tool_call_id"],
        state_json=json.dumps({"state": sdk_state, "pending_create": pending}, ensure_ascii=False))
    session.add(checkpoint)
    await session.flush()
    return checkpoint


async def restore_checkpoint(agent, checkpoint, runtime):
    pending = checkpoint_create_arguments(checkpoint)
    try:
        state = await RunState.from_json(agent, json.loads(checkpoint.state_json)["state"],
            context_override=RunContextWrapper(runtime), strict_context=True)
        interruptions = state.get_interruptions()
        if len(interruptions) != 1 or create_arguments(interruptions[0]) != pending:
            raise ValueError()
    except Exception:
        raise DomainError("checkpoint_incompatible", 409) from None
    state.approve(interruptions[0], always_approve=False)
    return state
