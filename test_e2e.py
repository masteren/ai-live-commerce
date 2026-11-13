"""端到端测试 v3:认证 → 商品 → 购物车 → 模拟支付下单 → 并发超卖 → 管理后台/机器人 → WebSocket 全链路

前提:服务端用【空的独立数据库】启动(会注册用户、下单),不要对 live.db 跑。
目标地址可用环境变量 E2E_BASE 覆盖(默认 http://127.0.0.1:8000)。
"""
import asyncio
import json
import os

import httpx
import websockets

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000").rstrip("/")
WS = BASE.replace("http", "ws", 1) + "/ws/live"
ADMIN = {"X-Admin-Token": "test-token"}
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'✅' if cond else '❌'} {name} {detail}")


async def drain(w, seconds=0.6):
    msgs = []
    end = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < end:
        try:
            msgs.append(json.loads(await asyncio.wait_for(w.recv(), 0.3)))
        except asyncio.TimeoutError:
            pass
    return msgs


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as c:
        # ---- 认证 ----
        r = await c.post("/api/auth/register", json={"username": "taro", "password": "pass1234"})
        check("注册用户", r.status_code == 200 and len(r.json()["token"]) == 64)
        token = r.json()["token"]
        H = {"X-Auth-Token": token}

        r = await c.post("/api/auth/register", json={"username": "taro", "password": "xxxxxx"})
        check("重名注册拒绝(409)", r.status_code == 409)
        r = await c.post("/api/auth/login", json={"username": "taro", "password": "wrongpw"})
        check("错误密码拒绝(401)", r.status_code == 401)
        r = await c.post("/api/auth/login", json={"username": "taro", "password": "pass1234"})
        check("登录并刷新token", r.status_code == 200 and r.json()["token"] != token)
        token = r.json()["token"]; H = {"X-Auth-Token": token}
        r = await c.get("/api/auth/me", headers=H)
        check("me接口", r.json().get("username") == "taro")

        # ---- 商品 / 购物车(鉴权) ----
        prods = (await c.get("/api/products")).json()
        check("商品列表", len(prods) == 3, f"{len(prods)}件")
        p1 = prods[0]
        r = await c.post("/api/cart", json={"product_id": p1["id"], "quantity": 2})
        check("未登录购物车拒绝(401)", r.status_code == 401)
        await c.post("/api/cart", headers=H, json={"product_id": p1["id"], "quantity": 2})
        r = await c.post("/api/cart", headers=H, json={"product_id": p1["id"], "quantity": 1})
        cart = r.json()
        check("购物车合并数量", len(cart) == 1 and cart[0]["quantity"] == 3)

        # ---- 模拟支付下单 ----
        r = await c.post("/api/orders/checkout", headers=H, json={"payment_method": "paypay"})
        order = r.json()
        check("下单成功(PayPay)", r.status_code == 200 and order["total"] == p1["price"] * 3
              and order["payment_method"] == "paypay" and order["nickname"] == "taro")
        r = await c.get(f"/api/products/{p1['id']}")
        check("库存扣减", r.json()["stock"] == p1["stock"] - 3, f"stock={r.json()['stock']}")
        check("下单后清空购物车", (await c.get("/api/cart", headers=H)).json() == [])
        r = await c.post("/api/orders/checkout", headers=H, json={"payment_method": "bitcoin"})
        check("非法支付方式拒绝(400)", r.status_code == 400)
        await c.post("/api/cart", headers=H, json={"product_id": p1["id"], "quantity": 99})
        r = await c.post("/api/orders/checkout", headers=H, json={"payment_method": "credit_card"})
        check("超卖保护(409)", r.status_code == 409, r.json().get("detail", ""))
        cid = (await c.get("/api/cart", headers=H)).json()[0]["id"]
        await c.delete(f"/api/cart/{cid}", headers=H)
        r = await c.get("/api/orders", headers=H)
        check("我的订单", len(r.json()) == 1)

        # ---- 管理后台 ----
        check("无token拒绝(401)", (await c.get("/api/admin/stats")).status_code == 401)
        r = await c.post("/api/admin/products", headers=ADMIN, json={
            "name": "テスト商品", "price": 1000, "stock": 5, "emoji": "🧸"})
        check("管理端上架商品", r.status_code == 200)
        nid = r.json()["id"]
        check("管理端改库存", (await c.patch(f"/api/admin/products/{nid}", headers=ADMIN,
                                        json={"stock": 8})).json()["stock"] == 8)
        check("管理端下架(软删除)", (await c.delete(f"/api/admin/products/{nid}", headers=ADMIN)).status_code == 200)
        s = (await c.get("/api/admin/stats", headers=ADMIN)).json()
        check("统计接口(含用户数/bot状态)", s["total_orders"] == 1 and s["total_users"] == 1
              and s["bot_enabled"] is False, f"revenue={s['total_revenue']}")

        # ---- 并发超卖:库存1件,5人同时下单 → 只能成功1单 ----
        rid = (await c.post("/api/admin/products", headers=ADMIN, json={
            "name": "限定1点", "price": 500, "stock": 1})).json()["id"]
        racers = []
        for i in range(5):
            rr = await c.post("/api/auth/register", json={"username": f"racer_{i}", "password": "pass1234"})
            racers.append({"X-Auth-Token": rr.json()["token"]})
        for h in racers:
            await c.post("/api/cart", headers=h, json={"product_id": rid, "quantity": 1})
        rs = await asyncio.gather(*(c.post("/api/orders/checkout", headers=h, json={}) for h in racers))
        codes = sorted(x.status_code for x in rs)
        check("并发下单仅1单成功(库存1·5人)", codes == [200, 409, 409, 409, 409], f"status={codes}")
        stock = next(p["stock"] for p in (await c.get("/api/admin/products", headers=ADMIN)).json() if p["id"] == rid)
        check("并发后库存=0(不为负/未丢失扣减)", stock == 0, f"stock={stock}")
        await c.delete(f"/api/admin/products/{rid}", headers=ADMIN)

        # ---- WebSocket:未登录看得见发不出;登录后弹幕广播+AI主播 ----
        async with websockets.connect(WS) as wa, websockets.connect(WS) as wb:
            await drain(wa); await drain(wb)
            await wa.send(json.dumps({"type": "comment", "token": "", "text": "hello"}))
            msgs = await drain(wa, 1.0)
            check("未登录发言被拒", any(m["type"] == "error" for m in msgs))

            await wa.send(json.dumps({"type": "comment", "token": token, "text": "イヤホンの音質どう?"}))
            got_comment_b = got_host = False; host_msg = {}
            deadline = asyncio.get_event_loop().time() + 12
            while asyncio.get_event_loop().time() < deadline and not (got_comment_b and got_host):
                try:
                    m = json.loads(await asyncio.wait_for(wb.recv(), 2))
                except asyncio.TimeoutError:
                    continue
                if m["type"] == "comment" and m["nickname"] == "taro":
                    got_comment_b = True
                if m["type"] == "host_reply":
                    got_host = True; host_msg = m
            check("弹幕跨客户端广播", got_comment_b)
            check("AI主播回答(问题必答·降级模式)", got_host and host_msg.get("emotion") in
                  {"normal", "happy", "excited", "troubled", "shy"},
                  f"emotion={host_msg.get('emotion')} | {host_msg.get('text','')[:30]}")

            # ---- 购买 → 广播 + 主播道谢 ----
            await c.post("/api/cart", headers=H, json={"product_id": 2, "quantity": 1})
            await c.post("/api/orders/checkout", headers=H, json={"payment_method": "convenience_store"})
            got_purchase = got_thanks = False
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline and not (got_purchase and got_thanks):
                try:
                    m = json.loads(await asyncio.wait_for(wb.recv(), 2))
                except asyncio.TimeoutError:
                    continue
                if m["type"] == "purchase":
                    got_purchase = "stocks" in m and m["nickname"] == "taro"
                if m["type"] == "host_reply" and "taro" in m.get("text", ""):
                    got_thanks = m.get("emotion") == "excited"
            check("购买事件广播", got_purchase)
            check("主播语音道谢(excited)", got_thanks)

            # ---- 气氛组机器人 ----
            r = await c.post("/api/admin/bot", headers=ADMIN, json={"enabled": True})
            check("bot开关API", r.json()["enabled"] is True)
            got_bot = False
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline and not got_bot:
                try:
                    m = json.loads(await asyncio.wait_for(wa.recv(), 2))
                    if m["type"] == "comment" and m["nickname"] != "taro":
                        got_bot = True
                except asyncio.TimeoutError:
                    pass
            check("bot自动弹幕", got_bot)
            await c.post("/api/admin/bot", headers=ADMIN, json={"enabled": False})

    failed = [x for x in results if not x[1]]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} 通过 =====")
    return len(failed)


raise SystemExit(asyncio.run(main()))
