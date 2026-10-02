"""Trusted execution context; approval secrets never enter tool arguments/history."""
from dataclasses import dataclass
from uuid import UUID
from webmonitor.schemas.identity import Principal

@dataclass
class RuntimeContext:
    principal: Principal
    conversation_id: UUID
    agent_run_id: UUID
    generation: int
    confirmation_token: str | None = None
