from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models.revenue_risk_case import RevenueRiskCase
from app.models.enums import CaseStatus
from app.utils.errors import NotFoundError


def get_cases(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    priority: str | None = None,
    min_risk_score: float | None = None,
    max_risk_score: float | None = None,
) -> tuple[list[RevenueRiskCase], int]:
    """
    Get paginated list of revenue risk cases with optional filters.

    Returns:
        Tuple of (list of cases, total count)
    """
    query = db.query(RevenueRiskCase)

    # Apply filters
    if status:
        query = query.filter(RevenueRiskCase.status == status)
    if priority:
        query = query.filter(RevenueRiskCase.priority == priority)
    if min_risk_score is not None:
        query = query.filter(RevenueRiskCase.risk_score >= min_risk_score)
    if max_risk_score is not None:
        query = query.filter(RevenueRiskCase.risk_score <= max_risk_score)

    # Get total count before pagination
    total = query.count()

    # Apply pagination
    offset = (page - 1) * page_size
    cases = query.order_by(RevenueRiskCase.created_at.desc()).offset(offset).limit(page_size).all()

    return cases, total


def get_case_by_id(db: Session, case_id: str) -> RevenueRiskCase:
    """
    Get a single revenue risk case by its case_id.

    Raises:
        NotFoundError: If case is not found
    """
    case = db.query(RevenueRiskCase).filter(RevenueRiskCase.case_id == case_id).first()

    if not case:
        raise NotFoundError(f"Revenue risk case '{case_id}' not found")

    return case


def get_case_stats(db: Session) -> dict:
    """Get basic statistics about revenue risk cases."""
    total_cases = db.query(func.count(RevenueRiskCase.id)).scalar()
    open_cases = db.query(func.count(RevenueRiskCase.id)).filter(
        RevenueRiskCase.status == CaseStatus.OPEN
    ).scalar()
    total_at_risk = db.query(func.sum(RevenueRiskCase.amount)).scalar() or 0.0
    total_recovered = db.query(func.sum(RevenueRiskCase.recovered_amount)).scalar() or 0.0

    return {
        "total_cases": total_cases,
        "open_cases": open_cases,
        "total_at_risk": total_at_risk,
        "total_recovered": total_recovered,
    }
