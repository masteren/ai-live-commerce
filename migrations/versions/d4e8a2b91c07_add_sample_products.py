"""add sample products

見本の商品を3点追加する(トップページの商品一覧が3点だけで寂しかったため)。
すでに運用中の DB にだけ追加する。空の DB は、起動時の初期データ投入(app/main.py の SEED_PRODUCTS)が6点まとめて入れる。

Revision ID: d4e8a2b91c07
Revises: c3a1f0d27b10
Create Date: 2026-11-15

"""
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e8a2b91c07'
down_revision: Union[str, Sequence[str], None] = 'c3a1f0d27b10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_PRODUCTS = [
    dict(name="モバイルバッテリー 10000mAh", emoji="🔋", image_url="/static/products/battery.png", price=2980, original_price=3980, stock=40,
         tag="新着", description="薄型・軽量180g、2台同時充電、残量ランプつき"),
    dict(name="アロマディフューザー", emoji="💧", image_url="/static/products/diffuser.png", price=3480, original_price=4980, stock=25,
         tag="ギフトに人気", description="超音波式で静か、やさしい灯りつき、最大8時間の連続運転"),
    dict(name="コットンフェイスタオル 2枚組", emoji="🧺", image_url="/static/products/towels.png", price=1480, original_price=1980, stock=80,
         tag="まとめ買い", description="オーガニックコットン100%、ふんわり厚手、生成りとセージの2色"),
]

products = sa.table(
    "products",
    sa.column("name", sa.String), sa.column("description", sa.Text), sa.column("emoji", sa.String),
    sa.column("image_url", sa.String), sa.column("price", sa.Integer), sa.column("original_price", sa.Integer),
    sa.column("stock", sa.Integer), sa.column("tag", sa.String), sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    conn = op.get_bind()
    # 空の DB には入れない(ここで入れると初期データ投入が「すでに商品がある」と判断して、元の3点が入らなくなる)
    if conn.execute(sa.select(sa.func.count()).select_from(products)).scalar() == 0:
        return
    existing = {row[0] for row in conn.execute(sa.select(products.c.name))}
    now = datetime.now(timezone.utc)
    for p in NEW_PRODUCTS:
        if p["name"] not in existing:         # 同じ名前の商品があれば何もしない(2回実行しても増えない)
            conn.execute(products.insert().values(is_active=True, created_at=now, **p))


def downgrade() -> None:
    # 注文やカートから参照されている可能性があるので削除せず、販売停止にする
    conn = op.get_bind()
    conn.execute(products.update().where(products.c.name.in_([p["name"] for p in NEW_PRODUCTS])).values(is_active=False))
