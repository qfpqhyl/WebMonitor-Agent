"""Stable public errors without internal exception details."""
from fastapi import Request
from fastapi.responses import JSONResponse

class DomainError(Exception):
    def __init__(self, code: str, status: int = 409, message: str | None = None, details=None):
        self.code, self.status = code, status
        self.message, self.details = message or code.replace("_", " "), details or {}
        super().__init__(self.message)

async def domain_error_handler(request: Request, exc: DomainError):
    headers = {}
    if exc.status == 429:
        headers["Retry-After"] = str(exc.details.get("retry_after", 900))
    return JSONResponse(status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}}, headers=headers)
