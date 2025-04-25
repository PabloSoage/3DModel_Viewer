from typing import Any, List
import os
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from sqlalchemy.orm import joinedload

from app.core.auth import (
    get_current_active_user,
    get_current_admin_user
)
from app.db.init_db import get_db
from app.core.config import settings
from app.models.models import User, Model, Setting, user_model_permissions
from app.schemas.model import Model as ModelSchema, ModelPermission, ModelUpdate

router = APIRouter()

# Set up logger
logger = logging.getLogger(__name__)

async def sync_discover_models(db: AsyncSession):
    """
    Ensure the models table matches subdirectories under MODELS_BASE_DIR.
    Only includes direct subdirectories of MODELS_BASE_DIR, not deeper ones.
    """
    # Fetch dynamic base directory from settings table
    result = await db.execute(select(Setting).where(Setting.key == 'MODELS_BASE_DIR'))
    setting = result.scalars().first()
    base_dir = setting.value if setting and setting.value else settings.MODELS_BASE_DIR
    
    # Normalize base directory path with OS-appropriate separators
    base_dir = os.path.normpath(base_dir)
    logger.info(f"Syncing models from base directory: {base_dir}")
    
    # List current folders (direct subdirectories)
    try:
        dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
        logger.info(f"Found {len(dirs)} directories in base directory: {dirs}")
    except Exception as e:
        logger.error(f"Error listing directories: {e}")
        return
    
    # Create a set of valid model paths (direct subdirectories only)
    valid_paths = set()
    for dir_name in dirs:
        full_path = os.path.normpath(os.path.join(base_dir, dir_name))
        valid_paths.add(full_path)
    
    # Fetch existing models
    result = await db.execute(select(Model))
    models = result.scalars().all()
    logger.info(f"Found {len(models)} existing models in database")
    logger.info(f"Valid direct subdirectory paths: {valid_paths}")
    
    # Debug: show all current model paths
    for model in models:
        norm_path = os.path.normpath(model.path)
        logger.info(f"Existing model in DB: {model.name}, path: {norm_path}")
        if norm_path not in valid_paths:
            logger.info(f"Model {model.name} with path {norm_path} is NOT a valid direct subdirectory")
        else:
            logger.info(f"Model {model.name} with path {norm_path} is a valid direct subdirectory")
    
    # Add new models for any new folder
    added_count = 0
    for valid_path in valid_paths:
        result = await db.execute(select(Model).where(Model.path == valid_path))
        existing_model = result.scalars().first()
        
        if not existing_model:
            name = os.path.basename(valid_path)
            model = Model(name=name, path=valid_path, description=f"Auto-discovered: {name}")
            db.add(model)
            added_count += 1
            logger.info(f"Adding new model: {name} at {valid_path}")
    
    # Remove models that are not in the valid_paths set
    removed_count = 0
    for model in models:
        norm_path = os.path.normpath(model.path)
        if norm_path not in valid_paths:
            logger.info(f"REMOVING model: {model.name} at {norm_path} (not a direct subdirectory)")
            await db.delete(model)
            removed_count += 1
    
    await db.commit()
    logger.info(f"Sync complete: Added {added_count} models, removed {removed_count} models")

@router.get("/", response_model=List[ModelSchema])
async def read_models(
    response: Response,
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Retrieve models.
    Only returns models that the current user has access to.
    Admin users can see all models.
    """
    # Prevent caching so updates to base dir are reflected immediately
    response.headers['Cache-Control'] = 'no-store'
    # Sync filesystem models into database
    await sync_discover_models(db)
    if current_user.is_admin:
        # Admin users can see all models, now sorted alphabetically by name
        result = await db.execute(select(Model).order_by(Model.name).offset(skip).limit(limit))
        models = result.scalars().all()
    else:
        # Regular users can only see models they have access to
        query = select(Model).join(
            user_model_permissions,
            Model.id == user_model_permissions.c.model_id
        ).where(
            user_model_permissions.c.user_id == current_user.id
        ).order_by(Model.name).offset(skip).limit(limit)  # Added ordering here
        
        result = await db.execute(query)
        models = result.scalars().all()
    
    return models


@router.get("/{model_id}", response_model=ModelSchema)
async def read_model(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Any:
    """
    Get model by ID.
    Only returns the model if the current user has access to it.
    Admin users can see any model.
    """
    # Get the model
    result = await db.execute(select(Model).where(Model.id == model_id))
    model = result.scalars().first()
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    
    # Check if the user has access to the model
    if not current_user.is_admin:
        # Check if the user has permission for this model
        query = select(user_model_permissions).where(
            and_(
                user_model_permissions.c.user_id == current_user.id,
                user_model_permissions.c.model_id == model.id
            )
        )
        result = await db.execute(query)
        permission = result.first()
        
        if not permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not enough permissions to access this model",
            )
    
    return model


@router.put("/{model_id}", response_model=ModelSchema)
async def update_model(
    model_id: int,
    model_in: ModelUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Update model metadata (description, preview image).
    Only admins can update.
    """
    result = await db.execute(select(Model).where(Model.id == model_id))
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    # Update fields
    if model_in.description is not None:
        model.description = model_in.description
    if model_in.preview_image is not None:
        model.preview_image = model_in.preview_image
    await db.commit()
    await db.refresh(model)
    return model


@router.put("/permissions", response_model=ModelPermission)
async def update_model_permission(
    permission_data: ModelPermission,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Grant model access permission to a user.
    Only admin users can grant permissions.
    """
    # Verify model exists
    result = await db.execute(select(Model).where(Model.id == permission_data.model_id))
    model = result.scalars().first()
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    
    # Verify user exists
    result = await db.execute(select(User).where(User.id == permission_data.user_id))
    user = result.scalars().first()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Check if permission already exists
    query = select(user_model_permissions).where(
        and_(
            user_model_permissions.c.user_id == permission_data.user_id,
            user_model_permissions.c.model_id == permission_data.model_id
        )
    )
    result = await db.execute(query)
    existing_permission = result.first()
    
    if not existing_permission:
        # Add the permission
        query = user_model_permissions.insert().values(
            user_id=permission_data.user_id,
            model_id=permission_data.model_id
        )
        await db.execute(query)
        await db.commit()
    
    return permission_data


@router.delete("/permissions", response_model=ModelPermission)
async def delete_model_permission(
    permission_data: ModelPermission,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Remove model access permission from a user.
    Only admin users can remove permissions.
    """
    # Check if permission exists
    query = select(user_model_permissions).where(
        and_(
            user_model_permissions.c.user_id == permission_data.user_id,
            user_model_permissions.c.model_id == permission_data.model_id
        )
    )
    result = await db.execute(query)
    existing_permission = result.first()
    
    if existing_permission:
        # Remove the permission
        query = user_model_permissions.delete().where(
            and_(
                user_model_permissions.c.user_id == permission_data.user_id,
                user_model_permissions.c.model_id == permission_data.model_id
            )
        )
        await db.execute(query)
        await db.commit()
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Permission not found",
        )
    
    return permission_data


@router.get("/{model_id}/permissions", response_model=List[int])
async def read_model_permissions(
    model_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Get all users with access to a specific model.
    Only admin users can view permissions.
    """
    # Verify model exists
    result = await db.execute(select(Model).where(Model.id == model_id))
    model = result.scalars().first()
    
    if not model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model not found",
        )
    
    # Get all user IDs with access to this model
    query = select(user_model_permissions.c.user_id).where(
        user_model_permissions.c.model_id == model_id
    )
    result = await db.execute(query)
    user_ids = [row[0] for row in result.fetchall()]
    
    return user_ids