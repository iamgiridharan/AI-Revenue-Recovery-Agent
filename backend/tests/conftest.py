import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings
from app.core.database import Base, get_db
from app.core.rate_limit import reset_rate_limiter
from app.main import app

# Use in-memory SQLite for testing — no leftover .db files.
TEST_DATABASE_URL = "sqlite://"  # in-memory

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)

# Enable foreign key enforcement for SQLite (off by default)
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Shared key used by the test suite.  The real value never leaves the server, and
# these tests only assert that *a* configured key is honoured.
TEST_DEMO_API_KEY = "test-demo-api-key"


@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables before tests, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def secure_test_environment(monkeypatch):
    """Configure the shared key and clear rate-limit counters for every test.

    Rate-limit state is process-global, so it is reset per test to keep tests
    independent rather than to loosen the limiter's behaviour.
    """
    monkeypatch.setattr(get_settings(), "DEMO_API_KEY", TEST_DEMO_API_KEY, raising=False)
    reset_rate_limiter()
    yield
    reset_rate_limiter()


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh database session for each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _override_get_db(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    return override_get_db


@pytest.fixture(scope="function")
def client(db_session):
    """Authenticated test client (sends the shared key on every request)."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    with TestClient(app, headers={"X-API-Key": TEST_DEMO_API_KEY}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def anon_client(db_session):
    """Unauthenticated test client, for the auth and rate-limit assertions."""
    app.dependency_overrides[get_db] = _override_get_db(db_session)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
