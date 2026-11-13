"""起動時のDBマイグレーション(Alembic)

- 空のDB → `alembic upgrade head` で全テーブルを作成
- 以前 create_all で作ったDB(テーブルはあるが alembic_version が無い)
  → Alembic 導入時点のスキーマ(BASELINE)として記録してから、以降のマイグレーションを適用
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


# Alembic を導入した時点のスキーマ(最初のマイグレーション)。
# それ以前に create_all で作られた DB は、この版と同じ構造のはずなので、ここを基準点として記録する
BASELINE = "0ca0b19dc820"


async def run_migrations() -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    cfg = _alembic_config()
    if "users" in tables and "alembic_version" not in tables:
        if "auth_sessions" not in tables:
            # 基準点よりさらに古い DB(認証改修の前)。構造が違うので自動では直せない → 黙って進まず止める
            raise RuntimeError(
                "データベースが古い形式です(マイグレーション導入前)。バックアップを取ってから DB を削除し、再起動してください。")
        # 注意:ここで head を記録してはいけない。head と記録すると、基準点より後のマイグレーション
        # (列の追加など)が「適用済み」扱いになり、実際の DB には列が無いままになる
        logger.info("既存DB(マイグレーション未管理)を検出 → 基準点 %s を記録してから最新まで適用", BASELINE)
        await asyncio.to_thread(command.stamp, cfg, BASELINE)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("DBマイグレーション完了(head)")
