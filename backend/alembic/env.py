from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool
from alembic import context
import os
import sys

# Add the parent directory to Python path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.core.config import get_settings
from app.core.database import Base

# Import ALL models so Alembic can detect them for autogenerate
from app.models.customer import Customer          # noqa: F401
from app.models.transaction import Transaction      # noqa: F401
from app.models.revenue_risk_case import RevenueRiskCase  # noqa: F401
from app.models.recovery_action import RecoveryAction      # noqa: F401
from app.models.audit_event import AuditEvent              # noqa: F401
from app.models.policy_config import PolicyConfig          # noqa: F401
from app.models.policy_decision import PolicyDecision      # noqa: F401

settings = get_settings()

# this is the Alembic Config object
config = context.config

# Set the database URL from our settings (normalize driver if needed)
database_url = settings.DATABASE_URL
if database_url:
    # Normalize Heroku/Render-style postgres:// to postgresql://
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://") :]
    # Ensure the postgresql+psycopg2 driver is specified
    if database_url.startswith("postgresql://") and "+" not in database_url.split(":")[0]:
        database_url = database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    config.set_main_option("sqlalchemy.url", database_url)

# Set up Python logging from the config file
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set target metadata for autogenerate support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
