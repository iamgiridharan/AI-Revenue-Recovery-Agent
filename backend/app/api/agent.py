"""
AI Agent API Endpoints.

Provides endpoints for:
- Diagnosing revenue risk cases
- Getting agent recommendations
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

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
):
    """
    Diagnose a revenue risk case using AI agent.
    
    This endpoint:
    1. Gathers context about the case
    2. Calls LLM for diagnosis and recommendation
    3. Validates structured output
    4. Records audit events
    5. Returns validated recommendation
    
    The agent NEVER directly executes payment operations.
    It produces recommendations that go through the Policy Engine.
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
            },
        )
    
    return {
        "success": True,
        "data": result["data"],
        "case_id": result["case_id"],
        "model_used": result.get("model_used", "unknown"),
        "processing_time_ms": result.get("processing_time_ms"),
    }
