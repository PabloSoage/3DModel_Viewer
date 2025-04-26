from typing import Any, List, Dict
import os
import logging
import asyncio
import time
import json
import tempfile
from pathlib import Path
from datetime import datetime
import threading

from fastapi import APIRouter, Depends, HTTPException, status, Response, Query
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

# File-based cache paths for sharing between workers
CACHE_DIR = os.path.join(tempfile.gettempdir(), "3dmodelviewer_cache")
ADMIN_MODELS_CACHE_FILE = os.path.join(CACHE_DIR, "admin_models_cache.json")
PERMISSION_CACHE_PREFIX = os.path.join(CACHE_DIR, "perm_")

# Create cache directory if it doesn't exist
os.makedirs(CACHE_DIR, exist_ok=True)

# In-memory cache for models - global singleton
class ModelsCache:
    def __init__(self):
        self.admin_models = []  # Cache for admin users (all models)
        self.user_models = {}   # Cache per user ID
        self.model_permissions = {}  # Cache for model permissions: {model_id: [user_ids]}
        self.last_sync = None   # When was the last sync performed
        self.syncing_lock = asyncio.Lock()  # Lock for concurrent sync operations
        self.sync_in_progress = False       # Flag for sync status
        self._process_id = os.getpid()  # Store the process ID for debugging
        logger.info(f"ModelsCache initialized in process {self._process_id}")
        logger.info(f"Using shared file cache directory: {CACHE_DIR}")
        
    def update_admin_models(self, models):
        """Update admin models cache in a consistent way"""
        if not models:
            logger.warning(f"Attempted to update admin models cache with empty list (process {self._process_id})")
            return
            
        self.admin_models = list(models)  # Make a copy to prevent reference issues
        logger.info(f"Admin models cache updated with {len(models)} models in process {self._process_id}")
        
        # Also write to file-based cache
        try:
            # Prepare serializable data
            serializable_models = []
            for model in models:
                model_dict = {
                    'id': model.id,
                    'name': model.name,
                    'path': model.path,
                    'description': model.description,
                    'preview_image': model.preview_image,
                    'created_at': model.created_at.isoformat() if model.created_at else None,
                    'updated_at': model.updated_at.isoformat() if model.updated_at else None,
                }
                serializable_models.append(model_dict)
                
            # Write atomically using a temporary file
            temp_file = f"{ADMIN_MODELS_CACHE_FILE}.tmp"
            with open(temp_file, 'w') as f:
                json.dump({
                    'timestamp': time.time(),
                    'models': serializable_models
                }, f)
            
            # Replace the actual cache file (atomic operation on most systems)
            os.replace(temp_file, ADMIN_MODELS_CACHE_FILE)
            logger.info(f"Wrote {len(models)} models to shared file cache")
        except Exception as e:
            logger.error(f"Error writing to file cache: {str(e)}")
        
    def get_admin_models(self):
        """Get admin models from cache, checking file cache first if memory cache is empty"""
        if self.admin_models:
            logger.info(f"Using {len(self.admin_models)} admin models from memory cache in process {self._process_id}")
            return self.admin_models
            
        # Memory cache is empty, try file cache
        try:
            if os.path.exists(ADMIN_MODELS_CACHE_FILE):
                file_age = time.time() - os.path.getmtime(ADMIN_MODELS_CACHE_FILE)
                
                # Only use file cache if it's fresh enough (less than 5 minutes old)
                if file_age < 300:  # 5 minutes
                    with open(ADMIN_MODELS_CACHE_FILE, 'r') as f:
                        cache_data = json.load(f)
                    
                    if 'models' in cache_data:
                        logger.info(f"Loading {len(cache_data['models'])} models from shared file cache (age: {int(file_age)}s)")
                        
                        # Convert JSON models to Model objects
                        from app.models.models import Model
                        models = []
                        for model_dict in cache_data['models']:
                            # Create Model object with proper datetime conversion
                            model = Model(
                                id=model_dict['id'],
                                name=model_dict['name'],
                                path=model_dict['path'],
                                description=model_dict['description'],
                                preview_image=model_dict['preview_image']
                            )
                            
                            # Explicitly convert ISO format strings to datetime objects
                            if model_dict['created_at']:
                                model.created_at = datetime.fromisoformat(model_dict['created_at'])
                            if model_dict['updated_at']:
                                model.updated_at = datetime.fromisoformat(model_dict['updated_at'])
                            
                            models.append(model)
                        
                        # Update the memory cache
                        self.admin_models = models
                        return self.admin_models
                else:
                    logger.info(f"Shared file cache exists but is too old ({int(file_age)}s)")
            else:
                logger.info("Shared file cache does not exist")
        except Exception as e:
            logger.error(f"Error reading from file cache: {str(e)}")
        
        logger.info(f"Admin models cache is empty in process {self._process_id}")
        return None
    
    def get_model_permissions(self, model_id):
        """Get model permissions, checking file cache first if memory cache is empty"""
        if model_id in self.model_permissions:
            logger.info(f"Using permissions for model {model_id} from memory cache")
            return self.model_permissions[model_id]
            
        # Try file cache
        perm_file = f"{PERMISSION_CACHE_PREFIX}{model_id}.json"
        try:
            if os.path.exists(perm_file):
                file_age = time.time() - os.path.getmtime(perm_file)
                
                # Only use file cache if it's fresh enough (less than 10 minutes old)
                if file_age < 600:  # 10 minutes
                    with open(perm_file, 'r') as f:
                        cache_data = json.load(f)
                    
                    if 'user_ids' in cache_data:
                        logger.info(f"Loading permissions for model {model_id} from file cache (age: {int(file_age)}s)")
                        user_ids = cache_data['user_ids']
                        
                        # Update memory cache
                        self.model_permissions[model_id] = user_ids
                        return user_ids
                else:
                    logger.info(f"File cache for model {model_id} permissions exists but is too old ({int(file_age)}s)")
        except Exception as e:
            logger.error(f"Error reading permissions from file cache: {str(e)}")
            
        return None
        
    def update_model_permissions(self, model_id, user_ids):
        """Update model permissions in both memory and file cache"""
        self.model_permissions[model_id] = user_ids
        
        # Also update file cache
        perm_file = f"{PERMISSION_CACHE_PREFIX}{model_id}.json"
        try:
            # Write atomically
            temp_file = f"{perm_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump({
                    'timestamp': time.time(),
                    'user_ids': user_ids
                }, f)
            
            # Replace the actual cache file (atomic operation on most systems)
            os.replace(temp_file, perm_file)
            logger.info(f"Updated permissions for model {model_id} in file cache")
        except Exception as e:
            logger.error(f"Error writing permissions to file cache: {str(e)}")
    
    def clear(self):
        """Clear all caches"""
        prev_admin_count = len(self.admin_models) if self.admin_models else 0
        prev_user_count = len(self.user_models) if self.user_models else 0
        prev_perm_count = len(self.model_permissions) if self.model_permissions else 0
        
        self.admin_models = []
        self.user_models = {}
        self.model_permissions = {}
        self.last_sync = None
        
        # Also clear file caches
        try:
            if os.path.exists(ADMIN_MODELS_CACHE_FILE):
                os.remove(ADMIN_MODELS_CACHE_FILE)
                
            # Clear permission cache files
            for file in os.listdir(CACHE_DIR):
                if file.startswith("perm_") and file.endswith(".json"):
                    os.remove(os.path.join(CACHE_DIR, file))
                    
            logger.info(f"Cleared file caches in {CACHE_DIR}")
        except Exception as e:
            logger.error(f"Error clearing file caches: {str(e)}")
        
        logger.warning(f"Cache cleared in process {self._process_id}: "
                     f"admin_models({prev_admin_count}), user_models({prev_user_count}), "
                     f"model_permissions({prev_perm_count})")

