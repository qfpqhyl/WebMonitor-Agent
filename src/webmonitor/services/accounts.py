"""PostgreSQL-backed identity, invitation consumption and session revocation."""
import asyncio
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog, Invitation, LoginRateLimit, Membership, Session, User, Workspace
from webmonitor.schemas.identity import Principal

_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    try:
        validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise DomainError("invalid_email", 422) from None
    return email


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise DomainError("invalid_password", 422, "Password must contain 12–128 characters")


def principal_for(user: User, membership: Membership) -> Principal:
    return Principal(user_id=user.id, workspace_id=membership.workspace_id, role=membership.role)


def new_session(session, principal: Principal) -> str:
    token = secrets.token_urlsafe(32)
    session.add(Session(workspace_id=principal.workspace_id, user_id=principal.user_id,
        token_hash=token_hash(token), expires_at=utc_now()+timedelta(hours=get_settings().session_ttl_hours)))
    return token


async def admin_create(session, *, email: str, password: str, display_name: str | None = None) -> Principal:
    email = normalize_email(email)
    validate_password(password)
    password_hash = await asyncio.to_thread(_hasher.hash, password)
    try:
        async with session.begin():
            workspace = await session.scalar(select(Workspace).where(Workspace.slug == "local").with_for_update())
            if workspace is None:
                raise DomainError("not_initialized", 503)
            if await session.scalar(select(User.id).where(User.email == email)):
                raise DomainError("account_exists", 409)
            user = User(email=email, display_name=display_name or email, password_hash=password_hash)
            session.add(user)
            await session.flush()
            membership = Membership(workspace_id=workspace.id, user_id=user.id, role="admin")
            session.add(membership)
            await session.flush()
            session.add(AuditLog(workspace_id=workspace.id, actor_user_id=user.id, action="admin.created", resource_type="user", resource_id=user.id))
            principal = principal_for(user, membership)
        return principal
    except IntegrityError:
        raise DomainError("account_exists", 409) from None


async def invite_create(session, *, email: str, ttl_hours: int = 24) -> str:
    email = normalize_email(email)
    if email not in get_settings().allowed_registration_emails:
        raise DomainError("email_not_allowed", 403)
    if not 1 <= ttl_hours <= 168:
        raise DomainError("invalid_invitation_ttl", 422)
    token = secrets.token_urlsafe(32)
    async with session.begin():
        admin = (await session.execute(select(Membership).join(Workspace, Workspace.id == Membership.workspace_id).join(User, User.id == Membership.user_id).where(Workspace.slug == "local", Membership.role == "admin", Membership.active.is_(True), User.active.is_(True)).order_by(Membership.created_at).limit(1))).scalar_one_or_none()
        if admin is None:
            raise DomainError("administrator_required", 409)
        invitation = Invitation(workspace_id=admin.workspace_id, email=email, token_hash=token_hash(token), created_by_user_id=admin.user_id, expires_at=utc_now()+timedelta(hours=ttl_hours))
        session.add(invitation)
        await session.flush()
        session.add(AuditLog(workspace_id=admin.workspace_id, actor_user_id=admin.user_id, action="invitation.created", resource_type="invitation", resource_id=invitation.id))
    return get_settings().app_origin+"/register?"+urlencode({"invitation_token":token,"email":email})


async def register(session, *, display_name: str, email: str, password: str, invitation_token: str):
    try:
        email = normalize_email(email)
        validate_password(password)
        if not display_name.strip() or len(display_name) > 200:
            raise DomainError("registration_failed", 422)
    except DomainError:
        raise DomainError("registration_failed", 422) from None
    hashed = await asyncio.to_thread(_hasher.hash, password)
    try:
        async with session.begin():
            invitation = await session.scalar(select(Invitation).where(Invitation.token_hash == token_hash(invitation_token)).with_for_update())
            if invitation is None or invitation.consumed_at or invitation.revoked_at or invitation.expires_at <= utc_now() or invitation.email != email:
                raise DomainError("registration_failed", 409)
            user = User(email=email, display_name=display_name.strip(), password_hash=hashed)
            session.add(user)
            await session.flush()
            membership = Membership(workspace_id=invitation.workspace_id, user_id=user.id, role="member")
            session.add(membership)
            await session.flush()
            invitation.consumed_at = utc_now()
            invitation.consumed_by_user_id = user.id
            principal = principal_for(user, membership)
            token = new_session(session, principal)
            session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=user.id, action="account.registered", resource_type="user", resource_id=user.id))
        return principal, token
    except IntegrityError:
        raise DomainError("registration_failed", 409) from None


async def login(session, *, email: str, password: str, source_ip: str):
    email = email.strip().casefold()
    now = utc_now()
    keys = [(dimension, token_hash(value)) for dimension, value in (("email",email),("ip",source_ip))]
    failure = None
    result = None
    async with session.begin():
        for dimension, key in sorted(keys):
            lock = int.from_bytes(hashlib.sha256((dimension+key).encode()).digest()[:8], "big", signed=True)
            await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key":lock})
            count, earliest = (await session.execute(select(func.coalesce(func.sum(LoginRateLimit.attempt_count),0), func.min(LoginRateLimit.expires_at)).where(LoginRateLimit.dimension == dimension, LoginRateLimit.key_hash == key, LoginRateLimit.expires_at > now))).one()
            if count >= 5:
                failure = DomainError("rate_limited",429,details={"retry_after":max(1,int((earliest-now).total_seconds())+1)})
        if failure is None:
            user = await session.scalar(select(User).where(User.email == email))
            membership = await session.scalar(select(Membership).where(Membership.user_id == user.id, Membership.active.is_(True)).limit(1)) if user else None
            try:
                valid = await asyncio.to_thread(_hasher.verify, user.password_hash if user else _dummy_hash, password)
            except (VerificationError, InvalidHashError):
                valid = False
            if not valid or not user or not user.active or membership is None:
                for dimension,key in keys:
                    session.add(LoginRateLimit(dimension=dimension,key_hash=key,window_started_at=now,expires_at=now+timedelta(minutes=15),attempt_count=1))
                failure = DomainError("invalid_credentials",401)
            else:
                principal = principal_for(user,membership)
                result = principal,new_session(session,principal)
    if failure:
        raise failure
    return result


async def resolve_session(session, token: str) -> Principal:
    row = (await session.execute(select(User, Membership).join(Membership, Membership.user_id == User.id).join(Session, (Session.user_id == User.id) & (Session.workspace_id == Membership.workspace_id)).where(Session.token_hash == token_hash(token), Session.revoked_at.is_(None), Session.expires_at > utc_now(), User.active.is_(True), Membership.active.is_(True)))).first()
    if row is None:
        raise DomainError("unauthenticated",401)
    return principal_for(*row)


async def logout(session, token: str) -> None:
    row = await session.scalar(select(Session).where(Session.token_hash == token_hash(token)).with_for_update())
    if row is not None:
        row.revoked_at = utc_now()
    await session.commit()
