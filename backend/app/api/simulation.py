"""
Simulation API Endpoint.

POST /api/simulation/run — Run a batch simulation of synthetic payment failures.

All simulation output is clearly labeled as SIMULATED.
No real Razorpay API calls are made during simulation.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.simulation import SimulationRequest, SimulationResult
from app.services.simulation_service import run_simulation
from app.utils.logging import logger

router = APIRouter(tags=["simulation"])


@router.post("/simulation/run", response_model=SimulationResult)
def run_simulation_endpoint(
    request: SimulationRequest,
    db: Session = Depends(get_db),
):
    """
    Run a batch simulation of synthetic payment failures and recovery attempts.

    This endpoint processes synthetic transactions through the complete pipeline:
      Transaction → ML Prediction → AI Decision → Policy → Recovery → Audit

    All results are labeled SIMULATED. No real payment APIs are called.

    The simulation:
    - Generates realistic synthetic failed payment scenarios
    - Runs ML prediction on each transaction
    - Makes deterministic AI recovery recommendations
    - Validates through the Policy Engine
    - Executes simulated recovery actions
    - Creates complete audit trail
    - Computes business metrics
    """
    logger.info(f"Simulation requested: {request.num_transactions} transactions")

    result = run_simulation(
        db=db,
        num_transactions=request.num_transactions,
        seed=request.seed,
    )

    return SimulationResult(**result)
