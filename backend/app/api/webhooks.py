"""
Webhook Handler for Razorpay Payment Events.

This handler processes Razorpay webhook events:
- payment.authorized
- payment.captured
- payment.failed
- payment_link.paid
- payment_link.expired

Features:
- Webhook signature verification
- Duplicate event protection
- Proper event handling and database updates
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from sqlalchemy.orm import Session
from typing import Dict, Any
import json

from app.db.session import get_db
from app.services.razorpay_service import verify_webhook_signature, parse_webhook_event
from app.services.recovery_service import process_payment_success, process_payment_failure
from app.models import RevenueRiskCase, AuditEvent, AuditEventType
from app.utils.logging import logger

router = APIRouter(tags=["webhooks"])


# In-memory store for duplicate detection.
# NOTE: this is process-local and not persisted, so it does not protect against
# replays after a restart or across multiple workers. A persistent event ledger
# would require a new database table (schema change) and is out of scope here.
_processed_events = set()
MAX_EVENT_CACHE_SIZE = 10000


def _is_duplicate_event(event_id: str) -> bool:
    """
    Check if an event has already been processed.
    
    Args:
        event_id: Unique event identifier
        
    Returns:
        True if event is duplicate, False otherwise
    """
    return event_id in _processed_events


def _mark_event_processed(event_id: str) -> None:
    """
    Record an event as processed.

    This must only be called AFTER the event has been handled successfully.
    Marking a delivery before handling it turns a transient processing failure
    into permanent data loss, because the provider's retry is then discarded as
    a duplicate.

    Args:
        event_id: Unique event identifier
    """
    _processed_events.add(event_id)

    # Prevent memory leak by limiting cache size
    if len(_processed_events) > MAX_EVENT_CACHE_SIZE:
        # Remove oldest entries (simple approach - in production use Redis with TTL)
        _processed_events.clear()
        _processed_events.add(event_id)


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


@router.post("/webhooks/razorpay")
async def handle_razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Razorpay webhook events.
    
    This endpoint:
    1. Verifies webhook signature
    2. Prevents duplicate processing
    3. Identifies event type
    4. Extracts event data
    5. Updates transaction/payment status
    6. Updates Revenue Risk Case
    7. Creates audit event
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
        
        # Check for duplicate event
        if _is_duplicate_event(event_id):
            logger.info(f"Duplicate webhook event ignored: {event_id}")
            return {
                "success": True,
                "message": "Duplicate event ignored",
                "event_id": event_id,
            }
        
        # Parse webhook event
        event_data = parse_webhook_event(payload)
        event_type = event_data.get("event_type", "")
        
        logger.info(f"Processing webhook event: {event_type}")

        # Case reference: payment entities carry it in notes.reference_id,
        # payment_link entities carry it in reference_id.
        notes = event_data.get("notes", {}) or {}
        reference_id = (
            event_data.get("reference_id")
            or notes.get("reference_id")
            or event_data.get("order_id")
        )
        
        # Handle different event types
        if event_type in ["payment.authorized", "payment.captured", "payment_link.paid"]:
            # Payment successful
            payment_id = event_data.get("payment_id") or event_data.get("payment_link_id")
            amount = event_data.get("amount", 0)
            
            if reference_id:
                result = process_payment_success(
                    db=db,
                    case_id=reference_id,
                    payment_id=payment_id,
                    amount=amount,
                )
                
                # Create audit event for webhook
                _create_webhook_audit_event(
                    db=db,
                    case_id=reference_id,
                    event_type=event_type,
                    payment_id=payment_id,
                    result=result,
                )
                
                response = {
                    "success": True,
                    "message": "Payment success processed",
                    "event_id": event_id,
                    "result": result,
                }
            else:
                logger.warning(f"No reference_id found in webhook event: {event_id}")
                response = {
                    "success": True,
                    "message": "Event processed but no case reference found",
                    "event_id": event_id,
                }
        
        elif event_type in ["payment.failed", "payment_link.expired"]:
            # Payment failed
            payment_id = event_data.get("payment_id") or event_data.get("payment_link_id")
            failure_reason = notes.get("failure_reason", "Unknown")
            
            if reference_id:
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
                
                response = {
                    "success": True,
                    "message": "Payment failure processed",
                    "event_id": event_id,
                    "result": result,
                }
            else:
                logger.warning(f"No reference_id found in webhook event: {event_id}")
                response = {
                    "success": True,
                    "message": "Event processed but no case reference found",
                    "event_id": event_id,
                }
        
        else:
            # Unknown event type - log but don't fail
            logger.info(f"Unhandled webhook event type: {event_type}")
            response = {
                "success": True,
                "message": f"Event type {event_type} acknowledged",
                "event_id": event_id,
            }

        # Only now, after the event has been handled, is it safe to treat this
        # delivery as processed (a failed delivery must remain retryable).
        _mark_event_processed(event_id)

        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}")
        raise HTTPException(status_code=500, detail="Webhook processing failed")


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
