import logging
import sys
from app.core.config import get_settings


def setup_logging() -> logging.Logger:
    """Configure and return application logger."""
    settings = get_settings()

    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    logger = logging.getLogger("revenue_recovery")
    logger.info(f"Logging initialized at level: {logging.getLevelName(log_level)}")

    return logger


logger = setup_logging()
