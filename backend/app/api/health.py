from fastapi import APIRouter, Depends
from sqlalchemy import text
from app.db.session import get_db
from app.core.config import get_settings

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
    secrets never leave the server. ``MOCK`` means executing an action produces
    a ``mock.invalid`` link and calls no provider, so the UI is expected to
    label it as such rather than implying money can move.
    """
    payments_configured = bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    # The prefix is public information and tells a test key from a live one;
    # the key itself is never returned.
    key_id = settings.RAZORPAY_KEY_ID
    if key_id.startswith("rzp_test_"):
        key_type = "test"
    elif key_id.startswith("rzp_live_"):
        key_type = "live"
    elif key_id:
        key_type = "unknown"
    else:
        key_type = None

    return {
        "success": True,
        "data": {
            "payments": {
                "mode": "TEST_MODE" if payments_configured else "MOCK",
                "razorpay_configured": payments_configured,
                "key_type": key_type,
                "webhook_configured": bool(settings.RAZORPAY_WEBHOOK_SECRET),
            },
        },
    }
