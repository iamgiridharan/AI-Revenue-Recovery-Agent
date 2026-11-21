"""
Razorpay Test Mode Service for Revenue Recovery.

This service handles:
- Payment Link creation via Razorpay Test Mode API
- Webhook signature verification
- Payment status retrieval

CRITICAL: This service ONLY uses Razorpay Test Mode.
NEVER use Razorpay Live Mode in this codebase.  A live key is refused at
configuration time and again here: it can neither initialise a client nor
create a payment link.

Payment modes and what they allow:

- MOCK (no credentials at all): a clearly labelled local placeholder link.
- TEST_MODE (complete rzp_test_ key + secret): the Razorpay Test Mode API.  If
  the SDK is missing or the client cannot be built, execution is refused --
  a mock link is never returned while the deployment reports TEST_MODE.
- LIVE_REJECTED (live key supplied): refused, no link of any kind.
- UNCONFIGURED (partial or unrecognised credentials): refused -- a
  half-configured deployment must not silently fall back to mock links.
"""
import hashlib
import hmac
import json
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.core.config import (
    LIVE_KEY_PREFIX,
    PAYMENT_MODE_LIVE_REJECTED,
    PAYMENT_MODE_MOCK,
    PAYMENT_MODE_TEST,
    PAYMENT_MODE_UNCONFIGURED,
    TEST_KEY_PREFIX,
    get_settings,
    key_id_prefix,
    resolve_payment_mode,
)
from app.utils.logging import logger

settings = get_settings()

# Razorpay client instance (initialized lazily)
_razorpay_client = None
# Why the last client initialization attempt failed, as a short phrase.  Never the
# exception text: an SDK error can echo the configuration it was handed.
_razorpay_client_error: Optional[str] = None


class PaymentProviderUnavailableError(RuntimeError):
    """
    Base class: the payment provider cannot be used as configured.

    ``code`` is the failure code returned to the caller and ``audit_decision`` the
    value recorded on the audit event, so a refusal is always recorded with the
    reason it happened instead of looking like a generic execution failure.
    """

    code = "PAYMENT_PROVIDER_UNAVAILABLE"
    audit_decision = "PAYMENT_PROVIDER_UNAVAILABLE"


class LiveModeRejectedError(PaymentProviderUnavailableError):
    """
    Raised when Razorpay live credentials are configured.

    The project may only reach Test Mode.  This error means a live key was
    supplied: it has been refused, so no provider client exists and no payment
    link is created (not even a mock one, which would misrepresent the state).
    """

    code = "LIVE_MODE_REJECTED"
    audit_decision = "LIVE_MODE_REJECTED"


class PaymentConfigurationError(PaymentProviderUnavailableError):
    """
    Raised when Razorpay credentials are partially configured or unrecognised.

    Reaching Test Mode requires a complete ``rzp_test_`` key + secret pair.  A
    half-configured deployment is refused rather than quietly mocked: mock links
    from a deployment that looks configured would misrepresent what happened.
    """

    code = "PAYMENT_UNCONFIGURED"
    audit_decision = "PAYMENT_UNCONFIGURED"


class PaymentProviderInitError(PaymentProviderUnavailableError):
    """
    Raised when complete Test Mode credentials are configured but no client exists.

    The SDK may be missing, client construction may fail, or the client may be
    unavailable for another reason.  Execution is refused: a deployment reporting
    TEST_MODE must never fall back to a mock link.
    """

    code = "PAYMENT_PROVIDER_UNAVAILABLE"
    audit_decision = "PAYMENT_PROVIDER_UNAVAILABLE"


def _unconfigured_reason() -> str:
    """
    Say what is wrong with the credential set, without echoing any of it.

    Only the shape of the configuration is reported -- which variable is missing,
    or that the prefix is unsupported.  The key id and secret values never appear.
    """
    prefix = key_id_prefix(getattr(settings, "RAZORPAY_KEY_ID", ""))
    secret = getattr(settings, "RAZORPAY_KEY_SECRET", "")
    has_secret = isinstance(secret, str) and bool(secret)

    if prefix and not has_secret:
        return "RAZORPAY_KEY_ID is set but RAZORPAY_KEY_SECRET is missing"
    if has_secret and not prefix:
        return "RAZORPAY_KEY_SECRET is set but RAZORPAY_KEY_ID is missing"
    return (
        f"RAZORPAY_KEY_ID does not use the supported '{TEST_KEY_PREFIX}' "
        "Test Mode prefix"
    )


