"""
Dashboard and Audit API Endpoints.

Provides read-only endpoints for the merchant dashboard frontend.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.dashboard_service import (
    get_dashboard_stats,
    get_recovery_by_status,
    get_recovery_by_priority,
    get_recovery_by_action,
    get_daily_cases,
    get_daily_recovered,
    get_case_detail_with_relations,
    get_audit_events,
)

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/stats")
def dashboard_stats(db: Session = Depends(get_db)):
    """Get aggregate dashboard statistics."""
    stats = get_dashboard_stats(db)
    return {"success": True, "data": stats}


@router.get("/dashboard/charts/status")
def dashboard_status_chart(db: Session = Depends(get_db)):
    """Get recovery counts grouped by status for charts."""
    data = get_recovery_by_status(db)
    return {"success": True, "data": data}


@router.get("/dashboard/charts/priority")
def dashboard_priority_chart(db: Session = Depends(get_db)):
    """Get recovery counts grouped by priority for charts."""
    data = get_recovery_by_priority(db)
    return {"success": True, "data": data}


@router.get("/dashboard/charts/actions")
def dashboard_actions_chart(db: Session = Depends(get_db)):
    """Get recovery outcomes grouped by action type."""
    data = get_recovery_by_action(db)
    return {"success": True, "data": data}


@router.get("/dashboard/charts/daily-cases")
def dashboard_daily_cases(
    days: int = Query(30, ge=1, le=90, description="Number of days"),
    db: Session = Depends(get_db),
):
    """Get daily case creation counts."""
    data = get_daily_cases(db, days=days)
    return {"success": True, "data": data}


@router.get("/dashboard/charts/daily-recovered")
def dashboard_daily_recovered(
    days: int = Query(30, ge=1, le=90, description="Number of days"),
    db: Session = Depends(get_db),
):
    """Get daily recovered revenue."""
    data = get_daily_recovered(db, days=days)
    return {"success": True, "data": data}


@router.get("/cases/{case_id}/detail")
def case_detail_full(case_id: str, db: Session = Depends(get_db)):
    """
    Get full case detail including customer, transaction,
    recovery actions, and audit events.
    """
    data = get_case_detail_with_relations(db, case_id)
    if data is None:
        return {"success": False, "error": {"message": f"Case {case_id} not found"}}
    return {"success": True, "data": data}


@router.get("/audit")
def list_audit_events(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    case_id: str = Query(None, description="Filter by case ID"),
    event_type: str = Query(None, description="Filter by event type"),
    actor: str = Query(None, description="Filter by actor"),
    db: Session = Depends(get_db),
):
    """Get paginated audit events."""
    events, total = get_audit_events(
        db=db,
        page=page,
        page_size=page_size,
        case_id=case_id,
        event_type=event_type,
        actor=actor,
    )
    total_pages = (total + page_size - 1) // page_size
    return {
        "success": True,
        "data": events,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }
