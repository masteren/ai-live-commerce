"""下単:在庫の原子的減算・ロールバック・同時購入での売り越し防止"""
import asyncio

from conftest import ADMIN, register


async def _stock(client, pid):
    return (await client.get(f"/api/products/{pid}")).json()["stock"]


async def test_checkout_decrements_stock_and_clears_cart(client):
    csrf = await register(client)
    before = await _stock(client, 1)
    await client.post("/api/cart", headers=csrf, json={"product_id": 1, "quantity": 2})
    r = await client.post("/api/orders/checkout", headers=csrf, json={"payment_method": "paypay"})
    assert r.status_code == 200
    order = r.json()
    assert order["items"][0]["quantity"] == 2 and order["status"] == "paid"
    assert await _stock(client, 1) == before - 2
    assert (await client.get("/api/cart")).json() == []


async def test_insufficient_stock_rolls_back_everything(client):
    csrf = await register(client)
    # 商品2は十分、商品3(在庫15)は不足 → どちらも減らず、注文もできず、カートは残る
    await client.post("/api/cart", headers=csrf, json={"product_id": 2, "quantity": 1})
    await client.post("/api/cart", headers=csrf, json={"product_id": 3, "quantity": 20})
    s2 = await _stock(client, 2)
    r = await client.post("/api/orders/checkout", headers=csrf, json={})
    assert r.status_code == 409
    assert await _stock(client, 2) == s2
    assert (await client.get("/api/orders")).json() == []
    assert len((await client.get("/api/cart")).json()) == 2


async def test_inactive_product_cannot_be_bought(client):
    csrf = await register(client)
    await client.post("/api/cart", headers=csrf, json={"product_id": 1, "quantity": 1})
    await client.delete("/api/admin/products/1", headers=ADMIN)
    r = await client.post("/api/orders/checkout", headers=csrf, json={})
    assert r.status_code == 409


async def test_invalid_payment_method(client):
    csrf = await register(client)
    await client.post("/api/cart", headers=csrf, json={"product_id": 1, "quantity": 1})
    r = await client.post("/api/orders/checkout", headers=csrf, json={"payment_method": "bitcoin"})
    assert r.status_code == 400


async def test_concurrent_checkout_never_oversells(client, make_client):
    """在庫1点に5人が同時に注文 → 成功は1件だけ、在庫は0(マイナスにならない)"""
    pid = (await client.post("/api/admin/products", headers=ADMIN,
                             json={"name": "限定1点", "price": 500, "stock": 1})).json()["id"]
    buyers = []
    for i in range(5):
        c = await make_client()
        csrf = await register(c, f"buyer_{i}")
        await c.post("/api/cart", headers=csrf, json={"product_id": pid, "quantity": 1})
        buyers.append((c, csrf))
    rs = await asyncio.gather(*(c.post("/api/orders/checkout", headers=h, json={}) for c, h in buyers))
    assert sorted(r.status_code for r in rs) == [200, 409, 409, 409, 409]
    assert await _stock(client, pid) == 0


async def test_same_user_concurrent_checkout_creates_one_order(client):
    """同じユーザーの注文リクエストが同時に5回届いても(ダブルクリック・再送)、注文は1件・在庫は1点だけ減る"""
    csrf = await register(client)
    before = await _stock(client, 1)
    await client.post("/api/cart", headers=csrf, json={"product_id": 1, "quantity": 1})
    rs = await asyncio.gather(*(client.post("/api/orders/checkout", headers=csrf, json={}) for _ in range(5)))
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) == 1 and all(c in (200, 400, 409) for c in codes), codes
    assert len((await client.get("/api/orders")).json()) == 1
    assert await _stock(client, 1) == before - 1


async def test_concurrent_add_to_cart_keeps_single_row(client):
    """同じ商品への「カートへ」が同時に5回届いても、カートは1行・数量5(以前は重複行ができ、以後 500 になった)"""
    csrf = await register(client)
    body = {"product_id": 2, "quantity": 1}
    rs = await asyncio.gather(*(client.post("/api/cart", headers=csrf, json=body) for _ in range(5)))
    assert [r.status_code for r in rs] == [200] * 5
    cart = (await client.get("/api/cart")).json()
    assert len(cart) == 1 and cart[0]["quantity"] == 5
    assert (await client.post("/api/cart", headers=csrf, json=body)).status_code == 200


async def test_cart_quantity_is_capped_at_99(client):
    csrf = await register(client)
    await client.post("/api/cart", headers=csrf, json={"product_id": 2, "quantity": 60})
    cart = (await client.post("/api/cart", headers=csrf, json={"product_id": 2, "quantity": 60})).json()
    assert cart[0]["quantity"] == 99


async def test_db_rejects_negative_stock_and_bad_quantity():
    """CHECK 制約:アプリのコードを通さずに直接更新しても、在庫マイナスや数量0は DB が拒否する"""
    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError
    from app.database import SessionLocal
    for sql in ("UPDATE products SET stock = -1 WHERE id = 1",
                "UPDATE products SET price = -100 WHERE id = 1",
                "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (1, 1, 0)"):
        async with SessionLocal() as db:
            if sql.startswith("INSERT"):
                await db.execute(text("INSERT INTO users (id, username, password_hash, created_at) "
                                      "VALUES (1, 'u1', 'x', CURRENT_TIMESTAMP)"))
            with pytest.raises(DBAPIError):
                await db.execute(text(sql))
                await db.commit()
