from typing import Any, List
import os
import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
import mimetypes

from app.core.auth import (
    get_current_active_user,
    get_current_admin_user
)
from app.db.init_db import get_db
from app.core.config import settings
from app.models.models import User, Model, user_model_permissions
from app.schemas.model import DirectoryListing, FileItem

router = APIRouter()


def has_media_files(directory_path: str) -> bool:
    """Check if a directory contains any media files for previews."""
    for ext in settings.MEDIA_EXTENSIONS:
        for file in Path(directory_path).glob(f"**/*{ext}"):
            return True
    return False


def get_media_type(file_path: str) -> str:
    """Get the media type of a file."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        return mime_type
    return None


def check_path_permission(path: str, user: User, db: AsyncSession) -> bool:
    """
    Check if a user has permission to access a path.
    Admin users can access any path.
    """
    if user.is_admin:
        return True
    
    # Get all models the user has access to
    query = select(Model).join(
        user_model_permissions,
        Model.id == user_model_permissions.c.model_id
    ).where(
        user_model_permissions.c.user_id == user.id
    )
    
    result = db.execute(query)
    models = result.scalars().all()
    
    # Check if the path is under any of the models the user has access to
    for model in models:
        if path.startswith(model.path):
            return True
    
    return False


@router.get("/list/", response_model=DirectoryListing)
async def list_directory(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    List files in a directory.
    Only returns files in directories that the current user has access to.
    Admin users can list any directory.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Check if the path exists
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Path not found",
        )
    
    # Check if the user has access to the path
    if not check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this path",
        )
    
    # Check if the path is a directory
    if not os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )
    
    # List directory contents
    items = []
    parent_path = os.path.dirname(path) if path != settings.MODELS_BASE_DIR else None
    
    for item in os.listdir(path):
        item_path = os.path.join(path, item)
        is_dir = os.path.isdir(item_path)
        
        stats = os.stat(item_path)
        size = stats.st_size if not is_dir else None
        last_modified = datetime.datetime.fromtimestamp(stats.st_mtime)
        
        # Determine if previews are available (for directories)
        preview_available = False
        media_type = None
        
        if is_dir:
            preview_available = has_media_files(item_path)
        else:
            # For files, check if they are previewable media files
            _, ext = os.path.splitext(item_path)
            if ext.lower() in settings.MEDIA_EXTENSIONS:
                preview_available = True
                media_type = get_media_type(item_path)
        
        items.append(
            FileItem(
                name=item,
                path=item_path,
                is_dir=is_dir,
                size=size,
                last_modified=last_modified,
                preview_available=preview_available,
                media_type=media_type
            )
        )
    
    # Sort items: directories first, then files, both alphabetically
    items.sort(key=lambda x: (not x.is_dir, x.name.lower()))
    
    return DirectoryListing(
        path=path,
        parent_path=parent_path,
        items=items
    )


@router.get("/media-files/")
async def get_media_files(
    path: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> List[FileItem]:
    """
    Get all media files (images, videos) in a directory and its subdirectories.
    Only returns files in directories that the current user has access to.
    Admin users can access any directory.
    """
    # Normalize the path
    path = os.path.normpath(path)
    
    # Check if the path exists
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Path not found",
        )
    
    # Check if the user has access to the path
    if not check_path_permission(path, current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not enough permissions to access this path",
        )
    
    # Check if the path is a directory
    if not os.path.isdir(path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path is not a directory",
        )
    
    # Find all media files
    media_files = []
    
    for ext in settings.MEDIA_EXTENSIONS:
        for file_path in Path(path).glob(f"**/*{ext}"):
            stats = os.stat(file_path)
            size = stats.st_size
            last_modified = datetime.datetime.fromtimestamp(stats.st_mtime)
            
            media_files.append(
                FileItem(
                    name=file_path.name,
                    path=str(file_path),
                    is_dir=False,
                    size=size,
                    last_modified=last_modified,
                    preview_available=True,
                    media_type=get_media_type(str(file_path))
                )
            )
    
    # Sort files alphabetically
    media_files.sort(key=lambda x: x.name.lower())
    
    return media_files