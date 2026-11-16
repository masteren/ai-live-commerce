"""起動時のDBマイグレーション(Alembic)

- 起動のたびに `alembic upgrade head`(空の DB なら全テーブルを作り、既存の DB なら足りない分だけ適用)
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
from .config import settings

logger = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config() -> Config:
    cfg = Config(os.path.join(ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False   # アプリのログ設定を上書きしない
    return cfg


async def run_migrations() -> None:
    await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
    logger.info("DBマイグレーション完了(head)")