def _unconfigured_message() -> str:
    """Actionable error text for a half-configured or unrecognised key set."""
    return (
        "Payment provider is not configured (mode: UNCONFIGURED): "
        f"{_unconfigured_reason()}. Set a complete Razorpay Test Mode "
        f"RAZORPAY_KEY_ID ('{TEST_KEY_PREFIX}...') and RAZORPAY_KEY_SECRET, or "
        "unset both to run in MOCK mode. No payment link was created and no "
        "provider call was made."
    )


def _provider_unavailable_message() -> str:
    """Actionable error text for Test Mode credentials with no usable client."""
    reason = _razorpay_client_error or "the provider client is unavailable"
    return (
        "Payment provider is unavailable (mode: TEST_MODE): complete Razorpay "
        f"Test Mode credentials are configured but {reason}. Install the Razorpay "
        "SDK or check the key pair, or unset both RAZORPAY_KEY_ID and "
        "RAZORPAY_KEY_SECRET to run in MOCK mode. No payment link was created "
        "and no mock link was substituted."
    )


def _payment_mode() -> str:
    """Current payment mode, safe against partially-shaped settings doubles."""
    return resolve_payment_mode(
        getattr(settings, "RAZORPAY_KEY_ID", ""),
        getattr(settings, "RAZORPAY_KEY_SECRET", ""),
        bool(getattr(settings, "RAZORPAY_LIVE_KEY_REJECTED", False)),
    )


def get_razorpay_client():
    """
    Get or initialize the Razorpay client.

    A client is only ever built for a complete ``rzp_test_`` key + secret pair.
    Live credentials are refused here as well as during settings validation, so
    no code path can hand the SDK a live key.

    Returns:
        Razorpay client instance or None when no provider may be reached
    """
    global _razorpay_client, _razorpay_client_error
    
    if _razorpay_client is not None:
        return _razorpay_client

    mode = _payment_mode()

    if mode == PAYMENT_MODE_LIVE_REJECTED:
        logger.error(
            "Refusing to initialize a Razorpay client: live credentials were "
            "supplied ('%s' key prefix or RAZORPAY_LIVE_KEY_REJECTED is set). "
            "Only Test Mode keys are accepted; no provider call will be made.",
            LIVE_KEY_PREFIX,
        )
        _razorpay_client = None
        return None

    # Defense in depth: even a validated mode is re-checked against the prefix
    # before the SDK is constructed, so this function is safe on its own.
    if mode != PAYMENT_MODE_TEST or not key_id_prefix(
        getattr(settings, "RAZORPAY_KEY_ID", "")
    ).startswith(TEST_KEY_PREFIX):
        if mode == PAYMENT_MODE_UNCONFIGURED:
            logger.error(
                "Refusing to initialize a Razorpay client: credentials are "
                "partially configured or unrecognised (mode: %s). %s. Payment "
                "execution will be refused.",
                mode,
                _unconfigured_reason(),
            )
        else:
            logger.warning(
                "No Razorpay credentials configured (mode: %s). Payment links "
                "will be mocked and no provider call will be made.",
                mode,
            )
        return None
    
    # Recorded so a refusal can say what to fix.  Only a fixed phrase per failure
    # kind is stored: exception text can echo the configuration it was given.
    _razorpay_client_error = None

    try:
        import razorpay
    except ImportError:
        _razorpay_client_error = "the 'razorpay' package is not installed"
        logger.error(
            "Cannot initialize a Razorpay client: the 'razorpay' package is not "
            "installed. Payment execution will be refused."
        )
        return None

    try:
        _razorpay_client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
        logger.info(
            "Razorpay client initialized (Test Mode): only test-mode objects can "
            "be created, live money cannot move through this client"
        )
        return _razorpay_client
    except Exception as e:
        _razorpay_client_error = "client construction failed"
        logger.error(
            "Failed to initialize Razorpay client (%s). Payment execution will be "
            "refused; no credential values are logged.",
            type(e).__name__,
        )
        return None


