"""
ML Inference API Endpoints for Revenue Recovery Agent.
"""
from fastapi import APIRouter
from app.schemas.ml import (
    MLInferenceRequest,
    MLInferenceResponse,
    MLPredictionResult,
    MLPredictRequest,
    MLPredictResponse,
    MLHealthResponse,
)
from app.services.ml_service import predict_recovery, predict_batch, get_model_info, get_model_health

router = APIRouter(tags=["ml"])


@router.post("/ml/predict", response_model=MLInferenceResponse)
def predict_recovery_endpoint(request: MLInferenceRequest):
    """
    Predict recovery probability for a failed payment.
    
    Takes transaction and customer data, returns recovery probability,
    risk assessment, and priority classification.
    """
    raw_data = {
        "amount": request.amount,
        "payment_method": request.payment_method,
        "failure_reason": request.failure_reason,
        "currency": request.currency,
        "attempt_number": request.attempt_number,
        "hour_of_day": request.hour_of_day,
        "day_of_week": request.day_of_week,
        "days_since_last_transaction": request.days_since_last_transaction,
        "customer_total_transactions": request.customer_total_transactions,
        "customer_successful_transactions": request.customer_successful_transactions,
        "customer_failed_transactions": request.customer_failed_transactions,
        "customer_lifetime_value": request.customer_lifetime_value,
        "customer_age_days": request.customer_age_days,
    }
    
    result = predict_recovery(raw_data)
    
    return MLInferenceResponse(
        success=True,
        data=MLPredictionResult(
            recovery_probability=result["recovery_probability"],
            risk_score=result["risk_score"],
            risk_level=result["risk_level"],
            priority=result["priority"],
            recovery_category=result["recovery_category"],
            model_version=result["model_version"],
        ),
    )


@router.post("/ml/predict/batch", response_model=MLPredictResponse)
def predict_batch_endpoint(request: MLPredictRequest):
    """
    Predict recovery probability for multiple failed payments.
    
    Takes a list of transactions and returns predictions for each.
    """
    raw_data_list = [
        {
            "amount": t.amount,
            "payment_method": t.payment_method,
            "failure_reason": t.failure_reason,
            "currency": t.currency,
            "attempt_number": t.attempt_number,
            "hour_of_day": t.hour_of_day,
            "day_of_week": t.day_of_week,
            "days_since_last_transaction": t.days_since_last_transaction,
            "customer_total_transactions": t.customer_total_transactions,
            "customer_successful_transactions": t.customer_successful_transactions,
            "customer_failed_transactions": t.customer_failed_transactions,
            "customer_lifetime_value": t.customer_lifetime_value,
            "customer_age_days": t.customer_age_days,
        }
        for t in request.transactions
    ]
    
    results = predict_batch(raw_data_list)
    
    return MLPredictResponse(
        success=True,
        data=[
            MLPredictionResult(
                recovery_probability=r["recovery_probability"],
                risk_score=r["risk_score"],
                risk_level=r["risk_level"],
                priority=r["priority"],
                recovery_category=r["recovery_category"],
                model_version=r["model_version"],
            )
            for r in results
        ],
    )


@router.get("/ml/model", response_model=dict)
def get_model_info_endpoint():
    """
    Get information about the loaded ML model.
    
    Returns model version, type, features, and training timestamp.
    """
    return get_model_info()


@router.get("/ml/health", response_model=MLHealthResponse)
def ml_health_check():
    """
    Check ML model health status.
    
    Verifies model is loaded and can make predictions.
    """
    health = get_model_health()
    return MLHealthResponse(
        success=health["status"] == "healthy",
        data=health,
    )
