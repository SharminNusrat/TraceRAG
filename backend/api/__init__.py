from api.routes import router
from api.auth_routes import router as auth_router
from api.project_routes import router as project_router

__all__ = ["router", "auth_router", "project_router"]
