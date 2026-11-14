"""注文の確定(決済は模擬):1つのトランザクションで 在庫の確認 → カートの確保 → 在庫を減らす → 注文を作る

同時にリクエストが来ても守る2つのこと(どちらも「条件付きの書き込み+変わった行数の確認」で守る。読んでから書く方式は使わない):
  - 売り越さない:     別々の人が同じ商品を買う → 条件付き UPDATE
  - 二重に注文しない:同じ人のリクエストが重なって届く → カートの行を id+数量 で削除し、削除できなければロールバック

売り越し防止の中心は「条件付き UPDATE」:
    UPDATE products SET stock = stock - :q WHERE id = :id AND is_active AND stock >= :q
判定と減算を DB の中で一度に行い、変わった行数が 1 でなければ在庫不足 → ロールバックして 409。
SQLite でも MySQL でも成り立つ(SQLite は FOR UPDATE を無視するので、行ロックだけには頼れない)。
MySQL では with_for_update() が行ロックもかけ、同時のリクエストを読み取りの段階で順番に並ばせる。
決済は模擬:画面の支払い画面で確定すると、このエンドポイントが呼ばれて支払い成功として扱う。
注文が成功したら購入を全員に知らせ、AI ホストのお礼をキューに入れる。
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
    # MySQL: SELECT ... FOR UPDATE で行ロック。SQLite はこの句を無視する(本当の保証は下の条件付き UPDATE)
    prows = await db.execute(select(Product).where(Product.id.in_(ids)).with_for_update())
    products = {p.id: p for p in prows.scalars()}

    # 事前チェック:分かりやすいエラーを返すため(同時に来た場合の最終判定は条件付き UPDATE)
    for c in cart:
        p = products.get(c.product_id)
        if not p or not p.is_active:
            raise HTTPException(409, f"商品(id={c.product_id})は販売終了しました")
        if p.stock < c.quantity:
            raise HTTPException(409, f"「{p.name}」の在庫が不足しています(残り{p.stock}点)")

    # ① まずカートを「確保」する:読み取った行を id + 数量 で1行ずつ削除し、本当に削除できたかを確認する。
    #    同じ人の注文が同時に届いたとき(ダブルクリック、通信の再送)、後から来た方はここで削除できない → 全体をロールバック。
    #    同じカートから注文が2つできることはない(在庫と同じく、前に読んだ値を信用せず、DB に1文で判定させる)
    for c in cart:
        res = await db.execute(
            delete(CartItem)
            .where(CartItem.id == c.id, CartItem.user_id == user.id, CartItem.quantity == c.quantity)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            await db.rollback()
            raise HTTPException(409, "カートの内容が変更されました。もう一度お試しください")

    # ② 1件ずつ在庫を減らす:Python で計算した値を書き戻すことはしない(同時に来ると更新が失われる=lost update)
    for c in cart:
        p = products[c.product_id]
        res = await db.execute(
            update(Product)
            .where(Product.id == p.id, Product.is_active == True, Product.stock >= c.quantity)  # noqa: E712
            .values(stock=Product.stock - c.quantity)
            .execution_options(synchronize_session=False)
        )
        if res.rowcount != 1:
            name = p.name           # rollback すると ORM オブジェクトが失効するので、先に取り出してからロールバック
            await db.rollback()
            raise HTTPException(409, f"「{name}」の在庫が不足しています")

    # ③ すべて減らせてから注文を作る(UPDATE の後に置く。先に作ると autoflush 後に order.items が非同期の遅延読み込みを起こす)
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

    # 減らした後の実際の在庫を配信する(メモリ上の p.stock は減らす前の古い値)
    srows = await db.execute(select(Product.id, Product.stock).where(Product.id.in_(ids)))
    await manager.broadcast({
        "type": "purchase",
        "nickname": order.nickname,
        "items": [{"name": i.product_name, "quantity": i.quantity} for i in order.items],
        "stocks": {str(pid): stock for pid, stock in srows},
    })
    # AI ホストのお礼(定型文。LLM は使わない。キューを通して、ほかの返答と重ならないようにする)
    names = [i.product_name for i in order.items]
    product_label = names[0] if len(names) == 1 else f"{names[0]}」と「{names[1]}" if len(names) == 2 \
        else f"{names[0]}」など{len(names)}点の商品"   # 外側の「」は定型文の側にある
    await host.enqueue_thanks(order.nickname, product_label)
    return order


@router.get("", response_model=list[OrderOut])
async def my_orders(limit: int = Query(30, ge=1, le=100),
                    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """自分の注文(新しい順)。件数が増えても応答が重くならないよう、上限を付けて返す"""
    rows = await db.execute(
        select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()).limit(limit))
    return rows.scalars().unique().all()
