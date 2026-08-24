from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import get_settings
from app.api.health import router as health_router
from app.api.cases import router as cases_router
from app.api.ml import router as ml_router
from app.api.agent import router as agent_router
from app.api.policy import router as policy_router
from app.api.webhooks import router as webhooks_router
from app.api.dashboard import router as dashboard_router
from app.api.simulation import router as simulation_router
from app.utils.errors import (
    AppError,
    app_error_handler,
    http_error_handler,
    generic_error_handler,
)
from app.utils.logging import logger

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered Revenue Recovery Agent API",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Error handlers
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, generic_error_handler)

# Routers
app.include_router(health_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(ml_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(policy_router, prefix="/api")
app.include_router(webhooks_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(simulation_router, prefix="/api")


@app.on_event("startup")
def startup_event():
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")


@app.on_event("shutdown")
def shutdown_event():
    logger.info(f"Shutting down {settings.APP_NAME}")
