"""Durable conversation HTTP commands and resumable, authenticated event streaming."""
import asyncio
import re
import time
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from webmonitor.agent.events import event_envelope
from webmonitor.api.dependencies import AuthenticatedPrincipal, DatabaseSession, SESSION_COOKIE
from webmonitor.api.errors import DomainError
from webmonitor.db.models.conversations import ConversationEvent
from webmonitor.db.session import get_session_factory
from webmonitor.schemas.conversations import (CancelRequest, ConfirmRequest, ConversationCreate,
    ConversationSnapshot, CreateRequest, EventEnvelope, MessageRequest, RunResponse)
from webmonitor.schemas.monitors import CreateMonitorResult
from webmonitor.services import accounts, conversations

router = APIRouter(prefix="/api/v1/agent/conversations", tags=["conversations"])


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"


@router.post("", response_model=ConversationSnapshot, status_code=201)
async def create_conversation(payload: ConversationCreate, response: Response,
                              session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.create_conversation(session, principal, payload.title)
    await session.commit()
    _no_store(response)
    return result


@router.get("/{conversation_id}", response_model=ConversationSnapshot)
async def snapshot(conversation_id: UUID, response: Response,
                   session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.snapshot(session, principal, conversation_id)
    await session.commit()
    _no_store(response)
    return result


@router.post("/{conversation_id}/messages", response_model=RunResponse, status_code=202)
async def message(conversation_id: UUID, payload: MessageRequest, response: Response,
                  session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.post_message(session, principal, conversation_id, payload)
    await session.commit()
    _no_store(response)
    return result


@router.post("/{conversation_id}/confirm", response_model=RunResponse)
async def confirm(conversation_id: UUID, payload: ConfirmRequest, response: Response,
                  session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.confirm(session, principal, conversation_id, payload)
    await session.commit()
    _no_store(response)
    if result.status != "completed":
        response.status_code = 202
    return result


@router.post("/{conversation_id}/cancel", response_model=RunResponse)
async def cancel(conversation_id: UUID, payload: CancelRequest, response: Response,
                 session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.cancel(session, principal, conversation_id, payload.agent_run_id)
    await session.commit()
    _no_store(response)
    return result


@router.post("/{conversation_id}/create", response_model=CreateMonitorResult)
async def recover_create(conversation_id: UUID, payload: CreateRequest, response: Response,
                         session: DatabaseSession, principal: AuthenticatedPrincipal):
    result = await conversations.create_from_approval(session, principal, conversation_id, payload)
    await session.commit()
    _no_store(response)
    return result


def _cursor(after, last_event_id):
    values = [value for value in (after, last_event_id) if value is not None]
    if any(not re.fullmatch(r"0|[1-9][0-9]{0,18}", value) for value in values):
        raise DomainError("cursor_invalid", 422)
    if len(values) == 2 and int(values[0]) != int(values[1]):
        raise DomainError("cursor_invalid", 422)
    return int(values[0]) if values else 0


@router.get("/{conversation_id}/events", response_class=StreamingResponse,
    responses={200: {"description": "SSE id is the conversation sequence; data is an EventEnvelope.",
        "content": {"text/event-stream": {"schema": EventEnvelope.model_json_schema()}}}})
async def events(conversation_id: UUID, request: Request, session: DatabaseSession,
                 principal: AuthenticatedPrincipal, after: str | None = None):
    cursor = _cursor(after, request.headers.get("Last-Event-ID"))
    conversation = await conversations.require_conversation(session, principal, conversation_id)
    if cursor > conversation.last_seq:
        raise DomainError("cursor_invalid", 422)
    await session.commit()
    token = request.cookies[SESSION_COOKIE]

    async def stream():
        nonlocal cursor
        heartbeat_at = time.monotonic()
        while not await request.is_disconnected():
            try:
                async with get_session_factory()() as polling:
                    live_principal = await accounts.resolve_session(polling, token)
                    current = await conversations.require_conversation(polling, live_principal, conversation_id)
                    if cursor > current.last_seq:
                        # Database reset invalidates the subscription; snapshot is required.
                        return
                    rows = (await polling.scalars(select(ConversationEvent).where(
                        ConversationEvent.workspace_id == live_principal.workspace_id,
                        ConversationEvent.conversation_id == conversation_id,
                        ConversationEvent.seq > cursor,
                    ).order_by(ConversationEvent.seq).limit(100))).all()
                    frames = [event_envelope(row) for row in rows]
            except DomainError:
                # Authentication/ownership loss terminates rather than leaking events.
                return
            for envelope in frames:
                cursor = envelope.seq
                yield f"id: {cursor}\ndata: {envelope.model_dump_json()}\n\n"
            now = time.monotonic()
            if now - heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                heartbeat_at = now
            if len(frames) < 100:
                await asyncio.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
