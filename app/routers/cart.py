"""购物车接口:登录用户绑定"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
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

    row = await db.execute(
        select(CartItem).where(CartItem.user_id == user.id, CartItem.product_id == body.product_id)
    )
    item = row.scalar_one_or_none()
    if item:
        item.quantity = min(item.quantity + body.quantity, 99)
    else:
        db.add(CartItem(user_id=user.id, product_id=body.product_id, quantity=body.quantity))
    await db.commit()

    rows = await db.execute(select(CartItem).where(CartItem.user_id == user.id))
    return rows.scalars().all()


@router.delete("/{item_id}")
async def remove_item(item_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(CartItem).where(CartItem.id == item_id, CartItem.user_id == user.id))
    await db.commit()
    return {"ok": True}
