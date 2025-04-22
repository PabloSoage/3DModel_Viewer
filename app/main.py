from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
import os
import logging

from app.core.config import settings
from app.api.api_v1.api import api_router
from app.core.auth import get_current_active_user, get_current_admin_user
from app.db.init_db import init_db

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="3D Model Viewer and Manager",
    version="1.0.0",
)

# Set up templates
templates = Jinja2Templates(directory="app/templates")

# Set up CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(api_router, prefix=settings.API_V1_STR)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Frontend routes
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to the login page."""
    return RedirectResponse(url="/login")

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page."""
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/models", response_class=HTMLResponse)
async def models_page(request: Request, current_user=Depends(get_current_active_user)):
    """Models listing page."""
    return templates.TemplateResponse("models.html", {"request": request, "user": current_user})

@app.get("/admin/models", response_class=HTMLResponse)
async def admin_models_page(request: Request, current_user=Depends(get_current_admin_user)):
    """Admin models management page."""
    return templates.TemplateResponse("admin_models.html", {"request": request, "user": current_user})

@app.get("/explorer/{model_id}", response_class=HTMLResponse)
async def explorer_page(model_id: int, request: Request, current_user=Depends(get_current_active_user)):
    """File explorer page for a specific model."""
    return templates.TemplateResponse("explorer.html", {"request": request, "model_id": model_id, "user": current_user})

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request, current_user=Depends(get_current_admin_user)):
    """User administration page."""
    return templates.TemplateResponse("admin_users.html", {"request": request, "user": current_user})

@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
