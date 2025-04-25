from fastapi import FastAPI, Depends, HTTPException, status, Request, Cookie, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from contextlib import asynccontextmanager
import os
import logging
from typing import Optional

from app.core.config import settings
from app.api.api_v1.api import api_router
from app.core.auth import get_current_active_user, get_current_admin_user, get_current_user
from app.db.init_db import init_db, get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.models import User
from app.core.utils import verify_password

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: initialize the database
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")
    yield
    # Shutdown: add cleanup here if needed
    logger.info("Shutting down application")

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="3D Model Viewer and Manager",
    version="1.0.0",
    lifespan=lifespan
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
async def login_page(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Login page."""
    # Determine whether to show default admin credentials hint
    result = await db.execute(select(User).where(User.username == "admin"))
    admin_user = result.scalars().first()
    show_default_hint = False
    if admin_user and admin_user.is_active and verify_password("admin", admin_user.hashed_password):
        show_default_hint = True
    return templates.TemplateResponse(
        "login.html", {"request": request, "show_default_hint": show_default_hint}
    )

# Función auxiliar para manejar la autenticación con cookies
async def get_current_user_from_cookie(
    token: Optional[str] = Cookie(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db)
):
    if not token:
        # Si no hay token en la cookie, intentar obtener del encabezado Authorization
        auth_header = request.headers.get("Authorization")
        if (auth_header and auth_header.startswith("Bearer ")):
            token = auth_header.split(" ")[1]
    
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Usar la función existente para validar el token
    # Pasando tanto el token como la sesión de base de datos
    return await get_current_user(token, db)

@app.get("/models", response_class=HTMLResponse)
async def models_page(
    request: Request, 
    response: Response,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Models listing page."""
    try:
        # Intentar obtener usuario con token de cookie o encabezado
        current_user = await get_current_user_from_cookie(token, request, db)
        return templates.TemplateResponse("models.html", {"request": request, "user": current_user})
    except HTTPException:
        # Si no está autenticado, redirigir a la página de login
        return RedirectResponse(url="/login", status_code=302)

@app.get("/admin/models", response_class=HTMLResponse)
async def admin_models_page(
    request: Request, 
    response: Response,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Admin models management page."""
    try:
        # Intentar obtener usuario con token de cookie o encabezado
        current_user = await get_current_user_from_cookie(token, request, db)
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="The user doesn't have enough privileges"
            )
        return templates.TemplateResponse("admin_models.html", {"request": request, "user": current_user})
    except HTTPException as e:
        if e.status_code == 401:
            # Si no está autenticado, redirigir a la página de login
            return RedirectResponse(url="/login", status_code=302)
        else:
            # Si no tiene permisos, mostrar página de error o redirigir
            return RedirectResponse(url="/models", status_code=302)

@app.get("/explorer/{model_id}", response_class=HTMLResponse)
async def explorer_page(
    model_id: int, 
    request: Request, 
    response: Response,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """File explorer page for a specific model."""
    try:
        # Intentar obtener usuario con token de cookie o encabezado
        current_user = await get_current_user_from_cookie(token, request, db)
        return templates.TemplateResponse("explorer.html", {"request": request, "model_id": model_id, "user": current_user})
    except HTTPException:
        # Si no está autenticado, redirigir a la página de login
        return RedirectResponse(url="/login", status_code=302)

@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(
    request: Request, 
    response: Response,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """User administration page."""
    try:
        # Intentar obtener usuario con token de cookie o encabezado
        current_user = await get_current_user_from_cookie(token, request, db)
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="The user doesn't have enough privileges"
            )
        return templates.TemplateResponse("admin_users.html", {"request": request, "user": current_user})
    except HTTPException as e:
        if e.status_code == 401:
            # Si no está autenticado, redirigir a la página de login
            return RedirectResponse(url="/login", status_code=302)
        else:
            # Si no tiene permisos, mostrar página de error o redirigir
            return RedirectResponse(url="/models", status_code=302)

@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings_page(
    request: Request,
    response: Response,
    token: Optional[str] = Cookie(None),
    db: AsyncSession = Depends(get_db)
):
    """Settings administration page."""
    try:
        current_user = await get_current_user_from_cookie(token, request, db)
        if not current_user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The user doesn't have enough privileges"
            )
        return templates.TemplateResponse("admin_settings.html", {"request": request, "user": current_user})
    except HTTPException as e:
        if e.status_code == 401:
            return RedirectResponse(url="/login", status_code=302)
        else:
            return RedirectResponse(url="/models", status_code=302)

if __name__ == "__main__":
    import uvicorn
    import platform
    
    # Keep workers=4 for all platforms
    workers = 4
    
    # Configure platform-specific settings for uvicorn
    if platform.system() == "Windows":
        # On Windows, use uvicorn directly with multiple workers but configure correctly
        logger.info(f"Running on Windows with {workers} workers")
        # The host is specified per-worker instead of shared across workers
        uvicorn.run(
            "main:app", 
            host="0.0.0.0", 
            port=8888, 
            reload=False,
            workers=workers,
            loop="asyncio",           # Use asyncio event loop explicitly
            http="h11",               # Use standard library HTTP implementation instead of httptools
            ws="none",                # Disable WebSockets since we don't use them
        )
    else:
        # On Unix systems, use the default configuration
        logger.info(f"Running on Unix-like system with {workers} workers")
        uvicorn.run(
            "main:app", 
            host="0.0.0.0", 
            port=8888, 
            reload=False,
            workers=workers
        )
