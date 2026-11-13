"""起動時のDBマイグレーション(Alembic)

- 空のDB → `alembic upgrade head` で全テーブルを作成
- 以前 create_all で作ったDB(テーブルはあるが alembic_version が無い)
  → 現行スキーマと同じとみなして `stamp head`(基準点を記録)してから upgrade
- 本番では起動コマンドの前に `alembic upgrade head` を明示的に実行し、
  AUTO_MIGRATE=false にする運用も可能

Alembic の env.py は内部で asyncio.run() を使うため、実行中のイベントループからは
別スレッド(asyncio.to_thread)で呼び出す。
"""
import asyncio
import logging
import os

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from .config import settings
from .database import engine

logger = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config() -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False   # アプリのログ設定を上書きしない
    return cfg


async def run_migrations() -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    cfg = _alembic_config()
    if "users" in tables and "alembic_version" not in tables:
        logger.info("既存DB(マイグレーション未管理)を検出 → 現行スキーマとして stamp head")
        await asyncio.to_thread(command.stamp, cfg, "head")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("DBマイグレーション完了(head)")
