from datetime import datetime, timedelta
from typing import Optional, Union, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from fastapi import Cookie, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.init_db import get_db
from app.core.config import settings
from app.core.utils import verify_password, get_password_hash
from app.models.models import User

# OAuth2 scheme for token authentication
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")

async def authenticate_user(username: str, password: str, db: AsyncSession) -> Optional[User]:
    """Authenticate a user by username and password."""
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token."""
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm="HS256")
    return encoded_jwt

async def get_current_user(
    token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)
) -> User:
    """Get the current user from token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    
    if user is None:
        raise credentials_exception
    return user

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get the current active user."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def get_current_admin_user(current_user: User = Depends(get_current_active_user)) -> User:
    """Get the current admin user."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="The user doesn't have enough privileges"
        )
    return current_user

async def get_current_user_from_cookie(
    token: Optional[str] = Cookie(None),
    request: Request = None,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get current user from cookie or Authorization header.
    """
    # Try cookie first
    if not token:
        auth_header = request.headers.get('Authorization')
        if (auth_header and auth_header.startswith('Bearer ')):
            token = auth_header.split(' ')[1]
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Not authenticated',
            headers={'WWW-Authenticate': 'Bearer'},
        )
    return await get_current_user(token, db)

async def get_current_active_user_cookie(
    current_user: User = Depends(get_current_user_from_cookie)
) -> User:
    """Get current active user via cookie-based auth."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail='Inactive user')
    return current_user

async def get_current_admin_user_cookie(
    current_user: User = Depends(get_current_active_user_cookie)
) -> User:
    """Get current admin user via cookie-based auth."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user

async def get_current_active_user_any(
    token: Optional[str] = Cookie(None),
    authorization: str = Depends(oauth2_scheme),
    request: Request = None,
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Get current active user from cookie or OAuth2 bearer token.
    """
    # Try cookie first
    if token:
        user = await get_current_user_from_cookie(token, request, db)
    else:
        # authorization provided by oauth2_scheme
        user = await get_current_active_user(authorization, db)
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user