# Create a global cache instance - ensure it's at module level
models_cache = ModelsCache()
logger.info(f"ModelsCache global instance created in process {os.getpid()}")

# For debugging - create endpoint to check cache status
@router.get("/cache-status", response_model=dict)
async def get_cache_status(
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Get current cache status information.
    Only admin users can access this endpoint.
    """
    return {
        "process_id": os.getpid(),
        "admin_models_count": len(models_cache.admin_models) if models_cache.admin_models else 0,
        "user_models_count": len(models_cache.user_models) if models_cache.user_models else 0,
        "permissions_count": len(models_cache.model_permissions) if models_cache.model_permissions else 0,
        "last_sync": models_cache.last_sync,
        "syncing_in_progress": models_cache.sync_in_progress,
    }

# Add endpoint to force-clear the cache
@router.post("/clear-cache", response_model=dict)
async def force_clear_cache(
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Force clear all caches.
    Only admin users can access this endpoint.
    """
    # Clear both memory and file cache
    models_cache.clear()
    return {"status": "success", "message": "All caches cleared (memory and file cache)"}

async def sync_discover_models(db: AsyncSession, force_refresh=False):
    """
    Ensure the models table matches subdirectories under MODELS_BASE_DIR.
    Only includes direct subdirectories of MODELS_BASE_DIR, not deeper ones.
    Uses caching to avoid unnecessary file scans.
    """
    # If sync is already in progress, wait for it to complete
    if models_cache.sync_in_progress and not force_refresh:
        logger.info(f"Another sync is in progress in process {os.getpid()}, waiting for it to complete")
        async with models_cache.syncing_lock:
            # By the time we acquire the lock, sync should be complete
            logger.info(f"Using results from previous sync operation in process {os.getpid()}")
            return
    
    async with models_cache.syncing_lock:
        try:
            # Only set if we actually acquired the lock
            models_cache.sync_in_progress = True
            logger.info(f"Starting model directory synchronization in process {os.getpid()}")
            
            # Check if we need to refresh
            current_time = time.time()
            if models_cache.last_sync and not force_refresh:
                # If cache is less than 5 minutes old, skip sync
                if current_time - models_cache.last_sync < 300:  # 5 minutes
                    logger.info("Using cached model data (less than 5 minutes old)")
                    return
            
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
            
            # If any changes were made, invalidate the cache
            if added_count > 0 or removed_count > 0:
                logger.info(f"Cache invalidated due to changes: {added_count} added, {removed_count} removed in process {os.getpid()}")
                models_cache.clear()  # Use the new clear method
            else:
                logger.info(f"No changes to models, cache remains valid in process {os.getpid()}")
            
            # Update last sync time
            models_cache.last_sync = time.time()
            
        finally:
            # Always clear the sync flag when done
            models_cache.sync_in_progress = False
            logger.info(f"Model synchronization completed in process {os.getpid()}")

@router.get("/", response_model=List[ModelSchema])
async def read_models(
    response: Response,
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_active_user),
    force_refresh: bool = Query(False, description="Force a refresh of models from disk")
) -> Any:
    """
    Retrieve models.
    Only returns models that the current user has access to.
    Admin users can see all models.
    """
    # Prevent browser caching so updates to base dir are reflected immediately
    response.headers['Cache-Control'] = 'no-store'
    
    # Add process ID to logs to help debug multi-worker issues
    pid = os.getpid()
    logger.info(f"Models requested by user {current_user.username} (admin: {current_user.is_admin}), force_refresh: {force_refresh}, process: {pid}")
    
    # Check if we should use cached data
    if not force_refresh:
        if current_user.is_admin:
            cached_models = models_cache.get_admin_models()
            if cached_models:
                logger.info(f"Using cached admin models: {len(cached_models)} models available in process {pid}")
                # Return a copy of the cache to prevent modification - important!
                models_subset = list(cached_models)[skip:skip+limit]
                return models_subset
            logger.info(f"Admin models cache miss in process {pid}, fetching from database")
        elif current_user.id in models_cache.user_models:
            logger.info(f"Using cached models for user {current_user.id}")
            # Return from cache, respecting pagination
            models_subset = models_cache.user_models[current_user.id][skip:skip+limit]
            return models_subset
        else:
            logger.info(f"Cache miss for user {current_user.id}, fetching from database")
    else:
        logger.info(f"Force refresh requested in process {pid}, bypassing cache")
    
    # Sync filesystem models into database if needed
    await sync_discover_models(db, force_refresh=force_refresh)
    
    # Fetch and cache models
    if current_user.is_admin:
        # Admin users can see all models, now sorted alphabetically by name
        result = await db.execute(select(Model).order_by(Model.name))
        models = list(result.scalars().all())  # Convert to list to ensure it's a collection
        # Cache all models for admin users
        models_cache.update_admin_models(models)
        logger.info(f"Admin models fetched and cached: {len(models)} models in process {pid}")
        return models[skip:skip+limit]
    else:
        # Regular users can only see models they have access to
        query = select(Model).join(
            user_model_permissions,
            Model.id == user_model_permissions.c.model_id
        ).where(
            user_model_permissions.c.user_id == current_user.id
        ).order_by(Model.name)
        
        result = await db.execute(query)
        models = result.scalars().all()
        # Cache models for this specific user
        models_cache.user_models[current_user.id] = models
        logger.info(f"User {current_user.id} models fetched and cached: {len(models)} models")
        return models[skip:skip+limit]


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
        
        # Invalidate cache for this model
        if permission_data.model_id in models_cache.model_permissions:
            del models_cache.model_permissions[permission_data.model_id]
            logger.info(f"Invalidated permissions cache for model {permission_data.model_id}")
        
        # Also invalidate user permissions cache
        user_cache_key = f"user_{permission_data.user_id}_permissions"
        if user_cache_key in models_cache.model_permissions:
            del models_cache.model_permissions[user_cache_key]
            logger.info(f"Invalidated permissions cache for user {permission_data.user_id}")
    
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
        
        # Invalidate cache for this model
        if permission_data.model_id in models_cache.model_permissions:
            del models_cache.model_permissions[permission_data.model_id]
            logger.info(f"Invalidated permissions cache for model {permission_data.model_id}")
        
        # Also invalidate user permissions cache
        user_cache_key = f"user_{permission_data.user_id}_permissions"
        if user_cache_key in models_cache.model_permissions:
            del models_cache.model_permissions[user_cache_key]
            logger.info(f"Invalidated permissions cache for user {permission_data.user_id}")
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
    force_refresh: bool = Query(False, description="Force refresh of permissions from database")
) -> Any:
    """
    Get all users with access to a specific model.
    Only admin users can view permissions.
    Uses caching to avoid repeated database queries for the same model.
    """
    # Check cache first if not forcing refresh
    if not force_refresh:
        # First check memory cache, then file cache
        cached_permissions = models_cache.get_model_permissions(model_id)
        if cached_permissions is not None:
            return cached_permissions
    
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
    
    # Cache the permissions (both memory and file cache)
    models_cache.update_model_permissions(model_id, user_ids)
    logger.info(f"Cached permissions for model {model_id}")
    
    return user_ids

