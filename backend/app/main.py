from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import (
    PAYMENT_MODE_LIVE_REJECTED,
    PAYMENT_MODE_MOCK,
    PAYMENT_MODE_TEST,
    PAYMENT_MODE_UNCONFIGURED,
    get_settings,
)
from app.core.rate_limit import rate_limit_middleware
from app.api.health import router as health_router
from app.api.cases import router as cases_router
from app.api.ml import router as ml_router
from app.api.agent import router as agent_router
from app.api.policy import router as policy_router
from app.api.webhooks import router as webhooks_router
from app.api.dashboard import router as dashboard_router
from app.api.simulation import router as simulation_router
from app.api.reviews import router as reviews_router
from app.utils.errors import (
    AppError,
    app_error_handler,
    generic_error_handler,
    http_error_handler,
)
from app.utils.logging import logger

settings = get_settings()


def _log_payment_mode() -> None:
    """State the payment mode at startup, including refusals.

    The mode is logged rather than validated into a crash so that
    ``/api/config/status`` stays reachable and can explain the refusal.
    """
    mode = settings.payment_mode

    if mode == PAYMENT_MODE_TEST:
        logger.info(
            "Payment provider: Razorpay TEST MODE (rzp_test_ keys). Recovery "
            "actions create real test-mode links; live money cannot move."
        )
    elif mode == PAYMENT_MODE_MOCK:
        logger.info(
            "Payment provider: MOCK (no Razorpay credentials). Recovery actions "
            "create mock.invalid links that cannot be paid; no provider is called."
        )
    elif mode == PAYMENT_MODE_LIVE_REJECTED:
        logger.error(
            "Payment provider: LIVE_REJECTED. Razorpay live credentials were "
            "supplied and refused; no payment client exists and no payment link "
            "can be created. Configure rzp_test_ keys to run Test Mode."
        )
    elif mode == PAYMENT_MODE_UNCONFIGURED:
        logger.warning(
            "Payment provider: UNCONFIGURED. Razorpay credentials are "
            "incomplete or the key prefix is unrecognised, so no provider can be "
            "reached and payment execution is refused (no mock fallback). Set a "
            "full rzp_test_ key + secret pair, or unset both to run in MOCK mode."
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    _log_payment_mode()
    yield
    # Shutdown
    logger.info(f"Shutting down {settings.APP_NAME}")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered Revenue Recovery Agent API",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory throttling for the sensitive routes.  The Razorpay webhook endpoint is
# exempt inside the middleware, so provider retries are never throttled.
app.middleware("http")(rate_limit_middleware)

# Error handlers -- one consistent envelope, and no stack traces or internals
# leaked to the caller (the generic handler logs the detail server-side only).
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(HTTPException, http_error_handler)
app.add_exception_handler(Exception, generic_error_handler)

# Routers
app.include_router(health_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(ml_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(policy_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(simulation_router, prefix="/api")
app.include_router(reviews_router, prefix="/api")
