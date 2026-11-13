"""端到端测试 v4:认证(Cookie/CSRF/Bearer) → 商品 → 购物车 → 模拟支付下单 → 并发超卖
                 → 管理后台 → WebSocket(Origin/限流) → 购买播报 → 机器人

前提:服务端用【空的独立数据库】启动(会注册用户、下单),不要对 live.db 跑。
      服务端需 ADMIN_TOKEN=test-token。
目标地址可用环境变量 E2E_BASE 覆盖(默认 http://127.0.0.1:8000)。
"""
import asyncio
import contextlib
import json
import os

import httpx
import websockets
from websockets.exceptions import InvalidStatus

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000").rstrip("/")
WS = BASE.replace("http", "ws", 1) + "/ws/live"
ADMIN = {"X-Admin-Token": "test-token"}
PW = "pass1234"
results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"{'✅' if cond else '❌'} {name} {detail}")


def now():
    return asyncio.get_event_loop().time()


async def drain(w, seconds=0.6):
    msgs = []
    end = now() + seconds
    while now() < end:
        try:
            msgs.append(json.loads(await asyncio.wait_for(w.recv(), 0.3)))
        except asyncio.TimeoutError:
            pass
    return msgs


async def wait_for(w, pred, seconds):
    """在 seconds 秒内等待满足 pred 的消息;返回该消息或 None"""
    end = now() + seconds
    while now() < end:
        try:
            m = json.loads(await asyncio.wait_for(w.recv(), 2))
        except asyncio.TimeoutError:
            continue
        if pred(m):
            return m
    return None


