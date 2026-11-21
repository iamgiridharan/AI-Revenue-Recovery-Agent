"""
AI Agent API Endpoints.

Provides endpoints for:
- Diagnosing revenue risk cases
- Getting agent recommendations
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.auth import require_auth
from app.db.session import get_db
from app.schemas.agent import AgentDiagnosisRequest
from app.services.agent_service import diagnose_case

router = APIRouter(tags=["agent"])


@router.post(
    "/agent/diagnose",
)
def diagnose_case_endpoint(
    request: AgentDiagnosisRequest,
    db: Session = Depends(get_db),
    _auth: str = Depends(require_auth),
):
    """
    Diagnose a revenue risk case.

    This endpoint:
    1. Gathers context about the case
    2. Produces a diagnosis and recommendation
    3. Validates structured output
    4. Records audit events
    5. Returns validated recommendation with its provenance

    The diagnosis is produced by a deterministic rule engine in this build (no
    language model runs), which is reported explicitly: ``diagnosis_source``,
    ``llm_used``, ``model_used`` and the probability/confidence sources are part
    of the response.  The agent NEVER directly executes payment operations; it
    produces recommendations that go through the Policy Engine.
    """
    result = diagnose_case(db, request.case_id)
    
    if not result["success"]:
        return JSONResponse(
            status_code=404 if "not found" in str(result.get("error", {})).lower() else 500,
            content={
                "success": False,
                "error": result.get("error", {"message": "Unknown error"}),
                "case_id": result["case_id"],
                "fallback_action": result.get("fallback_action"),
                "fallback_reason": result.get("fallback_reason"),
            },
        )
    
    return {
        "success": True,
        "data": result["data"],
        "case_id": result["case_id"],
        "model_used": result.get("model_used", "unknown"),
        # Provenance: a reader must be able to tell that no LLM produced this.
        "diagnosis_source": result.get("diagnosis_source"),
        "diagnosis_engine_version": result.get("diagnosis_engine_version"),
        "llm_used": result.get("llm_used"),
        "confidence_source": result.get("confidence_source"),
        "recovery_probability_source": result.get("recovery_probability_source"),
        "recovery_probability_used": result.get("recovery_probability_used"),
        "processing_time_ms": result.get("processing_time_ms"),
    }
