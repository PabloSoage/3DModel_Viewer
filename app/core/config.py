import os
import secrets
from typing import Optional, Dict, Any, List

from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    PROJECT_NAME: str = "3D Model Viewer"
    API_V1_STR: str = "/api/v1"
    
    # Secret key for JWT token generation
    # Generamos una clave secreta criptográficamente segura
    SECRET_KEY: str = secrets.token_hex(32)
    # 60 minutes * 24 hours * 7 days = 7 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    # Base directory for 3D models
    MODELS_BASE_DIR: str = os.path.abspath(r"D:/Downloads+/3DModel_Viewer/models")
    
    # Supported media file extensions for previews
    MEDIA_EXTENSIONS: List[str] = [
        ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
        ".mp4", ".webm", ".mov", ".avi", ".stl"
    ]
    
    # Supported text file extensions for web viewing
    TEXT_EXTENSIONS: List[str] = [
        ".txt", ".md", ".csv", ".json", ".xml", ".yaml", ".yml",
        ".html", ".htm", ".css", ".js", ".log", ".ini", ".conf"
    ]
    
    # Supported document file extensions for web viewing (if viewer available)
    DOCUMENT_EXTENSIONS: List[str] = [
        ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"
    ]
    
    # Database settings
    DATABASE_URL: str = f"sqlite:///./app/db/app.db"
    
    # STL preview settings
    STL_PREVIEW_DIR: str = os.path.abspath(r"D:/Downloads+/3DModel_Viewer/app/static/previews")
    
    @field_validator("STL_PREVIEW_DIR", mode="before")
    def create_stl_preview_dir(cls, v):
        os.makedirs(v, exist_ok=True)
        return v
    
    class Config:
        case_sensitive = True


settings = Settings()

# Create directories if they don't exist
os.makedirs(settings.MODELS_BASE_DIR, exist_ok=True)
os.makedirs(settings.STL_PREVIEW_DIR, exist_ok=True)