"""非同期 SQLAlchemy:MySQL(開発・本番想定)/ SQLite(単体テスト・Docker なしで試すとき)を DATABASE_URL で切り替え

CI では同じ単体テストを SQLite と MySQL 8.4 の両方で実行し、DB による違いがないことを確認している。

テーブル構造は Alembic(migrations/)で管理する。モデルを変更したら:
    alembic revision --autogenerate -m "変更内容"
    alembic upgrade head
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings

_engine_kwargs = {"echo": False, "pool_pre_ping": True}
if settings.database_url.startswith("mysql"):
    # MySQL はアイドル接続を wait_timeout(既定8時間)で切断する → それより前に接続を作り直す
    _engine_kwargs["pool_recycle"] = 3600

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI 依存:リクエストごとに1セッション"""
    async with SessionLocal() as session:
        yield session
