"""Alembic マイグレーション:モデルとマイグレーションがずれていないか"""
import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app import migrate
from app.database import Base, engine


async def test_upgrade_head_matches_models(tmp_path):
    """空の DB に upgrade head → ORM モデルとの差分がゼロ(モデルを変えたのにマイグレーションを作り忘れると失敗する)"""
    url = f"sqlite+aiosqlite:///{tmp_path}/mig.db"
    cfg = migrate._alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    eng = create_async_engine(url)
    async with eng.connect() as conn:
        diff = await conn.run_sync(
            lambda c: compare_metadata(MigrationContext.configure(c, opts={"render_as_batch": True}), Base.metadata))
    await eng.dispose()
    assert diff == [], diff


async def test_existing_db_without_version_is_stamped():
    """create_all で作られた既存 DB(conftest の DB がそれ)→ stamp head してから upgrade、エラーにならない"""
    await migrate.run_migrations()
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    assert "alembic_version" in tables
