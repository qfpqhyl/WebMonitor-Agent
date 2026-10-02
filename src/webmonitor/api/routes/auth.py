"""Cookie authentication; credentials and roles never come from client identity."""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from webmonitor.api.dependencies import AuthenticatedPrincipal, DatabaseSession, SESSION_COOKIE
from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings
from webmonitor.db.models.accounts import User
from webmonitor.schemas.identity import Principal
from webmonitor.security.csrf import CSRF_COOKIE, cookie_secure, issue_csrf_token
from webmonitor.services import accounts

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    display_name: str
    email: str
    password: str = Field(repr=False)
    invitation_token: str = Field(repr=False)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str
    password: str = Field(repr=False)


class AuthProfile(Principal):
    email: str
    display_name: str


class CSRFResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    csrf_token: str
    expires_at: int


async def _profile(session: DatabaseSession, principal: Principal) -> AuthProfile:
    user = await session.get(User, principal.user_id)
    if user is None or not user.active:
        raise DomainError("unauthenticated", status=401)
    return AuthProfile(**principal.model_dump(), email=user.email, display_name=user.display_name)


def _session_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token,
                        max_age=get_settings().session_ttl_hours * 3600,
                        httponly=True, secure=cookie_secure(), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"


@router.get("/csrf", response_model=CSRFResponse)
async def csrf(request: Request, response: Response):
    return issue_csrf_token(request, response)


@router.post("/register", response_model=AuthProfile, status_code=201)
async def register(payload: RegisterRequest, response: Response, session: DatabaseSession):
    principal, token = await accounts.register(
        session, display_name=payload.display_name, email=payload.email,
        password=payload.password, invitation_token=payload.invitation_token,
    )
    profile = await _profile(session, principal)
    _session_cookie(response, token)
    return profile


@router.post("/login", response_model=AuthProfile)
async def login(payload: LoginRequest, request: Request, response: Response, session: DatabaseSession):
    # Proxy forwarding headers are deliberately ignored. Configure a trusted proxy
    # at the ASGI server boundary, never infer trust from a client-supplied header.
    if request.client is None:
        raise DomainError("invalid_credentials", status=401)
    principal, token = await accounts.login(
        session, email=payload.email, password=payload.password,
        source_ip=request.client.host,
    )
    profile = await _profile(session, principal)
    _session_cookie(response, token)
    return profile


@router.get("/me", response_model=AuthProfile)
async def me(response: Response, session: DatabaseSession, principal: AuthenticatedPrincipal):
    response.headers["Cache-Control"] = "no-store"
    return await _profile(session, principal)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: DatabaseSession,
                 principal: AuthenticatedPrincipal):
    await accounts.logout(session, request.cookies[SESSION_COOKIE])
    response.delete_cookie(SESSION_COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=cookie_secure(), httponly=True, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
    response.status_code = 204
    return response
