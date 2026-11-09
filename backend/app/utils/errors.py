from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from app.utils.logging import logger


class AppError(Exception):
    """Base application error."""

    def __init__(self, message: str, status_code: int = 500, detail: str | None = None):
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(self.message)


class DatabaseError(AppError):
    """Database-related errors."""

    def __init__(self, message: str = "Database error occurred", detail: str | None = None):
        super().__init__(message=message, status_code=500, detail=detail)


class NotFoundError(AppError):
    """Resource not found errors."""

    def __init__(self, message: str = "Resource not found", detail: str | None = None):
        super().__init__(message=message, status_code=404, detail=detail)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle custom application errors."""
    logger.warning(f"AppError: {exc.message} | Path: {request.url.path}")
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "message": exc.message,
                "detail": exc.detail,
            },
        },
    )


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle FastAPI HTTP exceptions with the shared error envelope.

    ``detail`` may already be a structured payload (the authentication dependency
    raises ``{"error": ..., "message": ...}``).  Merge it rather than nesting it,
    so every error response has the same ``success`` / ``error`` shape and no
    stack trace or internal value is exposed.
    """
    logger.warning(f"HTTPException: {exc.detail} | Path: {request.url.path}")

    detail = exc.detail
    if isinstance(detail, dict):
        error = {"message": detail.get("message", "Request failed"), **detail}
    else:
        error = {"message": detail}

    return JSONResponse(
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None) or None,
        content={"success": False, "error": error},
    )


async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected errors."""
    logger.error(f"Unhandled error: {exc} | Path: {request.url.path}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "message": "An unexpected error occurred",
            },
        },
    )
