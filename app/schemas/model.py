from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime


# Shared properties
class ModelBase(BaseModel):
    name: str
    path: str
    description: Optional[str] = None
    # Optional stored preview image path
    preview_image: Optional[str] = None


# Properties to receive via API on creation
class ModelCreate(ModelBase):
    pass


# Properties to receive via API on update
class ModelUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    preview_image: Optional[str] = None


# Properties shared by models stored in DB
class ModelInDBBase(ModelBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Properties to return via API
class Model(ModelInDBBase):
    pass


# Model permission schema
class ModelPermission(BaseModel):
    model_id: int
    user_id: int


# File explorer item
class FileItem(BaseModel):
    name: str
    path: str
    is_dir: bool
    size: Optional[int] = None
    modified: Optional[datetime] = None
    preview_available: bool = False
    media_type: Optional[str] = None


# Directory listing response
class DirectoryListing(BaseModel):
    path: str
    parent_path: Optional[str] = None
    items: List[FileItem]