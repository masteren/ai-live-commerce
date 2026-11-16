"""見本の商品(初回起動時に登録する)"""
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Product

logger = logging.getLogger(__name__)

SEED_PRODUCTS = [
    dict(name="ワイヤレスイヤホン Pro", emoji="🎧", image_url="/static/products/earphones.png", price=4980, original_price=7980, stock=30,
         tag="タイムセール", description="ノイズキャンセリング搭載、最大32時間再生、IPX5防水"),
    dict(name="ステンレス保温マグ 480ml", emoji="🥤", image_url="/static/products/mug.png", price=1880, original_price=2480, stock=60,
         tag="人気No.1", description="12時間保温・保冷、ワンタッチ開閉、食洗機OK"),
    dict(name="ヒアルロン酸美容液 30ml", emoji="✨", image_url="/static/products/serum.png", price=2680, original_price=3980, stock=15,
         tag="残りわずか", description="高濃度ヒアルロン酸配合、無香料・無着色、敏感肌OK"),
    # ↓ あとから追加した3点。運用中の DB にはマイグレーション d4e8a2b91c07 が同じ内容を追加する
    dict(name="モバイルバッテリー 10000mAh", emoji="🔋", image_url="/static/products/battery.png", price=2980, original_price=3980, stock=40,
         tag="新着", description="薄型・軽量180g、2台同時充電、残量ランプつき"),
    dict(name="アロマディフューザー", emoji="💧", image_url="/static/products/diffuser.png", price=3480, original_price=4980, stock=25,
         tag="ギフトに人気", description="超音波式で静か、やさしい灯りつき、最大8時間の連続運転"),
    dict(name="コットンフェイスタオル 2枚組", emoji="🧺", image_url="/static/products/towels.png", price=1480, original_price=1980, stock=80,
         tag="まとめ買い", description="オーガニックコットン100%、ふんわり厚手、生成りとセージの2色"),
]


async def seed_products(db: AsyncSession) -> None:
    """商品が1つもないときだけ見本を登録する(運用中の DB は触らない)"""
    if (await db.execute(select(func.count(Product.id)))).scalar():
        return
    db.add_all(Product(**p) for p in SEED_PRODUCTS)
    await db.commit()
    logger.info("見本の商品を登録しました(初回起動)")
