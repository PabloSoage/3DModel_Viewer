from typing import Optional
from pydantic import BaseModel, EmailStr
from datetime import datetime


# Shared properties
class UserBase(BaseModel):
    username: str
    email: Optional[EmailStr] = None
    is_active: bool = True
    is_admin: bool = False


# Properties to receive via API on creation
class UserCreate(UserBase):
    password: str


# Properties to receive via API on update
class UserUpdate(BaseModel):
    username: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


# Properties shared by models stored in DB
class UserInDBBase(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Properties to return via API
class User(UserInDBBase):
    pass


# Properties stored in DB but not returned by API
class UserInDB(UserInDBBase):
    hashed_password: str