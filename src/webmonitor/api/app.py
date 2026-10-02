"""FastAPI factory. Readiness is separate from process liveness."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from sqlalchemy.exc import OperationalError

from webmonitor.api.errors import DomainError, domain_error_handler
from webmonitor.db.initialize import database_ready
from webmonitor.db.session import get_engine
from webmonitor.api.routes.auth import router as auth_router
from webmonitor.api.routes.members import router as members_router
from webmonitor.api.routes.evidence import router as evidence_router
from webmonitor.api.routes.conversations import router as conversations_router
from webmonitor.api.routes.notifications import router as notifications_router
from webmonitor.api.routes.monitors import router as monitors_router
from webmonitor.security.csrf import CSRFMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    from agents import set_tracing_disabled
    set_tracing_disabled(True)
    yield
    await get_engine().dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="WebMonitor", version="0.1.0", lifespan=lifespan, openapi_url="/api/v1/openapi.json", docs_url=None, redoc_url=None)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_middleware(CSRFMiddleware)
    app.include_router(auth_router)
    app.include_router(members_router)
    app.include_router(evidence_router)
    app.include_router(conversations_router)
    app.include_router(notifications_router)
    app.include_router(monitors_router)

    @app.exception_handler(OperationalError)
    async def database_error(request: Request, exc: OperationalError):
        return await domain_error_handler(request, DomainError("database_unavailable", 503))

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        return await domain_error_handler(request, DomainError("internal_error", 500, "Request could not be completed"))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {"code": "validation_failed", "message": "Invalid request", "details": {"fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}}})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": codes.get(exc.status_code, "request_failed"), "message": "Request rejected", "details": {}}})

    @app.get("/api/v1/health/live")
    async def live():
        return {"status": "alive"}

    @app.get("/api/v1/health/ready")
    async def ready():
        if not await database_ready():
            raise DomainError("not_ready", 503, "Database initialization is unavailable or incompatible")
        return {"status": "ready"}

    return app
