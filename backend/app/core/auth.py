"""Shared-secret bearer authentication.

The service uses a single static shared API key (DEMO_API_KEY) for this
single-instance academic demonstration.  Nothing else is attempted here: no
signup, no passwords, no per-user accounts, no multi-tenancy.

Security posture
----------------
- Missing *or* invalid key  -> HTTP 401 (fail closed).
- DEMO_API_KEY unset         -> HTTP 503 on protected routes (fail closed).
- Valid key                  -> access granted, identity ``demo_api_key``.
- The real key is never logged, never stored in an audit record, and never
  returned to a client.  Comparison is constant-time.
- DO NOT claim this is secure for public deployment.  The key ships inside a
  browser bundle when the frontend reads it from a Vite ``.env.local`` file, so
  it can be extracted from the page's source.  Public deployments must use a
  backend/library key or reverse-proxy (TLS-terminating) authentication.

Accepted credential forms (either is enough):

    X-API-Key: <DEMO_API_KEY>
    Authorization: Bearer <DEMO_API_KEY>
"""

import secrets

from fastapi import Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

__all__ = ["get_api_key", "require_auth", "AUTH_ACTOR"]

# Identity recorded on every action authorized by this shared key.  It is a
# fixed label, never the key material and never a named individual.
AUTH_ACTOR = "demo_api_key"

SECURITY_SCHEME = HTTPBearer(auto_error=False)


def _unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": "unauthorized", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(SECURITY_SCHEME),
) -> str:
    """Return the authenticated identity, or raise (fail closed)."""
    from app.core.config import get_settings

    settings = get_settings()

    # A deployment must have a key configured at all.  Without one, protected
    # routes refuse with 503 rather than silently running open.
    if not settings.DEMO_API_KEY:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "service_unavailable",
                "message": (
                    "Shared-key authentication is not configured on this server. "
                    "Set DEMO_API_KEY in backend/.env."
                ),
            },
        )

    supplied = request.headers.get("X-API-Key", "").strip()
    if not supplied and credentials is not None:
        supplied = (credentials.credentials or "").strip()

    # Fail closed: empty, missing, or malformed credential -> 401.
    if not supplied:
        raise _unauthorized(
            "Missing credential. Send X-API-Key: <DEMO_API_KEY> "
            "(or Authorization: Bearer <DEMO_API_KEY>)."
        )

    # Constant-time comparison keeps key-guessing work higher per attempt and
    # never exposes the value in logs or errors.
    if not secrets.compare_digest(
        supplied.encode("utf-8"), settings.DEMO_API_KEY.encode("utf-8")
    ):
        raise _unauthorized("Invalid API key.")

    return AUTH_ACTOR


# Re-exported for routes that prefer a plain Depends(...) over Security.
require_auth = get_api_key
