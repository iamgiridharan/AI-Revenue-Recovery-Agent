from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.services.case_service import get_cases, get_case_by_id
from app.services.value_service import assess_case_value_by_id
from app.services.recovery_service import execute_recovery_action, get_recorded_diagnosis
from app.schemas.case import CaseListResponse, CaseDetailResponse
from app.schemas.recovery import EXECUTABLE_ACTIONS, RecoveryExecutionRequest
from app.utils.errors import AppError, NotFoundError
from app.utils.logging import logger

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


@router.get("/cases/{case_id}/value")
def get_case_value(case_id: str, db: Session = Depends(get_db)):
    """
    Estimate the recoverable value of a case (value-aware recovery scoring).

    Returns the formula, inputs with their provenance, the estimated gross
    recovery, the (separately inspectable) intervention cost, the estimated net
    recovery and the separately labelled utility score, plus the ranked eligible
    candidate actions and the excluded ones with reasons.

    This endpoint is READ-ONLY and advisory: it ranks candidates and never
    authorizes or executes anything. Every monetary figure is an estimate, not
    booked revenue, and the Policy Engine remains the authorization authority.
    """
    try:
        assessment = assess_case_value_by_id(db, case_id)
    except ValueError as exc:
        # A malformed stored value (e.g. a probability outside [0, 1]) is a
        # client-visible data problem, not a server crash.
        raise AppError(message=f"Cannot assess case {case_id}: {exc}", status_code=422)

    if assessment is None:
        raise NotFoundError(f"Revenue risk case '{case_id}' not found")

    return {
        "success": True,
        "data": assessment,
    }


# Failure codes from the recovery service mapped to the HTTP status that
# describes them: a refusal is not a server fault, and a provider fault is not a
# client error.
_RECOVERY_FAILURE_STATUS = {
    "CASE_NOT_FOUND": 404,
    "TRANSACTION_NOT_FOUND": 409,
    "CASE_ALREADY_RECOVERED": 409,
    "CASE_CLOSED": 409,
    "POLICY_BLOCKED": 403,
    "POLICY_ESCALATED": 403,
    "UNSUPPORTED_ACTION": 400,
    "EXECUTION_FAILED": 502,
}


@router.post("/cases/{case_id}/recovery", response_model=None)
def execute_case_recovery(
    case_id: str,
    request: RecoveryExecutionRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    """
    Execute an approved recovery action for a case (Phase 4: Test Mode).

    Flow: recorded AI recommendation → deterministic Policy Engine → Razorpay
    Test Mode payment link → recovery action + audit event.

    - The action defaults to the case's recorded AI recommendation; the
      confidence and recovery probability are always the recorded diagnosis
      values, never caller input.
    - The Policy Engine authorizes the action before any provider call. A BLOCKED
      or ESCALATED decision (escalated cases await human review) stops here with
      403 and the full decision, and no provider call is made.
    - RECOVERED and CLOSED cases are refused (409): a settled case is not acted on
      again.
    - Without Razorpay Test Mode credentials there is no provider call at all.
      The execution is then labelled ``execution_mode: MOCK`` and is not a
      payment: the placeholder link cannot receive money.

    Amounts always come from the database transaction record. Secrets are never
    returned: only the link, its id, amount, mode and the policy decision.
    """
    case = get_case_by_id(db=db, case_id=case_id)

    diagnosis = get_recorded_diagnosis(db, case)
    if diagnosis is None:
        raise AppError(
            message=(f"Case {case_id} has no recorded AI diagnosis; run "
                     f"POST /api/agent/diagnose first"),
            status_code=409,
        )

    action_type = (request.action_type if request else None) or diagnosis["action_type"]
    if action_type not in EXECUTABLE_ACTIONS:
        raise AppError(
            message=(f"Recorded recommendation '{action_type}' is not an executable "
                     f"recovery action; choose one of: "
                     f"{', '.join(sorted(EXECUTABLE_ACTIONS))}"),
            status_code=409,
        )

    logger.info(
        f"Recovery execution requested for case {case_id}: {action_type} "
        f"(confidence {diagnosis['confidence']}, probability "
        f"{diagnosis['recovery_probability']})"
    )

    result = execute_recovery_action(
        db=db,
        case_id=case_id,
        action_type=action_type,
        confidence=diagnosis["confidence"],
        recovery_probability=diagnosis["recovery_probability"],
        reason=(request.reason if request and request.reason else
                "Executed from recorded AI recommendation"),
    )

    if result.get("success"):
        return {
            "success": True,
            "data": {
                "case_id": case_id,
                "action_type": action_type,
                "amount": result.get("amount"),
                "currency": result.get("currency"),
                "payment_link_id": result.get("payment_link_id"),
                "payment_link": result.get("payment_link"),
                "provider_status": result.get("provider_status"),
                "expires_at": result.get("expires_at"),
                # MOCK: no provider call was made, the link cannot be paid.
                # RAZORPAY_TEST_MODE: a real Razorpay Test Mode link was created.
                "execution_mode": result.get("execution_mode"),
                "mock": result.get("mock", False),
                "case_reference": result.get("case_reference"),
                "authorization": {
                    "required": True,
                    "authority": "policy_engine",
                    "decision": "APPROVED",
                },
            },
        }

    code = result.get("code", "EXECUTION_FAILED")
    status_code = _RECOVERY_FAILURE_STATUS.get(code, 400)

    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {
                "code": code,
                "message": result.get("error", "Recovery execution failed"),
            },
            "case_id": case_id,
            "action_type": action_type,
            "action_executed": False,
            # Present for policy refusals, so the caller can show exactly which
            # rule stopped the action.
            "policy_decision": result.get("policy_decision"),
        },
    )