# Add new endpoint to get all permissions for a user at once
@router.get("/user-permissions/{user_id}", response_model=Dict[int, bool])
async def read_user_model_permissions(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
    force_refresh: bool = Query(False, description="Force refresh of permissions from database")
) -> Any:
    """
    Get all model permissions for a specific user.
    Returns a dictionary mapping model_id -> boolean indicating if user has access.
    Only admin users can view permissions.
    Uses caching to avoid repeated database queries.
    """
    # Cache key for user permissions
    cache_key = f"user_{user_id}_permissions"
    
    # Check cache first if not forcing refresh
    if not force_refresh and cache_key in models_cache.model_permissions:
        logger.info(f"Using cached permissions for user {user_id}")
        return models_cache.model_permissions[cache_key]
    
    # Verify user exists
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    
    # Get all models
    result = await db.execute(select(Model))
    models = result.scalars().all()
    
    # Get all permissions for this user
    query = select(user_model_permissions.c.model_id).where(
        user_model_permissions.c.user_id == user_id
    )
    result = await db.execute(query)
    permitted_model_ids = [row[0] for row in result.fetchall()]
    
    # Create a dictionary of model_id -> has_permission
    permissions_dict = {model.id: (model.id in permitted_model_ids) for model in models}
    
    # Cache the permissions
    models_cache.model_permissions[cache_key] = permissions_dict
    logger.info(f"Cached all permissions for user {user_id}")
    
    return permissions_dict

# Modify the existing clear-permissions-cache endpoint to also clear user permissions
@router.post("/permissions/clear-cache", response_model=dict)
async def clear_permissions_cache(
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Clear the permissions cache.
    Only admin users can clear the cache.
    """
    # Clear individual model permissions and user permissions
    models_cache.model_permissions = {}
    return {"status": "success", "message": "Permissions cache cleared"}