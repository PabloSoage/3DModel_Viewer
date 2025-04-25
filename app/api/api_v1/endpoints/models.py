from typing import Any, List
import os

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

async def sync_discover_models(db: AsyncSession):
    """
    Ensure the models table matches subdirectories under MODELS_BASE_DIR.
    """
    # Fetch dynamic base directory from settings table
    result = await db.execute(select(Setting).where(Setting.key == 'MODELS_BASE_DIR'))
    setting = result.scalars().first()
    base_dir = setting.value if setting and setting.value else settings.MODELS_BASE_DIR
    # List current folders
    try:
        dirs = [d for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))]
    except Exception:
        return
    # Fetch existing models
    result = await db.execute(select(Model))
    models = result.scalars().all()
    existing_paths = {m.path for m in models}
    # Add new models for any new folder
    for name in dirs:
        full_path = os.path.join(base_dir, name)
        if full_path not in existing_paths:
            model = Model(name=name, path=full_path, description=f"Auto-discovered: {name}")
            db.add(model)
    # Remove models whose directory no longer exists (or outside base_dir)
    for model in models:
        if not os.path.isdir(model.path) or not model.path.startswith(base_dir):
            await db.delete(model)
    await db.commit()

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
        # Admin users can see all models
        result = await db.execute(select(Model).offset(skip).limit(limit))
        models = result.scalars().all()
    else:
        # Regular users can only see models they have access to
        query = select(Model).join(
            user_model_permissions,
            Model.id == user_model_permissions.c.model_id
        ).where(
            user_model_permissions.c.user_id == current_user.id
        ).offset(skip).limit(limit)
        
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