def create_payment_link(
    amount: float,
    currency: str = "INR",
    description: str = "Revenue Recovery Payment",
    customer_email: Optional[str] = None,
    customer_phone: Optional[str] = None,
    reference_id: Optional[str] = None,
    expiry_days: int = 1,
    notes: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Create a Razorpay Payment Link.
    
    Args:
        amount: Payment amount in INR
        currency: Currency code (default: INR)
        description: Payment description
        customer_email: Customer email address
        customer_phone: Customer phone number
        reference_id: Unique reference ID for the payment
        expiry_days: Number of days until link expires
        notes: Key/value metadata copied onto the link.  Reaches the payment and
            therefore the webhook, which is how a delivery is associated with
            the right revenue risk case.
        
    Returns:
        Dictionary containing payment link details
        
    Raises:
        LiveModeRejectedError: If live credentials are configured (refused).
        PaymentConfigurationError: If the credentials are incomplete or use an
            unsupported prefix (refused -- no mock fallback).
        PaymentProviderInitError: If Test Mode credentials are configured but no
            provider client exists (refused -- no mock fallback).
        Exception: If Razorpay API call fails
    """
    mode = _payment_mode()

    # Live credentials are refused before anything else: no provider call and no
    # mock link, because a mock link on a live-configured deployment would
    # misrepresent what happened.
    if mode == PAYMENT_MODE_LIVE_REJECTED:
        raise LiveModeRejectedError(
            "Razorpay live credentials are configured but refused: this "
            "application runs against Test Mode only. No payment link was created "
            "and no provider call was made."
        )

    # Partial or unrecognised credentials are refused as well.  This deployment
    # was configured for a provider but cannot reach one; silently returning a
    # mock link would present an unconfigured system as a working one.
    if mode == PAYMENT_MODE_UNCONFIGURED:
        raise PaymentConfigurationError(_unconfigured_message())

    client = get_razorpay_client()

    if client is None:
        # Complete Test Mode credentials are configured, so a provider link is the
        # only acceptable outcome: a mock link here would report TEST_MODE while
        # handing back something that can never be paid.
        if mode == PAYMENT_MODE_TEST:
            raise PaymentProviderInitError(_provider_unavailable_message())

        # MOCK is the only mode that may produce a placeholder link: no
        # credentials were supplied at all, so there is no provider state for a
        # mock link to misrepresent.
        return _create_mock_payment_link(
            amount=amount,
            currency=currency,
            description=description,
            reference_id=reference_id,
        )
    
    try:
        # Convert amount to paise (Razorpay expects amount in smallest currency unit)
        amount_paise = int(amount * 100)
        
        # Prepare payment link payload
        payload = {
            "amount": amount_paise,
            "currency": currency,
            "description": description,
            "reference_id": reference_id or f"recovery_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "callback_url": f"{settings.CORS_ORIGINS[0]}/payment/callback" if settings.CORS_ORIGINS else None,
            "callback_method": "get",
        }

        if notes:
            payload["notes"] = notes
        
        # Add customer details if provided
        if customer_email or customer_phone:
            payload["customer"] = {}
            if customer_email:
                payload["customer"]["email"] = customer_email
            if customer_phone:
                payload["customer"]["contact"] = customer_phone
        
        # Create payment link via Razorpay API
        response = client.payment_link.create(payload)
        
        logger.info(f"Payment link created: {response.get('id')} for amount {amount} {currency}")
        
        return {
            "success": True,
            "payment_link_id": response.get("id"),
            "payment_link": response.get("short_url"),
            "amount": amount,
            "currency": currency,
            "reference_id": payload["reference_id"],
            "status": response.get("status"),
            "created_at": response.get("created_at"),
            "expires_at": response.get("expires_by"),
            # Test Mode credentials were used for this call: the link is a real
            # Razorpay object, but no live money can move through it.
            "provider": "razorpay_test_mode",
            "mock": False,
        }
        
    except Exception as e:
        logger.error(f"Failed to create payment link: {e}")
        raise Exception(f"Razorpay API error: {str(e)}")


def _create_mock_payment_link(
    amount: float,
    currency: str = "INR",
    description: str = "Revenue Recovery Payment",
    reference_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a mock payment link for testing when Razorpay is not configured.
    
    This is only reachable in MOCK mode, where no Razorpay credentials are set at
    all.  A partially configured deployment is refused before this point, and the
    mode is re-checked here so a mock link can never be produced under TEST_MODE.
    """
    mode = _payment_mode()
    if mode != PAYMENT_MODE_MOCK:
        raise PaymentProviderUnavailableError(
            f"Refusing to create a mock payment link in {mode} mode: a mock link "
            "is only valid when no Razorpay credentials are configured."
        )

    import uuid
    
    mock_id = f"plink_{uuid.uuid4().hex[:16]}"
    mock_ref = reference_id or f"recovery_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    
    logger.info(f"MOCK: Created payment link {mock_id} for amount {amount} {currency}")
    
    return {
        "success": True,
        "payment_link_id": mock_id,
        # Deliberately not an rzp.io URL: a mock link must be impossible to mistake
        # for a real Razorpay payment link (the .invalid TLD can never resolve).
        "payment_link": f"https://mock.invalid/recovery/{mock_id}",
        "amount": amount,
        "currency": currency,
        "reference_id": mock_ref,
        "status": "created",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": None,
        # No provider call was made: this link is a local placeholder, not a
        # Razorpay payment link, and it must never be presented as one.
        "provider": "mock",
        "mock": True,
    }


def verify_webhook_signature(
    payload: str,
    signature: str,
    secret: Optional[str] = None,
) -> bool:
    """
    Verify Razorpay webhook signature.
    
    Args:
        payload: Raw request body as string
        signature: X-Razorpay-Signature header value
        secret: Webhook secret (uses env config if not provided)
        
    Returns:
        True if signature is valid, False otherwise

    Verification FAILS CLOSED: if no webhook secret is configured the request is
    rejected instead of trusted, because an unverified webhook can mark cases as
    recovered with an attacker-chosen amount. Configure
    ``RAZORPAY_WEBHOOK_SECRET`` to accept webhook deliveries.
    """
    webhook_secret = secret or settings.RAZORPAY_WEBHOOK_SECRET
    
    if not webhook_secret:
        logger.error(
            "Webhook secret is not configured. Rejecting webhook: signature "
            "cannot be verified. Set RAZORPAY_WEBHOOK_SECRET to enable webhooks."
        )
        return False

    if not signature:
        logger.warning("Webhook request has no X-Razorpay-Signature header. Rejecting.")
        return False
    
    try:
        # Razorpay uses HMAC SHA256 for signature verification
        expected_signature = hmac.new(
            webhook_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        
        is_valid = hmac.compare_digest(expected_signature, signature)
        
        if not is_valid:
            logger.warning("Webhook signature verification failed")
        
        return is_valid
        
    except Exception as e:
        logger.error(f"Webhook signature verification error: {e}")
        return False


def parse_webhook_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse a Razorpay webhook event.
    
    Args:
        payload: Raw webhook payload from Razorpay
        
    Returns:
        Parsed event data with type and details
    """
    try:
        event = payload.get("event", "")
        entities = payload.get("payload", {}) or {}
        entity = (entities.get("payment") or {}).get("entity") or {}
        # payment_link.* events carry the case reference on the payment_link
        # entity, not on the payment entity.
        link_entity = (entities.get("payment_link") or {}).get("entity") or {}

        entity_amount = entity.get("amount")
        link_amount = link_entity.get("amount")
        if entity_amount is not None:
            amount = entity_amount / 100  # Convert from paise to INR
        elif link_amount is not None:
            amount = link_amount / 100
        else:
            amount = 0
        
        # Extract payment details
        payment_data = {
            "event_type": event,
            "payment_id": entity.get("id"),
            "order_id": entity.get("order_id"),
            "amount": amount,
            "currency": entity.get("currency") or link_entity.get("currency"),
            "status": entity.get("status") or link_entity.get("status"),
            "method": entity.get("method"),
            "email": entity.get("email"),
            "contact": entity.get("contact"),
            "created_at": entity.get("created_at") or link_entity.get("created_at"),
            "captured": entity.get("captured"),
            "description": entity.get("description") or link_entity.get("description"),
            # reference_id is what maps a webhook back to a revenue risk case.
            "reference_id": entity.get("reference_id") or link_entity.get("reference_id"),
            "payment_link_id": link_entity.get("id"),
            "payment_link_status": link_entity.get("status"),
            "notes": {
                **(link_entity.get("notes") or {}),
                **(entity.get("notes") or {}),
            },
        }
        
        logger.info(f"Parsed webhook event: {event} for payment {payment_data['payment_id']}")
        
        return payment_data
        
    except Exception as e:
        logger.error(f"Failed to parse webhook event: {e}")
        return {"event_type": "unknown", "error": str(e)}


def get_payment_status(payment_id: str) -> Dict[str, Any]:
    """
    Get payment status from Razorpay.
    
    Args:
        payment_id: Razorpay payment ID
        
    Returns:
        Payment status details
    """
    mode = _payment_mode()

    if mode == PAYMENT_MODE_LIVE_REJECTED:
        return {
            "success": False,
            "error": ("Payment provider unavailable: Razorpay live credentials "
                      "are configured but refused (Test Mode only)"),
            "mock": False,
        }

    if mode == PAYMENT_MODE_UNCONFIGURED:
        return {
            "success": False,
            "error": _unconfigured_message(),
            "mock": False,
        }

    client = get_razorpay_client()

    if client is None:
        if mode == PAYMENT_MODE_TEST:
            return {
                "success": False,
                "error": _provider_unavailable_message(),
                "mock": False,
            }

        return {
            "success": False,
            "error": "Razorpay client not configured",
            "mock": True,
        }
    
    try:
        payment = client.payment.fetch(payment_id)
        
        return {
            "success": True,
            "payment_id": payment.get("id"),
            "status": payment.get("status"),
            "amount": payment.get("amount", 0) / 100,
            "currency": payment.get("currency"),
            "captured": payment.get("captured"),
            "raw_response": payment,
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch payment status: {e}")
        return {
            "success": False,
            "error": str(e),
        }
