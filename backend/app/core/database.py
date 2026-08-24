from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()

engine = None
SessionLocal = None
Base = declarative_base()


def init_database():
    """Initialize database engine and session factory."""
    global engine, SessionLocal

    if not settings.DATABASE_URL:
        raise ValueError(
            "DATABASE_URL is not configured. "
            "Please set it in your .env file. "
            "Example: DATABASE_URL=postgresql://user:password@localhost:5432/revenue_recovery"
        )

    engine = create_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
    )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency for database sessions."""
    if engine is None:
        init_database()

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
