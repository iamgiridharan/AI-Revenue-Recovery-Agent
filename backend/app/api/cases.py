from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.case_service import get_cases, get_case_by_id
from app.schemas.case import CaseListResponse, CaseDetailResponse
from app.utils.errors import NotFoundError

router = APIRouter(tags=["cases"])


@router.get("/cases", response_model=CaseListResponse)
def list_cases(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by status"),
    priority: str | None = Query(None, description="Filter by priority"),
    min_risk_score: float | None = Query(None, ge=0, le=1, description="Minimum risk score"),
    max_risk_score: float | None = Query(None, ge=0, le=1, description="Maximum risk score"),
    db: Session = Depends(get_db),
):
    """
    List revenue risk cases with pagination and filtering.

    Supports filtering by:
    - status: OPEN, IN_PROGRESS, RECOVERY_ATTEMPTED, RECOVERED, FAILED, ESCALATED, CLOSED
    - priority: LOW, MEDIUM, HIGH, CRITICAL
    - risk_score range: 0.0 to 1.0
    """
    cases, total = get_cases(
        db=db,
        page=page,
        page_size=page_size,
        status=status,
        priority=priority,
        min_risk_score=min_risk_score,
        max_risk_score=max_risk_score,
    )

    total_pages = (total + page_size - 1) // page_size

    return CaseListResponse(
        success=True,
        data=cases,
        pagination={
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    )


@router.get("/cases/{case_id}", response_model=CaseDetailResponse)
def get_case(case_id: str, db: Session = Depends(get_db)):
    """
    Get detailed information about a specific revenue risk case.

    Includes case details and related entities.
    """
    case = get_case_by_id(db=db, case_id=case_id)

    return CaseDetailResponse(
        success=True,
        data=case,
    )
