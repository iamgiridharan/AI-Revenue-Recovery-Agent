import logging

from pydantic import model_validator
from pydantic_settings import BaseSettings
from functools import lru_cache

logger = logging.getLogger(__name__)

# Razorpay key-id prefixes.  Test Mode is the only provider mode this project
# may reach; a live key is refused instead of being used.
TEST_KEY_PREFIX = "rzp_test_"
LIVE_KEY_PREFIX = "rzp_live_"

# Payment modes reported by /api/config/status and the startup log.  They are
# deliberately distinct so a half-configured or refused deployment can never be
# mistaken for a working Test Mode one.
PAYMENT_MODE_UNCONFIGURED = "UNCONFIGURED"
PAYMENT_MODE_MOCK = "MOCK"
PAYMENT_MODE_TEST = "TEST_MODE"
PAYMENT_MODE_LIVE_REJECTED = "LIVE_REJECTED"


def key_id_prefix(key_id) -> str:
    """Return a normalised Razorpay key-id prefix ('' when absent or not a str)."""
    return key_id.strip().lower() if isinstance(key_id, str) else ""


def resolve_payment_mode(key_id, key_secret, live_key_rejected: bool = False) -> str:
    """
    Classify the configured Razorpay credentials into exactly one mode.

    Precedence (fail closed, most severe first):

    - ``LIVE_REJECTED``: a live key was supplied, or a previous validation step
      already refused one.  No client may be built and no link may be created.
    - ``TEST_MODE``: a full ``rzp_test_`` key + secret pair is configured, so
      Razorpay Test Mode is reachable and live money is impossible.  If the client
      cannot be built, execution is refused rather than mocked.
    - ``UNCONFIGURED``: some Razorpay credential is present but the set is
      incomplete or unrecognised (key without secret, secret without key, or an
      unexpected key prefix).  Nothing is silently mocked behind a half-configured
      deployment.
    - ``MOCK``: no Razorpay credentials at all.  This is the demo path: recovery
      actions produce ``mock.invalid`` links and make no provider call.

    The key-id prefix is re-checked here as well as during settings validation so
    that any settings object -- including one built without validation, e.g. in a
    test double -- still cannot report a live key as Test Mode.
    """
    prefix = key_id_prefix(key_id)
    has_secret = isinstance(key_secret, str) and bool(key_secret)

    if live_key_rejected or prefix.startswith(LIVE_KEY_PREFIX):
        return PAYMENT_MODE_LIVE_REJECTED

    if prefix.startswith(TEST_KEY_PREFIX) and has_secret:
        return PAYMENT_MODE_TEST

    if not prefix and not has_secret:
        return PAYMENT_MODE_MOCK

    return PAYMENT_MODE_UNCONFIGURED


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
    # Set by _reject_live_credentials below.  It records that a live key was
    # supplied and discarded, so the status endpoint can report LIVE_REJECTED
    # instead of falling back to MOCK once the key has been blanked.
    RAZORPAY_LIVE_KEY_REJECTED: bool = False

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

    # Demonstration authentication (shared secret, one key, one actor).
    # Unauthenticated access is refused (401); an unset key refuses with 503.
    # The value is never logged or audited.
    DEMO_API_KEY: str = ""

    @model_validator(mode="after")
    def _reject_live_credentials(self):
        """
        Refuse Razorpay live credentials at configuration time.

        A live key is discarded together with its secret, so no later code path
        can build a provider client or create a live payment link from it even if
        it forgets to check the prefix.  The refusal is recorded so the status
        endpoint can report LIVE_REJECTED rather than silently looking MOCK.
        """
        if key_id_prefix(self.RAZORPAY_KEY_ID).startswith(LIVE_KEY_PREFIX):
            self.RAZORPAY_KEY_ID = ""
            self.RAZORPAY_KEY_SECRET = ""
            self.RAZORPAY_LIVE_KEY_REJECTED = True
            logger.error(
                "Refusing Razorpay live credentials: RAZORPAY_KEY_ID starts with "
                "'%s'. This application runs against Razorpay Test Mode only. The "
                "live key and secret were discarded; no payment client will be "
                "created and no payment link can be generated.",
                LIVE_KEY_PREFIX,
            )
        return self

    @property
    def payment_mode(self) -> str:
        """One of UNCONFIGURED / MOCK / TEST_MODE / LIVE_REJECTED."""
        return resolve_payment_mode(
            self.RAZORPAY_KEY_ID,
            self.RAZORPAY_KEY_SECRET,
            self.RAZORPAY_LIVE_KEY_REJECTED,
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
