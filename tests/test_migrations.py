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


async def test_legacy_db_gets_later_migrations_applied():
    """マイグレーション管理前の DB(alembic_version なし)→ 基準点を記録し、それ以降のマイグレーションが実際に適用される。
    以前は head と記録してしまい、image_url 列が無いのに「最新」扱いになっていた"""
    from sqlalchemy import text
    cfg = migrate._alembic_config()
    async with engine.begin() as conn:                       # conftest が作った表を消し、基準点の構造を再現
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await asyncio.to_thread(command.upgrade, cfg, migrate.BASELINE)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE alembic_version"))

    await migrate.run_migrations()

    async with engine.connect() as conn:
        cols = await conn.run_sync(lambda c: [k["name"] for k in inspect(c).get_columns("products")])
        uniques = await conn.run_sync(lambda c: [u["name"] for u in inspect(c).get_unique_constraints("cart_items")])
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE alembic_version"))   # 後続テストに影響しないよう片付け
    assert "image_url" in cols and "uq_cart_user_product" in uniques


async def test_pre_baseline_db_is_rejected_with_clear_error():
    """基準点より古い DB(auth_sessions が無い)は自動移行せず、分かりやすいエラーで止める"""
    import pytest
    from sqlalchemy import text
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE auth_sessions"))
    with pytest.raises(RuntimeError, match="古い形式"):
        await migrate.run_migrations()


async def test_image_url_data_migration_fills_seed_products(tmp_path):
    """旧スキーマで登録済みの初期商品 → upgrade 後に画像 URL が入る(データ移行)。手動追加の商品は空のまま"""
    from sqlalchemy import text
    url = f"sqlite+aiosqlite:///{tmp_path}/old.db"
    cfg = migrate._alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "0ca0b19dc820")      # image_url が無い頃のスキーマ

    eng = create_async_engine(url)
    async with eng.begin() as conn:
        for name in ("ステンレス保温マグ 480ml", "手動で追加した商品"):
            await conn.execute(text(
                "INSERT INTO products (name, description, emoji, price, original_price, stock, tag, is_active, created_at)"
                " VALUES (:n, '', '🛍️', 100, 0, 1, '', 1, CURRENT_TIMESTAMP)"), {"n": name})
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with eng.connect() as conn:
        rows = dict((await conn.execute(text("SELECT name, image_url FROM products"))).all())
    await eng.dispose()
    assert rows["ステンレス保温マグ 480ml"] == "/static/products/mug.png"
    assert rows["手動で追加した商品"] == ""


async def test_cart_unique_migration_merges_existing_duplicates(tmp_path):
    """重複行が残っている DB でも upgrade できる(数量を合算して1行にまとめてから一意制約を付ける)"""
    from sqlalchemy import text
    url = f"sqlite+aiosqlite:///{tmp_path}/dup.db"
    cfg = migrate._alembic_config()
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "61d35b77307b")       # 一意制約が無い頃のスキーマ
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        for qty in (1, 2, 3):
            await conn.execute(text("INSERT INTO cart_items (user_id, product_id, quantity) VALUES (1, 2, :q)"), {"q": qty})
        await conn.execute(text("INSERT INTO cart_items (user_id, product_id, quantity) VALUES (1, 3, 1)"))
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with eng.connect() as conn:
        rows = (await conn.execute(text("SELECT product_id, quantity FROM cart_items ORDER BY product_id"))).all()
    await eng.dispose()
    assert [tuple(r) for r in rows] == [(2, 6), (3, 1)]


async def test_sample_products_migration_adds_to_existing_db_only(tmp_path):
    """見本商品の追加:運用中の DB には不足分だけ追加し(同名があれば増やさない)、空の DB には何も入れない"""
    from sqlalchemy import text
    cfg = migrate._alembic_config()

    # 1) 商品がすでにある DB(うち1点は、追加予定の商品と同じ名前)
    url = f"sqlite+aiosqlite:///{tmp_path}/live.db"
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "c3a1f0d27b10")
    eng = create_async_engine(url)
    async with eng.begin() as conn:
        for name in ("ワイヤレスイヤホン Pro", "アロマディフューザー"):
            await conn.execute(text(
                "INSERT INTO products (name, description, emoji, image_url, price, original_price, stock, tag, is_active, created_at)"
                " VALUES (:n, '', '🛍️', '', 100, 0, 1, '', 1, CURRENT_TIMESTAMP)"), {"n": name})
    await asyncio.to_thread(command.upgrade, cfg, "head")
    async with eng.connect() as conn:
        names = [r[0] for r in (await conn.execute(text("SELECT name FROM products ORDER BY id"))).all()]
        price = (await conn.execute(text("SELECT price FROM products WHERE name = 'アロマディフューザー'"))).scalar_one()
    await eng.dispose()
    assert len(names) == 4 and names.count("アロマディフューザー") == 1 and "モバイルバッテリー 10000mAh" in names
    assert price == 100                                  # すでにあった商品は書き換えない

    # 2) 空の DB:何も入れない(初期データ投入が6点まとめて入れるため)
    url = f"sqlite+aiosqlite:///{tmp_path}/empty.db"
    cfg.set_main_option("sqlalchemy.url", url)
    await asyncio.to_thread(command.upgrade, cfg, "head")
    eng = create_async_engine(url)
    async with eng.connect() as conn:
        count = (await conn.execute(text("SELECT COUNT(*) FROM products"))).scalar_one()
    await eng.dispose()
    assert count == 0
