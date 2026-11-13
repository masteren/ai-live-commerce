"""add product image_url

商品に画像 URL の列を追加し、既存の初期商品には用意した画像を設定する(スキーマ変更 + データ移行)。

Revision ID: 61d35b77307b
Revises: 0ca0b19dc820
Create Date: 2026-11-13 12:19:58.127034

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61d35b77307b'
down_revision: Union[str, Sequence[str], None] = '0ca0b19dc820'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 初期商品(app/main.py の SEED_PRODUCTS)と画像の対応
SEED_IMAGES = {
    "ワイヤレスイヤホン Pro": "/static/products/earphones.png",
    "ステンレス保温マグ 480ml": "/static/products/mug.png",
    "ヒアルロン酸美容液 30ml": "/static/products/serum.png",
}


def upgrade() -> None:
    """Upgrade schema."""
    # 1) スキーマ変更:既存行があるので NOT NULL 列には server_default が必要
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.add_column(sa.Column('image_url', sa.String(length=255), server_default='', nullable=False))

    # 2) データ移行:既存の初期商品に画像を設定(商品名で対応付け。管理画面で追加した商品は空のまま=絵文字表示)
    products = sa.table("products", sa.column("name", sa.String), sa.column("image_url", sa.String))
    for name, url in SEED_IMAGES.items():
        op.execute(products.update().where(products.c.name == name).values(image_url=url))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('products', schema=None) as batch_op:
        batch_op.drop_column('image_url')
