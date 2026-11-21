from fastapi import APIRouter, Depends
from sqlalchemy import text
from app.db.session import get_db
from app.core.config import (
    PAYMENT_MODE_LIVE_REJECTED,
    PAYMENT_MODE_TEST,
    TEST_KEY_PREFIX,
    get_settings,
    key_id_prefix,
    resolve_payment_mode,
)

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
def health_check():
    """
    Health check endpoint that verifies:
    - Backend is running
    - Database connection is alive (if configured)
    """
    db_status = "not configured"

    # Check database if configured
    if settings.DATABASE_URL:
        try:
            # Import the module, not the names: ``engine``/``SessionLocal`` are
            # created lazily by ``init_database()``, so a by-value import would
            # keep the pre-initialisation ``None`` binding.
            from app.core import database as database_module

            if database_module.engine is None:
                database_module.init_database()

            db = database_module.SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                db_status = "connected"
            except Exception as e:
                db_status = f"error: {str(e)}"
            finally:
                db.close()
        except Exception as e:
            db_status = f"error: {str(e)}"
    else:
        db_status = "not configured (set DATABASE_URL in .env)"

    return {
        "success": True,
        "data": {
            "status": "healthy",
            "version": settings.APP_VERSION,
            "database": db_status,
        },
    }


@router.get("/config/status")
def config_status():
    """
    Report whether payment actions reach a real provider or are mocked.

    The response carries presence flags and a mode label only: key values and
    secrets never leave the server.  Exactly one mode is reported:

    - ``TEST_MODE``: a complete ``rzp_test_`` key + secret pair is configured, so
      a recovery action creates a real Razorpay Test Mode link.  If the SDK is
      missing or the client cannot be built, execution is refused -- this mode
      never falls back to a mock link.
    - ``LIVE_REJECTED``: a ``rzp_live_`` key was supplied and refused.  No client
      exists and no payment link can be created.  This is never reported as
      TEST_MODE.
    - ``UNCONFIGURED``: Razorpay credentials are half-configured or the key prefix
      is unrecognised, so no provider can be reached.  Not silently mocked.
    - ``MOCK``: no Razorpay credentials at all; an executed action produces a
      ``mock.invalid`` link and calls no provider.
    """
    mode = resolve_payment_mode(
        getattr(settings, "RAZORPAY_KEY_ID", ""),
        getattr(settings, "RAZORPAY_KEY_SECRET", ""),
        bool(getattr(settings, "RAZORPAY_LIVE_KEY_REJECTED", False)),
    )

    # Only the presence flags and the (public) key prefix class are reported;
    # the key itself is never returned.
    prefix = key_id_prefix(getattr(settings, "RAZORPAY_KEY_ID", ""))
    if mode == PAYMENT_MODE_LIVE_REJECTED:
        key_type = "live"
    elif prefix.startswith(TEST_KEY_PREFIX):
        key_type = "test"
    elif prefix:
        key_type = "unknown"
    else:
        key_type = None

    return {
        "success": True,
        "data": {
            "payments": {
                # A provider can only be reached in TEST_MODE; every other mode
                # means no Razorpay call is possible.
                "mode": mode,
                "razorpay_configured": mode == PAYMENT_MODE_TEST,
                "key_type": key_type,
                "webhook_configured": bool(getattr(settings, "RAZORPAY_WEBHOOK_SECRET", "")),
                # True when live credentials were supplied and refused, so a
                # client can warn explicitly instead of inferring it from key_type.
                "live_key_rejected": mode == PAYMENT_MODE_LIVE_REJECTED,
            },
        },
    }
