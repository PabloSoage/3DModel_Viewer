from fastapi import APIRouter

from app.api.api_v1.endpoints import auth, users, models, explorer, files, settings

api_router = APIRouter()

# Include all API endpoints
api_router.include_router(auth.router, prefix="/auth", tags=["authentication"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(models.router, prefix="/models", tags=["models"])
api_router.include_router(explorer.router, prefix="/explorer", tags=["explorer"])
api_router.include_router(files.router, prefix="/files", tags=["files"])
api_router.include_router(settings.router, prefix="/settings", tags=["settings"])