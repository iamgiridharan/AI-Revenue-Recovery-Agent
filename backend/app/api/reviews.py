"""
Human review API endpoints.

An ESCALATED case is waiting for a human decision: the Policy Engine refused to
authorize the action on its own, so the case cannot move until a named reviewer
approves, overrides or closes it.
"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.review import CaseReviewRequest
from app.services.review_service import get_review_queue, review_case

router = APIRouter(tags=["reviews"])


@router.get("/reviews/queue")
def review_queue_endpoint(
    limit: int = Query(100, ge=1, le=200, description="Maximum cases to return"),
    db: Session = Depends(get_db),
):
    """
    List the escalated cases awaiting human review.

    Each entry carries the policy rule that escalated it and the recorded AI
    recommendation, so a reviewer can decide without opening every case.  This
    endpoint is read-only: it makes no decisions.
    """
    return {"success": True, "data": get_review_queue(db, limit=limit)}


@router.post("/cases/{case_id}/review", response_model=None)
def review_case_endpoint(
    case_id: str,
    request: CaseReviewRequest,
    db: Session = Depends(get_db),
):
    """
    Record a named reviewer's decision on an escalated case.

    - ``APPROVE`` executes the recorded AI recommendation. The Policy Engine is
      re-run with the reviewer named as the authorizing human, so the autonomy
      thresholds that caused the escalation no longer stop it.
    - ``OVERRIDE`` does the same for a different executable action and requires a
      written reason.
    - ``CLOSE`` / ``MARK_UNRECOVERABLE`` close the case without recovering it. A
      RECOVERED case is never closed: settled money is not relabelled.

    An approval is not a bypass: hard policy stops (settled or expired case,
    exhausted retry / reminder / attempt budget) still refuse the action, the
    response says so, and the case stays ESCALATED. Every decision - including a
    refusal - is recorded as a HUMAN_REVIEW_COMPLETED audit event whose actor is
    the reviewer.

    The amount always comes from the failed transaction record and the confidence
    / recovery probability always from the recorded diagnosis; the request can
    never supply them.
    """
    result = review_case(
        db=db,
        case_id=case_id,
        reviewer=request.reviewer,
        decision=request.decision,
        action_type=request.action_type,
        note=request.note,
    )

    if result.get("success"):
        return result

    status_code = result.pop("http_status", 400)
    payload = {"success": False, "error": {"code": result["code"],
                                           "message": result["error"]}}
    # Carry the review context through on refusals too: the caller needs to know
    # the decision was recorded and what the case status is now.
    payload.update({key: value for key, value in result.items()
                    if key not in ("success", "code", "error")})
    return JSONResponse(status_code=status_code, content=payload)
