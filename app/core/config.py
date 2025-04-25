import os
import secrets
from typing import Optional, Dict, Any, List
from pathlib import Path
import logging
import time

from pydantic_settings import BaseSettings
from pydantic import field_validator

# Base directory of the project
default_base = Path(__file__).parents[2]

def get_secure_secret_key():
    """
    Get a secure secret key for the application.
    Priority:
    1. Use SECRET_KEY from environment variable (recommended for production)
    2. Generate a random key in memory (will be different on each restart)
    
    Note: When running with multiple workers, all workers will use the same key only if:
    - An environment variable SECRET_KEY is provided before starting the application
    - OR if the app is started with `workers=1`
    """
    # First try to use environment variable if provided (highest priority)
    if os.getenv("SECRET_KEY"):
        return os.getenv("SECRET_KEY")
    
    # Generate a random key in memory
    memory_key = secrets.token_hex(32)
    
    # Set this key as an environment variable so child workers inherit it
    # This ensures all workers use the same key during this app run
    os.environ["SECRET_KEY"] = memory_key
    
    # Log a warning about this being unsuitable for production
    logging.warning(
        "SECURITY WARNING: Using a random memory-only SECRET_KEY. "
        "Sessions will be invalidated on server restart. "
        "For production, set the SECRET_KEY environment variable before starting the application "
        "to ensure consistent authentication across restarts."
    )
    
    return memory_key

class Settings(BaseSettings):
    PROJECT_NAME: str = "3D Model Viewer"
    API_V1_STR: str = "/api/v1"
    
    # Secret key for JWT token generation - now stored only in memory
    # This implementation ensures a consistent key across all workers for the current run
    SECRET_KEY: str = get_secure_secret_key()
    
    # 60 minutes * 24 hours * 7 days = 7 days
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    
    # Base directory for 3D models (override with env var)
    MODELS_BASE_DIR: Path = Path(os.getenv('MODELS_BASE_DIR', default_base / 'models'))
    
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
    
    # STL preview directory (override with env var)
    STL_PREVIEW_DIR: Path = Path(os.getenv('STL_PREVIEW_DIR', default_base / 'app' / 'static' / 'previews'))
    
    @field_validator('MODELS_BASE_DIR', mode='before')
    @classmethod
    def create_models_dir(cls, v):
        os.makedirs(v, exist_ok=True)
        return str(v)

    @field_validator('STL_PREVIEW_DIR', mode='before')
    def create_stl_preview_dir(cls, v):
        os.makedirs(v, exist_ok=True)
        return str(v)
    
    class Config:
        case_sensitive = True


settings = Settings()