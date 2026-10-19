"""
Razorpay Test Mode Service for Revenue Recovery.

This service handles:
- Payment Link creation via Razorpay Test Mode API
- Webhook signature verification
- Payment status retrieval

CRITICAL: This service ONLY uses Razorpay Test Mode.
NEVER use Razorpay Live Mode in this codebase.
"""
import hashlib
import hmac
import json
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from app.core.config import get_settings
from app.utils.logging import logger

settings = get_settings()

# Razorpay client instance (initialized lazily)
_razorpay_client = None


def get_razorpay_client():
    """
    Get or initialize the Razorpay client.
    
    Returns:
        Razorpay client instance or None if not configured
        
    Raises:
        ValueError: If Razorpay credentials are not configured
    """
    global _razorpay_client
    
    if _razorpay_client is not None:
        return _razorpay_client
    
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        logger.warning("Razorpay credentials not configured. Payment operations will be mocked.")
        return None
    
    try:
        import razorpay
        _razorpay_client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        logger.info("Razorpay client initialized successfully (Test Mode)")
        return _razorpay_client
    except Exception as e:
        logger.error(f"Failed to initialize Razorpay client: {e}")
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
        Exception: If Razorpay API call fails
    """
    client = get_razorpay_client()
    
    # If no client (not configured), use mock mode
    if client is None:
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
    
    This is used for development/testing when Razorpay credentials are not set.
    """
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
    client = get_razorpay_client()
    
    if client is None:
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
