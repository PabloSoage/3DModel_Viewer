import os
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from typing import AsyncGenerator

from app.core.config import settings
from app.core.utils import get_password_hash
from app.models.models import Base, User, Model

# Convert SQLite URL to async format
SQLALCHEMY_DATABASE_URL = settings.DATABASE_URL.replace(
    "sqlite:///", "sqlite+aiosqlite:///"
)

# Create SQLite directory if it doesn't exist
os.makedirs(os.path.dirname(SQLALCHEMY_DATABASE_URL.replace("sqlite+aiosqlite:///", "")), exist_ok=True)

# Create async engine
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False},
    poolclass=NullPool
)

# Create async session factory
AsyncSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Dependency to get DB session
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session

# Initialize database with admin user and default models
async def init_db() -> None:
    try:
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Seed default admin user only on first initialization
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select, func
            # Count existing users
            result = await db.execute(select(func.count(User.id)))
            user_count = result.scalar_one()
            # Determine admin_user
            if user_count == 0:
                admin_user = User(
                    username="admin",
                    hashed_password=get_password_hash("admin"),  # Default password, should be changed
                    email="admin@example.com",
                    is_active=True,
                    is_admin=True
                )
                db.add(admin_user)
                await db.commit()
                await db.refresh(admin_user)
                logging.info("Created default admin user")
            else:
                # Fetch existing admin user if present
                result = await db.execute(select(User).where(User.username == "admin"))
                admin_user = result.scalars().first()

            # Seed application settings with defaults
            from sqlalchemy import select as select_setting
            from app.models.models import Setting
            default_settings = {
                'MODELS_BASE_DIR': str(settings.MODELS_BASE_DIR),
                'STL_PREVIEW_DIR': str(settings.STL_PREVIEW_DIR),
            }
            for key, val in default_settings.items():
                result = await db.execute(select_setting(Setting).where(Setting.key == key))
                existing = result.scalars().first()
                if not existing:
                    new_setting = Setting(key=key, value=val)
                    db.add(new_setting)
                    logging.info(f"Seeded config setting: {key}")
            await db.commit()

            # Ensure 'preview_image' column exists on existing DB
            from sqlalchemy import text
            pragma = await db.execute(text("PRAGMA table_info(models)"))
            cols = [row[1] for row in pragma.fetchall()]
            if 'preview_image' not in cols:
                await db.execute(text('ALTER TABLE models ADD COLUMN preview_image TEXT'))
                await db.commit()
            
            # NOTE: Preview_image population skipped synchronously to speed startup; handled in background
    except Exception as e:
        logging.error(f"Error initializing database: {str(e)}")
        raise

async def discover_models() -> None:
    """
    Discover and add 3D models from the models directory into the database.
    """
    from sqlalchemy import select
    from app.models.models import Model, User as UserModel
    async with AsyncSessionLocal() as db:
        # Fetch admin user for default grant
        result = await db.execute(select(UserModel).where(UserModel.username == 'admin'))
        admin_user = result.scalars().first()
        models_dir = settings.MODELS_BASE_DIR
        if os.path.exists(models_dir) and os.path.isdir(models_dir):
            for item in os.listdir(models_dir):
                model_path = os.path.join(models_dir, item)
                if os.path.isdir(model_path):
                    # Check if already exists
                    result = await db.execute(select(Model).where(Model.path == str(model_path)))
                    existing = result.scalars().first()
                    if not existing:
                        model = Model(
                            name=item,
                            path=str(model_path),
                            description=f"Auto-discovered model: {item}"
                        )
                        db.add(model)
                        if admin_user:
                            model.users.append(admin_user)
                        logging.info(f"Discovered model: {item}")
            await db.commit()

async def discover_models_in_batches(batch_size: int = 50):
    """
    Discover and add 3D models in batches to avoid long blocking scans.
    """
    import os
    import asyncio
    from sqlalchemy import select
    from app.core.config import settings
    from app.models.models import Model, User as UserModel

    models_dir = settings.MODELS_BASE_DIR
    if not os.path.isdir(models_dir):
        return

    # Prepare list of model directories
    dirs = [item for item in os.listdir(models_dir) if os.path.isdir(os.path.join(models_dir, item))]
    total = len(dirs)
    idx = 0
    # Chunked processing
    while idx < total:
        chunk = dirs[idx:idx + batch_size]
        async with AsyncSessionLocal() as db:
            # Fetch admin user once per batch
            result = await db.execute(select(UserModel).where(UserModel.username == 'admin'))
            admin_user = result.scalars().first()
            for item in chunk:
                path = os.path.join(models_dir, item)
                # Skip if exists
                res = await db.execute(select(Model).where(Model.path == str(path)))
                if res.scalars().first():
                    continue
                model = Model(name=item, path=str(path), description=f"Auto-discovered: {item}")
                db.add(model)
                if admin_user:
                    model.users.append(admin_user)
            await db.commit()
        idx += batch_size
        # yield to event loop
        await asyncio.sleep(0)