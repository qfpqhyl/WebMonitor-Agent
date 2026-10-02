"""Authentication always resolves identity from the server-side session record."""
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.errors import DomainError
from webmonitor.db.session import session_dependency
from webmonitor.schemas.identity import Principal
from webmonitor.services import accounts

SESSION_COOKIE = "wm_session"
DatabaseSession = Annotated[AsyncSession, Depends(session_dependency)]


async def current_principal(request: Request, session: DatabaseSession) -> Principal:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise DomainError("unauthenticated", status=401)
    return await accounts.resolve_session(session, token)


AuthenticatedPrincipal = Annotated[Principal, Depends(current_principal)]
