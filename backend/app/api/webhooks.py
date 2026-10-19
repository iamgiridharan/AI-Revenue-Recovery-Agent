"""
Webhook Handler for Razorpay Payment Events.

This handler processes Razorpay webhook events:
- payment.captured              → payment settled (marks the case recovered)
- payment_link.paid             → payment link settled (marks the case recovered)
- payment.failed                → failure
- payment_link.expired          → failure
- payment.authorized            → authorized but NOT captured: recorded, not settled
- anything else                 → acknowledged and recorded

Guarantees:
- Signature verification (fails closed: an unverified delivery is rejected)
- Idempotency: every handled delivery is recorded by its provider event id in the
  ``webhook_events`` table, so a retry (also after a restart or on another worker)
  is answered as a duplicate instead of being applied twice
- A delivery is only recorded as processed AFTER it has been handled, so a failed
  delivery stays retryable
- Only a *captured* payment can settle a case, and only for the amount that was due
- Settled cases (RECOVERED / CLOSED) are never moved by a later event
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Dict, Any, Optional
import json

from app.db.session import get_db
from app.services.razorpay_service import verify_webhook_signature, parse_webhook_event
from app.services.recovery_service import process_payment_success, process_payment_failure
from app.models import RevenueRiskCase, AuditEvent, AuditEventType, WebhookEvent
from app.utils.logging import logger

router = APIRouter(tags=["webhooks"])


# Events that mean "money is captured and settled".  ``payment.authorized`` is
# deliberately NOT here: an authorization is not a capture, so settling a case on
# it would book revenue that may never arrive.
SETTLING_EVENTS = ("payment.captured", "payment_link.paid")

# Events that report a payment attempt that did not complete.
FAILURE_EVENTS = ("payment.failed", "payment_link.expired")

# Authorized but not captured: recorded, and the case is left exactly as it is.
NOT_CAPTURED_EVENTS = ("payment.authorized",)


def _find_processed_event(db: Session, event_id: str) -> Optional[WebhookEvent]:
    """Return the ledger row for an already-handled delivery, if any."""
    return db.query(WebhookEvent).filter(WebhookEvent.event_id == event_id).first()


def _record_event(
    db: Session,
    event_id: str,
    event_type: str,
    payment_id: Optional[str],
    case_id: Optional[str],
    amount: Optional[float],
    result: str,
) -> None:
    """
    Record a handled delivery in the durable idempotency ledger.

    Called only after the delivery has been handled.  A concurrent delivery of
    the same event id loses the race on the unique constraint; that is a
    duplicate, not an error, so it is reported as one.
    """
    db.add(WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payment_id=payment_id,
        case_id=case_id,
        amount=amount,
        signature_verified=True,
        result=result,
    ))
    db.commit()


def _extract_event_id(payload: Dict[str, Any], header_event_id: str = "") -> str:
    """
    Extract unique event identifier from webhook payload.

    Args:
        payload: Webhook payload from Razorpay
        header_event_id: Value of the provider's X-Razorpay-Event-Id header

    Returns:
        Unique event identifier
    """
    # Prefer the provider's own event id: two distinct deliveries of the same
    # event type for the same payment are indistinguishable in the payload.
    if header_event_id:
        return header_event_id

    # Fall back to event + payment_id + created_at for uniqueness
    event_type = payload.get("event", "unknown")
    payload_data = payload.get("payload", {})
    payment_entity = payload_data.get("payment", {}).get("entity", {}) or {}
    link_entity = payload_data.get("payment_link", {}).get("entity", {}) or {}

    payment_id = payment_entity.get("id") or link_entity.get("id") or "unknown"
    created_at = (
        payment_entity.get("created_at")
        or link_entity.get("created_at")
        or "unknown"
    )

    return f"{event_type}:{payment_id}:{created_at}"


def _extract_case_reference(event_data: Dict[str, Any]) -> Optional[str]:
    """
    Resolve the revenue risk case a delivery belongs to.

    Payment entities carry the reference in ``notes.reference_id`` (Razorpay
    copies the payment link's notes onto the payment), payment_link entities
    carry it in ``reference_id``.  ``notes.case_id`` is the second, explicit
    channel; ``order_id`` is the last resort for manually created orders.
    """
    notes = event_data.get("notes", {}) or {}
    return (
        event_data.get("reference_id")
        or notes.get("reference_id")
        or notes.get("case_id")
        or event_data.get("order_id")
    )


@router.post("/webhooks/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Razorpay webhook events.

    This endpoint:
    1. Verifies webhook signature (fails closed)
    2. Rejects duplicate deliveries using the durable event ledger
    3. Identifies event type
    4. Extracts event data
    5. Validates status/amount and updates the Revenue Risk Case
    6. Records the verified outcome in the audit trail and the event ledger
    """
    try:
        # Get raw request body
        body = await request.body()
        payload_str = body.decode("utf-8")

        # Parse JSON payload
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        # Get signature from headers
        signature = request.headers.get("X-Razorpay-Signature", "")

        # Verify webhook signature (fails closed when no secret is configured)
        if not verify_webhook_signature(payload_str, signature):
            logger.warning("Webhook signature verification failed")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # Extract event ID for duplicate detection, preferring the provider's id
        header_event_id = request.headers.get("X-Razorpay-Event-Id", "").strip()
        event_id = _extract_event_id(payload, header_event_id)

        # Check for duplicate event (durable: survives restarts and other workers)
        already_handled = _find_processed_event(db, event_id)
        if already_handled is not None:
            logger.info(f"Duplicate webhook event ignored: {event_id}")
            return {
                "success": True,
                "message": "Duplicate event ignored",
                "event_id": event_id,
                "duplicate": True,
                "original_result": already_handled.result,
            }

        # Parse webhook event
        event_data = parse_webhook_event(payload)
        event_type = event_data.get("event_type", "")

        logger.info(f"Processing webhook event: {event_type}")

        reference_id = _extract_case_reference(event_data)
        payment_id = event_data.get("payment_id") or event_data.get("payment_link_id")
        amount = event_data.get("amount")

        if event_type in SETTLING_EVENTS:
            response, ledger_result = _handle_settling_event(
                db=db,
                event_id=event_id,
                event_type=event_type,
                payment_id=payment_id,
                amount=amount,
                reference_id=reference_id,
            )
        elif event_type in NOT_CAPTURED_EVENTS:
            response, ledger_result = _handle_not_captured_event(
                db=db,
                event_id=event_id,
                event_type=event_type,
                payment_id=payment_id,
                amount=amount,
                reference_id=reference_id,
            )
        elif event_type in FAILURE_EVENTS:
            response, ledger_result = _handle_failure_event(
                db=db,
                event_id=event_id,
                event_type=event_type,
                payment_id=payment_id,
                amount=amount,
                reference_id=reference_id,
                notes=event_data.get("notes", {}) or {},
            )
        else:
            # Unknown event type - log but don't fail
            logger.info(f"Unhandled webhook event type: {event_type}")
            response = {
                "success": True,
                "message": f"Event type {event_type} acknowledged",
                "event_id": event_id,
            }
            ledger_result = "UNHANDLED"

        # Only now, after the event has been handled, is it safe to treat this
        # delivery as processed (a failed delivery must remain retryable).
        try:
            _record_event(
                db=db,
                event_id=event_id,
                event_type=event_type or "unknown",
                payment_id=payment_id,
                case_id=reference_id,
                amount=amount,
                result=ledger_result,
            )
        except IntegrityError:
            # A concurrent delivery of the same event won the race. The effect
            # itself is idempotent (a settled case is never settled twice), so
            # this delivery is reported as the duplicate it is.
            db.rollback()
            logger.info(f"Concurrent duplicate webhook delivery: {event_id}")
            return {
                "success": True,
                "message": "Duplicate event ignored",
                "event_id": event_id,
                "duplicate": True,
            }

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


def _handle_settling_event(
    db: Session,
    event_id: str,
    event_type: str,
    payment_id: Optional[str],
    amount: Optional[float],
    reference_id: Optional[str],
):
    """Handle a delivery that reports captured money (payment.captured / payment_link.paid)."""
    if not reference_id:
        logger.warning(f"No case reference found in webhook event: {event_id}")
        return {
            "success": True,
            "message": "Event processed but no case reference found",
            "event_id": event_id,
        }, "NO_CASE_REFERENCE"

    result = process_payment_success(
        db=db,
        case_id=reference_id,
        payment_id=payment_id,
        amount=amount,
    )

    _create_webhook_audit_event(
        db=db,
        case_id=reference_id,
        event_type=event_type,
        payment_id=payment_id,
        result=result,
    )

    return {
        "success": True,
        "message": "Payment success processed",
        "event_id": event_id,
        "result": result,
    }, "PROCESSED"


def _handle_not_captured_event(
    db: Session,
    event_id: str,
    event_type: str,
    payment_id: Optional[str],
    amount: Optional[float],
    reference_id: Optional[str],
):
    """
    Handle payment.authorized.

    An authorization can still fail to capture, so it must not settle a case or
    move any money figure. The delivery is recorded for the audit trail only.
    """
    logger.info(
        f"Payment {payment_id} authorized for case {reference_id}; not captured, "
        f"case left unchanged"
    )
    if reference_id:
        _create_webhook_audit_event(
            db=db,
            case_id=reference_id,
            event_type=event_type,
            payment_id=payment_id,
            result={
                "success": True,
                "settled": False,
                "reason": "payment authorized but not captured",
                "amount": amount,
            },
        )

    return {
        "success": True,
        "message": "Authorized payment recorded; not captured, case unchanged",
        "event_id": event_id,
        "settled": False,
    }, "NOT_CAPTURED"


def _handle_failure_event(
    db: Session,
    event_id: str,
    event_type: str,
    payment_id: Optional[str],
    amount: Optional[float],
    reference_id: Optional[str],
    notes: Dict[str, Any],
):
    """Handle payment.failed / payment_link.expired."""
    if not reference_id:
        logger.warning(f"No case reference found in webhook event: {event_id}")
        return {
            "success": True,
            "message": "Event processed but no case reference found",
            "event_id": event_id,
        }, "NO_CASE_REFERENCE"

    failure_reason = notes.get("failure_reason", "Unknown")

    result = process_payment_failure(
        db=db,
        case_id=reference_id,
        payment_id=payment_id,
        failure_reason=failure_reason,
    )

    # Create audit event for webhook
    _create_webhook_audit_event(
        db=db,
        case_id=reference_id,
        event_type=event_type,
        payment_id=payment_id,
        result=result,
    )

    return {
        "success": True,
        "message": "Payment failure processed",
        "event_id": event_id,
        "result": result,
    }, "PROCESSED"


def _create_webhook_audit_event(
    db: Session,
    case_id: str,
    event_type: str,
    payment_id: str,
    result: Dict[str, Any],
):
    """Create an audit event for webhook processing."""
    try:
        case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()
        if case:
            audit_event = AuditEvent(
                case_id=case.id,
                event_type=AuditEventType.ACTION_EXECUTED.value,
                actor="webhook",
                decision=event_type,
                reason=f"Webhook event: {event_type} for payment {payment_id}",
                action="WEBHOOK_RECEIVED",
                result="PROCESSED",
                metadata_={
                    "payment_id": payment_id,
                    "event_type": event_type,
                    "result": result,
                },
            )
            db.add(audit_event)
            db.commit()
    except Exception as e:
        logger.error(f"Failed to create webhook audit event: {e}")
