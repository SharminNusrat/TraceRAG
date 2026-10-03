from api.analysis_routes import router
from api.auth_routes import router as auth_router
from api.project_routes import router as project_router
from api.github_routes import router as github_router
from api.source_routes import router as source_router
from api.sync_routes import router as sync_router
from api.job_routes import router as job_router

__all__ = ["router", "auth_router", "project_router", "github_router", "source_router", "sync_router",
           "job_router"]
