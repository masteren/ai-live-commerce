"""複数の場所から使う DB クエリ(同じ条件を何箇所にも書かない)"""
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Product


async def list_active_products(db: AsyncSession) -> List[Product]:
    """販売中の商品(id 順)。視聴者向け API・AI ホスト・コメント BOT が共通で使う"""
    rows = await db.execute(select(Product).where(Product.is_active.is_(True)).order_by(Product.id))
    return list(rows.scalars())
