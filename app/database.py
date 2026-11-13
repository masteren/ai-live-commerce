"""异步 SQLAlchemy:SQLite(开发)/ PostgreSQL(生产)通过 DATABASE_URL 切换"""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from .config import settings

engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 依赖:每个请求一个会话"""
    async with SessionLocal() as session:
        yield session
