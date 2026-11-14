"""check constraints

在庫・価格・カート数量に CHECK 制約を追加する(アプリの不具合があっても、不正な値を DB が拒否する)。

Revision ID: c3a1f0d27b10
Revises: a7be66a3f3ae
Create Date: 2026-11-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3a1f0d27b10'
down_revision: Union[str, Sequence[str], None] = 'a7be66a3f3ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) データ移行:制約に違反する行が残っていると制約を追加できないので、先に範囲内に直す
    op.execute("UPDATE products SET stock = 0 WHERE stock < 0")
    op.execute("UPDATE products SET price = 0 WHERE price < 0")
    op.execute("UPDATE products SET original_price = 0 WHERE original_price < 0")
    op.execute("DELETE FROM cart_items WHERE quantity < 1")
    op.execute("UPDATE cart_items SET quantity = 99 WHERE quantity > 99")

    # 2) スキーマ変更
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.create_check_constraint('ck_products_stock_nonneg', 'stock >= 0')
        batch_op.create_check_constraint('ck_products_price_nonneg', 'price >= 0 AND original_price >= 0')
    with op.batch_alter_table('cart_items', schema=None) as batch_op:
        batch_op.create_check_constraint('ck_cart_quantity_range', 'quantity >= 1 AND quantity <= 99')


def downgrade() -> None:
    with op.batch_alter_table('cart_items', schema=None) as batch_op:
        batch_op.drop_constraint('ck_cart_quantity_range', type_='check')
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_constraint('ck_products_price_nonneg', type_='check')
        batch_op.drop_constraint('ck_products_stock_nonneg', type_='check')
