from api.routes import router
from api.auth_routes import router as auth_router
from api.project_routes import router as project_router
from api.integration_routes import router as integration_router
from api.job_routes import router as job_router

__all__ = ["router", "auth_router", "project_router", "integration_router", "job_router"]