async def main():
    # web: 模拟浏览器(有 Cookie 罐);c: 不带 Cookie 的普通客户端(未登录 / Bearer 用)
    async with httpx.AsyncClient(base_url=BASE, timeout=15) as web, \
               httpx.AsyncClient(base_url=BASE, timeout=15) as c, \
               contextlib.AsyncExitStack() as stack:

        # ---- 认证(Web:HttpOnly Cookie + CSRF)----
        r = await web.post("/api/auth/register", json={"username": "taro", "password": PW})
        set_cookie = r.headers.get("set-cookie", "").lower()
        check("注册(发行会话Cookie)", r.status_code == 200 and "lc_session" in web.cookies
              and len(r.json().get("csrf_token", "")) >= 16)
        check("Cookie属性 HttpOnly+SameSite=Lax", "httponly" in set_cookie and "samesite=lax" in set_cookie)
        check("响应体不含会话令牌(JS拿不到)", web.cookies.get("lc_session", "") not in r.text)

        r = await c.post("/api/auth/register", json={"username": "taro", "password": "xxxxxx"})
        check("重名注册拒绝(409)", r.status_code == 409)
        r = await c.post("/api/auth/login", json={"username": "taro", "password": "wrongpw"})
        check("错误密码拒绝(401)", r.status_code == 401)

        old_cookie = web.cookies.get("lc_session")
        r = await web.post("/api/auth/login", json={"username": "taro", "password": PW})
        check("登录并发行新会话", r.status_code == 200 and web.cookies.get("lc_session") != old_cookie)
        csrf = r.json()["csrf_token"]
        W = {"X-CSRF-Token": csrf}
        r = await web.get("/api/auth/me")
        check("me接口(Cookie)", r.json().get("username") == "taro" and r.json().get("csrf_token") == csrf)

        # ---- 商品 / 购物车(鉴权 + CSRF)----
        prods = (await c.get("/api/products")).json()
        check("商品列表", len(prods) == 3, f"{len(prods)}件")
        p1 = prods[0]
        r = await c.post("/api/cart", json={"product_id": p1["id"], "quantity": 2})
        check("未登录购物车拒绝(401)", r.status_code == 401)
        r = await web.post("/api/cart", json={"product_id": p1["id"], "quantity": 2})
        check("缺少CSRF令牌拒绝(403)", r.status_code == 403)
        r = await web.post("/api/cart", headers={"X-CSRF-Token": "wrong" * 8},
                           json={"product_id": p1["id"], "quantity": 2})
        check("CSRF令牌错误拒绝(403)", r.status_code == 403)
        await web.post("/api/cart", headers=W, json={"product_id": p1["id"], "quantity": 2})
        r = await web.post("/api/cart", headers=W, json={"product_id": p1["id"], "quantity": 1})
        cart = r.json()
        check("购物车合并数量", len(cart) == 1 and cart[0]["quantity"] == 3)

        # ---- Bearer 令牌(iOS 等原生 App 用)----
        r = await c.post("/api/auth/token", json={"username": "taro", "password": PW})
        tok = r.json().get("access_token", "")
        B = {"Authorization": f"Bearer {tok}"}
        check("Bearer令牌发行(不设Cookie)", r.status_code == 200 and len(tok) >= 32
              and r.json().get("token_type") == "bearer" and "lc_session" not in c.cookies)
        r = await c.get("/api/auth/me", headers=B)
        check("me接口(Bearer)", r.json().get("username") == "taro")

        # ---- 模拟支付下单(用 Bearer:无需 CSRF)----
        r = await c.post("/api/orders/checkout", headers=B, json={"payment_method": "paypay"})
        order = r.json()
        check("下单成功(Bearer·PayPay)", r.status_code == 200 and order["total"] == p1["price"] * 3
              and order["payment_method"] == "paypay" and order["nickname"] == "taro")
        r = await c.get(f"/api/products/{p1['id']}")
        check("库存扣减", r.json()["stock"] == p1["stock"] - 3, f"stock={r.json()['stock']}")
        check("下单后清空购物车(多端共享)", (await web.get("/api/cart")).json() == [])
        r = await web.post("/api/orders/checkout", headers=W, json={"payment_method": "bitcoin"})
        check("非法支付方式拒绝(400)", r.status_code == 400)
        await web.post("/api/cart", headers=W, json={"product_id": p1["id"], "quantity": 99})
        r = await web.post("/api/orders/checkout", headers=W, json={"payment_method": "credit_card"})
        check("超卖保护(409)", r.status_code == 409, r.json().get("detail", ""))
        cid = (await web.get("/api/cart")).json()[0]["id"]
        await web.delete(f"/api/cart/{cid}", headers=W)
        r = await web.get("/api/orders")
        check("我的订单", len(r.json()) == 1)

        # ---- 管理后台 ----
        check("无token拒绝(401)", (await c.get("/api/admin/stats")).status_code == 401)
        r = await c.post("/api/admin/products", headers=ADMIN, json={
            "name": "テスト商品", "price": 1000, "stock": 5, "emoji": "🧸"})
        check("管理端上架商品", r.status_code == 200)
        nid = r.json()["id"]
        check("管理端改库存", (await c.patch(f"/api/admin/products/{nid}", headers=ADMIN,
                                        json={"stock": 8})).json()["stock"] == 8)
        r = await c.patch(f"/api/admin/products/{nid}", headers=ADMIN, json={"emoji": "<img src=x onerror=alert(1)>"})
        check("emoji超长拒绝(422)", r.status_code == 422)
        check("管理端下架(软删除)", (await c.delete(f"/api/admin/products/{nid}", headers=ADMIN)).status_code == 200)
        s = (await c.get("/api/admin/stats", headers=ADMIN)).json()
        check("统计接口(含用户数/bot状态)", s["total_orders"] == 1 and s["total_users"] == 1
              and s["bot_enabled"] is False, f"revenue={s['total_revenue']}")

        # ---- 并发超卖:库存1件,5人同时下单 → 只能成功1单 ----
        rid = (await c.post("/api/admin/products", headers=ADMIN, json={
            "name": "限定1点", "price": 500, "stock": 1})).json()["id"]
        racers = []
        for i in range(5):
            rc = await stack.enter_async_context(httpx.AsyncClient(base_url=BASE, timeout=15))
            rr = await rc.post("/api/auth/register", json={"username": f"racer_{i}", "password": PW})
            hdr = {"X-CSRF-Token": rr.json()["csrf_token"]}
            await rc.post("/api/cart", headers=hdr, json={"product_id": rid, "quantity": 1})
            racers.append((rc, hdr))
        rs = await asyncio.gather(*(rc.post("/api/orders/checkout", headers=h, json={}) for rc, h in racers))
        codes = sorted(x.status_code for x in rs)
        check("并发下单仅1单成功(库存1·5人)", codes == [200, 409, 409, 409, 409], f"status={codes}")
        stock = next(p["stock"] for p in (await c.get("/api/admin/products", headers=ADMIN)).json() if p["id"] == rid)
        check("并发后库存=0(不为负/未丢失扣减)", stock == 0, f"stock={stock}")
        await c.delete(f"/api/admin/products/{rid}", headers=ADMIN)

        # ---- 登录暴力破解防护 ----
        await (await stack.enter_async_context(httpx.AsyncClient(base_url=BASE))).post(
            "/api/auth/register", json={"username": "brute_target", "password": PW})
        fails = [(await c.post("/api/auth/login", json={"username": "brute_target", "password": f"bad{i}xx"})).status_code
                 for i in range(5)]
        r = await c.post("/api/auth/login", json={"username": "brute_target", "password": PW})
        check("连续失败后锁定(429,正确密码也拒绝)", fails == [401] * 5 and r.status_code == 429, f"{fails}→{r.status_code}")

        # ---- 登出:只影响当前设备 ----
        r = await web.post("/api/auth/logout", headers=W)
        check("Web登出", r.status_code == 200 and (await web.get("/api/auth/me")).status_code == 401)
        check("登出不影响iOS会话(多端独立)", (await c.get("/api/auth/me", headers=B)).status_code == 200)
        r = await web.post("/api/auth/login", json={"username": "taro", "password": PW})
        W = {"X-CSRF-Token": r.json()["csrf_token"]}
        cookie_hdr = {"Cookie": f"lc_session={web.cookies.get('lc_session')}"}

        # ---- WebSocket ----
        try:
            async with websockets.connect(WS, origin="http://evil.example.com"):
                evil_ok = True
        except InvalidStatus:
            evil_ok = False
        check("跨站WebSocket拒绝(Origin检查)", evil_ok is False)

        async with websockets.connect(WS, additional_headers=cookie_hdr) as wa, \
                   websockets.connect(WS) as wb, \
                   websockets.connect(WS, additional_headers=B) as wc:
            await drain(wa); await drain(wb); await drain(wc)
            await wb.send(json.dumps({"type": "comment", "text": "hello"}))
            msgs = await drain(wb, 1.0)
            check("未登录发言被拒", any(m["type"] == "error" for m in msgs))

            await wa.send(json.dumps({"type": "comment", "text": "イヤホンの音質どう?"}))
            got_comment_b = got_host = False
            host_msg = {}
            deadline = now() + 12
            while now() < deadline and not (got_comment_b and got_host):
                try:
                    m = json.loads(await asyncio.wait_for(wb.recv(), 2))
                except asyncio.TimeoutError:
                    continue
                if m["type"] == "comment" and m["nickname"] == "taro":
                    got_comment_b = True
                # 队列里可能还有前面购买产生的道谢,只认对「イヤホン」提问的回答
                if m["type"] == "host_reply" and "イヤホン" in m["text"]:
                    got_host = True
                    host_msg = m
            check("弹幕跨客户端广播(Cookie认证)", got_comment_b)
            check("AI主播回答(问题必答·按商品数据回答·切换商品卡)", got_host and host_msg.get("emotion") in
                  {"normal", "happy", "excited", "troubled", "shy"} and host_msg.get("product_id") == 1,
                  f"emotion={host_msg.get('emotion')} | {host_msg.get('text', '')[:30]}")

            await wc.send(json.dumps({"type": "comment", "text": "iOSから参加しています"}))
            m = await wait_for(wb, lambda m: m["type"] == "comment" and m["text"] == "iOSから参加しています", 5)
            check("Bearer认证的WebSocket发言", m is not None and m["nickname"] == "taro")

            # ---- 购买 → 广播 + 主播道谢 ----
            await web.post("/api/cart", headers=W, json={"product_id": 2, "quantity": 1})
            await web.post("/api/orders/checkout", headers=W, json={"payment_method": "convenience_store"})
            got_purchase = got_thanks = False
            deadline = now() + 15
            while now() < deadline and not (got_purchase and got_thanks):
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
            m = await wait_for(wa, lambda m: m["type"] == "comment" and m["nickname"] != "taro", 15)
            check("bot自动弹幕", m is not None)
            await c.post("/api/admin/bot", headers=ADMIN, json={"enabled": False})

            # ---- 弹幕限流(放最后,避免影响上面的主播回答)----
            for i in range(8):
                await wa.send(json.dumps({"type": "comment", "text": f"連投{i}"}))
            msgs = await drain(wa, 1.5)
            check("弹幕限流(连发被拒)", any(m["type"] == "error" and "早すぎ" in m.get("message", "") for m in msgs))

    failed = [x for x in results if not x[1]]
    print(f"\n===== {len(results) - len(failed)}/{len(results)} 通过 =====")
    return len(failed)


raise SystemExit(asyncio.run(main()))
