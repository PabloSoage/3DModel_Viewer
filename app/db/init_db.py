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

            # Discover and add 3D models in the models directory
            models_dir = settings.MODELS_BASE_DIR
            if os.path.exists(models_dir) and os.path.isdir(models_dir):
                for item in os.listdir(models_dir):
                    model_path = os.path.join(models_dir, item)
                    if os.path.isdir(model_path):
                        # Check if model already exists in database
                        result = await db.execute(select(Model).where(Model.path == str(model_path)))
                        existing_model = result.scalars().first()
                        if not existing_model:
                            model = Model(
                                name=item,
                                path=str(model_path),
                                description=f"Auto-discovered model: {item}"
                            )
                            db.add(model)
                            # Grant admin access if admin_user exists
                            if admin_user:
                                model.users.append(admin_user)
                            logging.info(f"Added model: {item}")
                await db.commit()
    except Exception as e:
        logging.error(f"Error initializing database: {str(e)}")
        raise