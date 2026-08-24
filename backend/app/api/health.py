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
            from app.core.database import get_db as _get_db
            from sqlalchemy.orm import Session
            from app.core.database import init_database, engine
            from app.core.database import SessionLocal as _SessionLocal

            if engine is None:
                init_database()

            db = _SessionLocal()
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
