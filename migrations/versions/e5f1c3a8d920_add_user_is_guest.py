"""add users.is_guest

登録せずに参加するゲストを区別する列。既存のユーザーはすべて登録済みなので false。

Revision ID: e5f1c3a8d920
Revises: d4e8a2b91c07
Create Date: 2026-11-17 12:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5f1c3a8d920'
down_revision: Union[str, Sequence[str], None] = 'd4e8a2b91c07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 既存行があるので NOT NULL 列には server_default が必要
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_guest', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('is_guest')
