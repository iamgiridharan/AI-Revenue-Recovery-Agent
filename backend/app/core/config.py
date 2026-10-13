from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    APP_NAME: str = "Revenue Recovery Agent"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = ""

    # Razorpay (Test Mode only)
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # LLM
    LLM_API_KEY: str = ""

    # Value-aware recovery scoring (estimates only, never authorization)
    # Optional intervention cost per recovery action, in INR, e.g.
    #   INTERVENTION_COSTS='{"RETRY": 3.0, "CREATE_PAYMENT_LINK": 2.0}'
    # Empty means no measured/configured cost is available: expected net
    # recovery is then reported as not computed rather than assumed to be zero.
    INTERVENTION_COSTS: dict[str, float] = {}
    # Optional contact / operational-risk penalties (INR) applied to the
    # separately labelled utility score.  Empty means no penalties are applied.
    UTILITY_PENALTIES: dict[str, float] = {}

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
