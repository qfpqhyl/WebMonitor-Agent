"""Server-derived identity; never accept this model from request or tool arguments."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class Principal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    user_id: UUID
    workspace_id: UUID
    role: Literal["admin", "member"]
