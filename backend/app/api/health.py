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
