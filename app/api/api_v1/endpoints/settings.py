from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
import os

from app.db.init_db import get_db
from app.models.models import Setting
from app.schemas.setting import Setting as SettingSchema, SettingCreate, DirItem
from app.core.auth import get_current_admin_user

router = APIRouter()

@router.get("/", response_model=List[SettingSchema])
async def read_settings(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    result = await db.execute(select(Setting))
    return result.scalars().all()

@router.put("/{key}", response_model=SettingSchema)
async def update_setting(
    key: str,
    setting: SettingCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    result = await db.execute(select(Setting).where(Setting.key == key))
    existing = result.scalars().first()
    if existing:
        existing.value = setting.value
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
        # If base directory changed, clear existing models so they will be re-synced
        if key == 'MODELS_BASE_DIR':
            from sqlalchemy import delete
            from app.models.models import Model
            await db.execute(delete(Model))
            await db.commit()
        return existing
    new = Setting(key=setting.key, value=setting.value)
    db.add(new)
    await db.commit()
    await db.refresh(new)
    return new

@router.get("/files", response_model=List[DirItem])
async def list_files(
    path: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_admin_user)
):
    if not os.path.exists(path) or not os.path.isdir(path):
        raise HTTPException(status_code=400, detail="Path not found or is not a directory")
    items = []
    # Include parent directory to enable navigation up
    parent = os.path.dirname(path)
    if parent and os.path.isdir(parent):
        items.append(DirItem(name='..', path=parent))
    for name in os.listdir(path):
        full = os.path.join(path, name)
        items.append(DirItem(name=name, path=full))
    return items
