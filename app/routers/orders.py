"""下单结算(模拟支付):单事务内 校验库存 → 认领购物车 → 原子扣减库存 → 生成订单

并发下的两个保证(都靠「带条件的写入 + 检查影响行数」,而不是先读后写):
  - 不超卖:   不同用户抢同一件商品 → 条件 UPDATE
  - 不重复下单:同一用户的请求重复到达 → 按 id+数量 删除购物车行,删不到就回滚

防超卖的核心是「条件 UPDATE」:
    UPDATE products SET stock = stock - :q WHERE id = :id AND is_active AND stock >= :q
判断与扣减在 DB 内一步完成,影响行数 != 1 即库存不足 → 回滚返回 409。
SQLite / PostgreSQL 都成立(SQLite 会忽略 FOR UPDATE,不能单靠行锁)。
PostgreSQL 下 with_for_update() 另外加行锁,让并发请求在读取阶段就排队。
支付为模拟:前端支付页确认后调用本接口即视为支付成功。
下单成功后广播购买事件,并让 AI 主播排队语音道谢。
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_host import host
from ..database import get_db
from ..models import CartItem, Order, OrderItem, Product, User
from ..schemas import CheckoutIn, OrderOut
from ..ws import manager
from .auth import get_current_user

router = APIRouter(prefix="/api/orders", tags=["orders"])

PAYMENT_METHODS = {"credit_card", "convenience_store", "paypay"}


@router.post("/checkout", response_model=OrderOut)
async def checkout(body: CheckoutIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if body.payment_method not in PAYMENT_METHODS:
        raise HTTPException(400, "不明な支払い方法です")

    rows = await db.execute(select(CartItem).where(CartItem.user_id == user.id))
    cart = list(rows.scalars())
    if not cart:
        raise HTTPException(400, "カートが空です")

    ids = [c.product_id for c in cart]
    # PostgreSQL: SELECT ... FOR UPDATE 行锁;SQLite 忽略该子句(真正的保证在下方条件 UPDATE)
    prows = await db.execute(select(Product).where(Product.id.in_(ids)).with_for_update())
    products = {p.id: p for p in prows.scalars()}

    # 预检查:给出友好的错误信息(并发下的最终判定以条件 UPDATE 为准)
    for c in cart:
        p = products.get(c.product_id)
        if not p or not p.is_active:
            raise HTTPException(409, f"商品(id={c.product_id})は販売終了しました")
        if p.stock < c.quantity:
            raise HTTPException(409, f"「{p.name}」の在庫が不足しています(残り{p.stock}点)")

    # ① 先「认领」购物车:按 id + 数量 删除自己读到的每一行,检查确实删掉了。
    #    同一用户的下单请求同时到达时(双击、网络重发),后到的请求在这里删不到行 → 整单回滚,
    #    不会用同一个购物车生成两张订单。(和扣库存一样:不相信之前读到的值,让 DB 在一条语句里判定)
    for c in cart:
        res = await db.execute(
            delete(CartItem)
            .where(CartItem.id == c.id, CartItem.user_id == user.id, CartItem.quantity == c.quantity)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "カートの内容が変更されました。もう一度お試しください")

    # ② 逐件原子扣减库存:不在 Python 里算好绝对值再写回(那样并发时会 lost update)
    for c in cart:
        p = products[c.product_id]
        res = await db.execute(
            update(Product)
            .where(Product.id == p.id, Product.is_active == True, Product.stock >= c.quantity)  # noqa: E712
            .values(stock=Product.stock - c.quantity)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            name = p.name           # rollback 会让 ORM 对象过期,先取出再回滚
            await db.rollback()
            raise HTTPException(409, f"「{name}」の在庫が不足しています")

    # ③ 全部扣减成功后再建订单(放在 UPDATE 之后,避免 autoflush 后访问 order.items 触发异步 lazy load)
    order = Order(
        user_id=user.id, nickname=user.username, payment_method=body.payment_method,
        total=sum(products[c.product_id].price * c.quantity for c in cart),
        items=[
            OrderItem(product_id=c.product_id, product_name=products[c.product_id].name,
                      unit_price=products[c.product_id].price, quantity=c.quantity)
            for c in cart
        ],
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)

    # 广播扣减后的真实库存(内存中的 p.stock 是扣减前的旧值)
    srows = await db.execute(select(Product.id, Product.stock).where(Product.id.in_(ids)))
    await manager.broadcast({
        "type": "purchase",
        "nickname": order.nickname,
        "items": [{"name": i.product_name, "quantity": i.quantity} for i in order.items],
        "stocks": {str(pid): stock for pid, stock in srows},
    })
    # AI 主播语音道谢(模板生成,不耗 LLM;走队列保证与回答不抢话)
    names = [i.product_name for i in order.items]
    product_label = names[0] if len(names) == 1 else f"{names[0]}」と「{names[1]}" if len(names) == 2 \
        else f"{names[0]}」など{len(names)}点の商品"   # 模板外层已有「」
    await host.enqueue_thanks(order.nickname, product_label)
    return order


@router.get("", response_model=list[OrderOut])
async def my_orders(limit: int = Query(30, ge=1, le=100),
                    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """自分の注文(新しい順)。件数が増えても応答が重くならないよう、上限を付けて返す"""
    rows = await db.execute(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(limit))
    return rows.scalars().unique().all()
