from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()

engine = None
SessionLocal = None
Base = declarative_base()


def _build_database_url(url: str) -> str:
    """Ensure the DATABASE_URL uses the correct SQLAlchemy driver.

    Accepts plain postgres:// or postgresql:// URIs and converts them
    to the modern postgresql+psycopg2:// form when needed.
    """
    # Normalize Heroku/Render-style postgres:// to postgresql://
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    # Ensure the postgresql+psycopg2 driver is specified
    if url.startswith("postgresql://") and "+" not in url.split(":")[0]:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)

    return url


def init_database():
    """Initialize database engine and session factory."""
    global engine, SessionLocal

    if not settings.DATABASE_URL:
        raise ValueError(
            "DATABASE_URL is not configured. "
            "Please set it in your .env file. "
            "Example: DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/revenue_recovery"
        )

    database_url = _build_database_url(settings.DATABASE_URL)

    engine = create_engine(
        database_url,
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
