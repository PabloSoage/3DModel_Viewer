from typing import Any, List

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func

from app.core.auth import get_current_admin_user
from app.core.utils import get_password_hash
from app.db.init_db import get_db
from app.models.models import User
from app.schemas.user import User as UserSchema, UserCreate, UserUpdate

router = APIRouter()


@router.get("/", response_model=List[UserSchema])
async def read_users(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Retrieve users.
    """
    results = await db.execute(select(User).offset(skip).limit(limit))
    users = results.scalars().all()
    return users


@router.post("/", response_model=UserSchema)
async def create_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_in: UserCreate,
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Create new user. Only admins can create users.
    """
    # Check if user with this username already exists
    result = await db.execute(select(User).where(User.username == user_in.username))
    user = result.scalars().first()
    if user:
        raise HTTPException(
            status_code=400,
            detail="A user with this username already exists.",
        )
    
    # Check if user with this email already exists
    if user_in.email:
        result = await db.execute(select(User).where(User.email == user_in.email))
        user = result.scalars().first()
        if user:
            raise HTTPException(
                status_code=400,
                detail="A user with this email already exists.",
            )
    
    # Create new user
    user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        is_active=user_in.is_active,
        is_admin=user_in.is_admin,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/me", response_model=UserSchema)
async def read_user_me(
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Get current user.
    """
    return current_user


@router.get("/{user_id}", response_model=UserSchema)
async def read_user(
    user_id: int,
    current_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """
    Get a specific user by id.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )
    return user


@router.put("/{user_id}", response_model=UserSchema)
async def update_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    user_in: UserUpdate,
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Update a user.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )
    
    # Update user attributes
    if user_in.username is not None:
        user.username = user_in.username
    if user_in.email is not None:
        user.email = user_in.email
    if user_in.is_active is not None:
        user.is_active = user_in.is_active
    if user_in.is_admin is not None:
        user.is_admin = user_in.is_admin
    if user_in.password is not None:
        user.hashed_password = get_password_hash(user_in.password)
    
    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", response_model=UserSchema)
async def delete_user(
    *,
    db: AsyncSession = Depends(get_db),
    user_id: int,
    current_user: User = Depends(get_current_admin_user),
) -> Any:
    """
    Delete a user.
    """
    # Fetch the user to delete
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found",
        )

    # Prevent deleting the only admin user
    if user.is_admin:
        # Count all other active admin users excluding this one
        count_result = await db.execute(
            select(func.count(User.id)).where(
                and_(
                    User.is_admin == True,
                    User.is_active == True,
                    User.id != user_id
                )
            )
        )
        other_admin_count = count_result.scalar_one()
        if other_admin_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete the only admin user",
            )

    # Proceed with deletion
    await db.delete(user)
    await db.commit()
    return user