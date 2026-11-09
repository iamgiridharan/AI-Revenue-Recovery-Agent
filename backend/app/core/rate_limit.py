"""In-memory rate limiting for the demonstration service.

One lightweight implementation, no extra third-party dependency: a per
(client, method, route) sliding counter held in this process.  ``slowapi`` and
``fastapi-limiter`` are deliberately not used -- the correct pin for either
depends on the exact FastAPI/Starlette versions installed here, and stacking two
throttling engines would change the response surface and the webhook exemption
contract.

Documented limitation: counters live in memory.  They vanish on restart, count
per process rather than per host, and are not shared between workers.  For a
single-instance academic demonstration that is exact and simple; a production
deployment would move the counters to a shared store (e.g. Redis).

The Razorpay webhook route is exempt: provider retries must always land, so it is
never throttled.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse

__all__ = [
    "SENSITIVE_ROUTES",
    "WEBHOOK_EXEMPT_PATHS",
    "InMemoryRateLimiter",
    "limiter",
    "match_route",
    "reset_rate_limiter",
    "rate_limit_middleware",
]

# Sensitive route patterns and their budget: (requests, window_seconds).
SENSITIVE_ROUTES: dict[str, tuple[int, int]] = {
    "/api/agent/diagnose": (10, 60),
    "/api/cases/{case_id}/recovery": (10, 60),
    "/api/cases/{case_id}/review": (10, 60),
    "/api/policies": (10, 60),
    "/api/policies/evaluate": (10, 60),
    "/api/simulation/run": (10, 60),
}

# Never throttled.  The webhook has its own HMAC signature contract, and a 429
# here would silently drop a provider retry (and with it a settlement).
WEBHOOK_EXEMPT_PATHS: frozenset[str] = frozenset({"/api/webhooks/razorpay"})


class InMemoryRateLimiter:
    """Fixed-window counter: at most ``limit`` hits per ``window`` seconds."""

    def __init__(self) -> None:
        # key -> (window_started_at, hits)
        self._windows: dict[tuple[str, str, str], tuple[float, int]] = {}

    def hit(self, key: tuple[str, str, str], limit: int, window: int) -> bool:
        """Record one hit.  Return ``True`` when the caller is still allowed."""
        now = time.monotonic()
        started, hits = self._windows.get(key, (now, 0))
        if now - started >= window:
            started, hits = now, 0
        hits += 1
        self._windows[key] = (started, hits)
        return hits <= limit

    def reset(self) -> None:
        """Drop every counter (used by tests and local troubleshooting)."""
        self._windows.clear()


limiter = InMemoryRateLimiter()


def match_route(path: str) -> tuple[str, int, int] | None:
    """Return ``(pattern, limit, window)`` when ``path`` is a throttled route.

    Matching is segment-by-segment so ``{case_id}`` consumes exactly one path
    segment: ``/api/cases/abc/recovery`` matches, ``/api/cases/abc/recovery/x``
    does not.
    """
    if path in WEBHOOK_EXEMPT_PATHS:
        return None

    path_parts = path.strip("/").split("/")
    for pattern, (limit, window) in SENSITIVE_ROUTES.items():
        pattern_parts = pattern.strip("/").split("/")
        if len(pattern_parts) != len(path_parts):
            continue
        if all(
            expected.startswith("{") and expected.endswith("}")
            or expected == actual
            for expected, actual in zip(pattern_parts, path_parts)
        ):
            return pattern, limit, window
    return None


async def rate_limit_middleware(request: Request, call_next: Callable[..., Any]):
    """Throttle the sensitive routes; let everything else through untouched.

    A refused request gets a 429 JSON envelope directly (a ``HTTPException``
    raised from a user middleware would bypass FastAPI's exception handlers).
    """
    path = request.url.path
    route = match_route(path)
    if route is not None:
        pattern, limit, window = route
        client_host = request.client.host if request.client else "local"
        key = (client_host, request.method, pattern)
        if not limiter.hit(key, limit, window):
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": (
                            f"Too many requests to {pattern}. Limit is {limit} per "
                            f"{window}s. The Razorpay webhook endpoint is exempt."
                        ),
                    },
                },
            )
    return await call_next(request)


def reset_rate_limiter() -> None:
    """Clear all counters.  Exposed for tests."""
    limiter.reset()
