"""カートの API(ログインしたユーザーごと)"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import CartItem, Product, User
from ..schemas import CartAdd, CartItemOut
from .auth import get_current_user

router = APIRouter(prefix="/api/cart", tags=["cart"])


@router.get("", response_model=list[CartItemOut])
async def get_cart(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(CartItem).where(CartItem.user_id == user.id))
    return rows.scalars().all()


@router.post("", response_model=list[CartItemOut])
async def add_to_cart(body: CartAdd, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    product = await db.get(Product, body.product_id)
    if not product or not product.is_active:
        raise HTTPException(404, "商品が見つかりません")
    if product.stock <= 0:
        raise HTTPException(409, "在庫切れです")

    user_id = user.id      # rollback すると ORM オブジェクトが失効するので先に取り出す
    # まず追加を試み、すでに行があれば(一意制約 uq_cart_user_product に当たれば)数量の加算に切り替える。
    # 「読んでから書く」をやめ、判定を DB の制約に任せることで、同時リクエストでも重複行・加算漏れが起きない。
    # ※「先に UPDATE、0件なら INSERT」の順にすると、MySQL(InnoDB)ではギャップロック同士でデッドロックする
    db.add(CartItem(user_id=user_id, product_id=body.product_id, quantity=body.quantity))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        new_qty = case((CartItem.quantity + body.quantity > 99, 99), else_=CartItem.quantity + body.quantity)
        await db.execute(
            update(CartItem)
            .where(CartItem.user_id == user_id, CartItem.product_id == body.product_id)
            .values(quantity=new_qty)             # 加算は DB 内で1文(上限 99)
            .execution_options(synchronize_session=False)
        )
        await db.commit()

    rows = await db.execute(select(CartItem).where(CartItem.user_id == user_id))
    return rows.scalars().all()


@router.delete("/{item_id}")
async def remove_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(CartItem).where(CartItem.id == item_id, CartItem.user_id == user.id))
    await db.commit()
    return {"ok": True}
