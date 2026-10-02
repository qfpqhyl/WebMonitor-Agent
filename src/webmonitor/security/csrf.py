"""Origin and nonce-bound CSRF protection, including unauthenticated writes."""
import base64
import hashlib
import hmac
import re
import secrets
import time
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from webmonitor.api.errors import DomainError, domain_error_handler
from webmonitor.config import get_settings

CSRF_COOKIE = "wm_csrf_nonce"
CSRF_TTL_SECONDS = 30 * 60
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_NONCE = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_TOKEN = re.compile(r"v1\.([0-9]{1,12})\.([0-9a-f]{64})\.([A-Za-z0-9_-]{43})\Z")


def cookie_secure() -> bool:
    settings = get_settings()
    return not (settings.development_mode and urlsplit(settings.app_origin).scheme == "http")


def derive_signing_key(purpose: str) -> bytes:
    """Derive domain-separated keys; approval callers must use purpose='approval'."""
    if purpose not in {"csrf", "approval"}:
        raise ValueError("Unknown signing purpose")
    try:
        path = get_settings().secret_file
        if path.stat().st_mode & 0o077:
            raise ValueError("Secret file permissions")
        root = path.read_bytes()
        if len(root) < 32:
            raise ValueError("Secret file length")
    except (OSError, ValueError) as exc:
        raise DomainError("signing_unavailable", status=503) from exc
    return hmac.digest(root, b"webmonitor/signing/v1/" + purpose.encode("ascii"), "sha256")


def _signature(body: str) -> str:
    digest = hmac.digest(derive_signing_key("csrf"), body.encode("ascii"), "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_csrf_token(request: Request, response: Response) -> dict[str, str | int]:
    """Issue an accessible signed token, never expose the HttpOnly nonce itself."""
    nonce = request.cookies.get(CSRF_COOKIE, "")
    if not _NONCE.fullmatch(nonce):
        nonce = secrets.token_urlsafe(32)
    issued_at = int(time.time())
    nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    body = f"v1.{issued_at}.{nonce_hash}"
    token = f"{body}.{_signature(body)}"
    response.set_cookie(CSRF_COOKIE, nonce, max_age=CSRF_TTL_SECONDS,
                        httponly=True, secure=cookie_secure(), samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token, "expires_at": issued_at + CSRF_TTL_SECONDS}


def enforce_csrf(request: Request) -> None:
    """Callable gate for every non-safe request, irrespective of session state."""
    if request.method.upper() in SAFE_METHODS:
        return
    origins = request.headers.getlist("origin")
    if len(origins) != 1 or origins[0] != get_settings().app_origin:
        raise DomainError("origin_invalid", status=403)
    tokens = request.headers.getlist("x-csrf-token")
    nonce = request.cookies.get(CSRF_COOKIE, "")
    if len(tokens) != 1 or not _NONCE.fullmatch(nonce):
        raise DomainError("csrf_invalid", status=403)
    match = _TOKEN.fullmatch(tokens[0])
    if match is None:
        raise DomainError("csrf_invalid", status=403)
    issued_at = int(match[1])
    now = int(time.time())
    if issued_at > now or now >= issued_at + CSRF_TTL_SECONDS:
        raise DomainError("csrf_invalid", status=403)
    nonce_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
    body, signature = tokens[0].rsplit(".", 1)
    if not hmac.compare_digest(match[2], nonce_hash) or not hmac.compare_digest(signature, _signature(body)):
        raise DomainError("csrf_invalid", status=403)


class CSRFMiddleware:
    """Wire with app.add_middleware(CSRFMiddleware); covers all HTTP writes."""
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            try:
                enforce_csrf(request)
            except DomainError as exc:
                response = await domain_error_handler(request, exc)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
