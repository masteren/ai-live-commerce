"""複数の場所から使う DB クエリ(同じ条件を何箇所にも書かない)"""
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Comment, Product, iso_utc


async def list_active_products(db: AsyncSession) -> List[Product]:
    """販売中の商品(id 順)。視聴者向け API・AI ホスト・コメント BOT が共通で使う"""
    rows = await db.execute(select(Product).where(Product.is_active.is_(True)).order_by(Product.id))
    return list(rows.scalars())


async def recent_comments(db: AsyncSession, limit: int = 30) -> List[dict]:
    """最近のコメント(古い順)。ライブに入った人に、それまでの流れを見せるために使う"""
    rows = await db.execute(select(Comment).order_by(Comment.id.desc()).limit(limit))
    return [
        {"nickname": c.nickname, "text": c.text, "ts": iso_utc(c.created_at),
         "kind": "host" if c.is_host else "bot" if c.is_bot else "user"}
        for c in reversed(list(rows.scalars()))
    ]
