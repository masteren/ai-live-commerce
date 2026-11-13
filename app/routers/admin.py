"""管理后台接口:X-Admin-Token 头鉴权(值 = 环境变量 ADMIN_TOKEN)"""
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from ..ai_host import host
from ..bot import bot
from ..config import settings
from ..database import get_db
from ..models import Comment, Order, OrderItem, Product, User
from ..schemas import OrderOut, ProductCreate, ProductOut, ProductUpdate, StatsOut
from ..ws import manager

router = APIRouter(prefix="/api/admin", tags=["admin"])


def require_admin(x_admin_token: str = Header(default="")):
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(401, "管理者トークンが正しくありません")


# ---- 商品管理 ----
@router.get("/products", response_model=list[ProductOut], dependencies=[Depends(require_admin)])
async def all_products(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Product).order_by(Product.id))
    return rows.scalars().all()


@router.post("/products", response_model=ProductOut, dependencies=[Depends(require_admin)])
async def create_product(body: ProductCreate, db: AsyncSession = Depends(get_db)):
    product = Product(**body.model_dump())
    db.add(product)
    await db.commit()
    await db.refresh(product)
    await manager.broadcast({"type": "products_changed"})
    await host.refresh_product_names()
    return product


@router.patch("/products/{product_id}", response_model=ProductOut, dependencies=[Depends(require_admin)])
async def update_product(product_id: int, body: ProductUpdate, db: AsyncSession = Depends(get_db)):
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "商品が見つかりません")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(product, k, v)
    await db.commit()
    await db.refresh(product)
    await manager.broadcast({"type": "products_changed"})
    await host.refresh_product_names()
    return product


@router.delete("/products/{product_id}", dependencies=[Depends(require_admin)])
async def delete_product(product_id: int, db: AsyncSession = Depends(get_db)):
    """软删除:下架而非物理删除,保住历史订单引用"""
    product = await db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "商品が見つかりません")
    product.is_active = False
    await db.commit()
    await manager.broadcast({"type": "products_changed"})
    return {"ok": True}


# ---- 数据统计 ----
@router.get("/stats", response_model=StatsOut, dependencies=[Depends(require_admin)])
async def stats(db: AsyncSession = Depends(get_db)):
    total_orders = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    total_revenue = (await db.execute(select(func.coalesce(func.sum(Order.total), 0)))).scalar() or 0
    total_comments = (await db.execute(
        select(func.count(Comment.id)).where(Comment.is_host == False)  # noqa: E712
    )).scalar() or 0
    active_products = (await db.execute(
        select(func.count(Product.id)).where(Product.is_active == True)  # noqa: E712
    )).scalar() or 0
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0

    top = await db.execute(
        select(
            OrderItem.product_name,
            func.sum(OrderItem.quantity).label("sold"),
            func.sum(OrderItem.quantity * OrderItem.unit_price).label("revenue"),
        ).group_by(OrderItem.product_name).order_by(func.sum(OrderItem.quantity).desc()).limit(5)
    )
    return StatsOut(
        total_orders=total_orders,
        total_revenue=total_revenue,
        total_comments=total_comments,
        total_users=total_users,
        active_products=active_products,
        bot_enabled=bot.enabled,
        current_viewers=manager.viewer_count,
        peak_viewers=manager.peak_viewers,
        top_products=[{"name": r[0], "sold": int(r[1]), "revenue": int(r[2])} for r in top],
    )


@router.get("/orders", response_model=list[OrderOut], dependencies=[Depends(require_admin)])
async def recent_orders(db: AsyncSession = Depends(get_db)):
    rows = await db.execute(select(Order).order_by(Order.id.desc()).limit(30))
    return rows.scalars().unique().all()


# ---- 气氛组机器人开关 ----
class BotToggle(BaseModel):
    enabled: bool


@router.post("/bot", dependencies=[Depends(require_admin)])
async def toggle_bot(body: BotToggle):
    bot.set_enabled(body.enabled)
    return {"enabled": bot.enabled}
