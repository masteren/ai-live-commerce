"""AI ライブコマース — FastAPI 主入口

启动: uvicorn app.main:app --reload
观众端:  http://127.0.0.1:8000/
管理后台: http://127.0.0.1:8000/admin
API 文档: http://127.0.0.1:8000/docs
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func

from .ai_host import host
from .bot import bot
from .config import settings
from .database import Base, SessionLocal, engine
from .models import Comment, Product
from .routers import admin, auth, cart, orders, products
from .routers.auth import resolve_token
from .ws import manager

SEED_PRODUCTS = [
    dict(name="ワイヤレスイヤホン Pro", emoji="🎧", price=4980, original_price=7980, stock=30,
         tag="タイムセール", description="ノイズキャンセリング搭載、最大32時間再生、IPX5防水"),
    dict(name="ステンレス保温マグ 480ml", emoji="🥤", price=1880, original_price=2480, stock=60,
         tag="人気No.1", description="12時間保温・保冷、ワンタッチ開閉、食洗機OK"),
    dict(name="ヒアルロン酸美容液 30ml", emoji="✨", price=2680, original_price=3980, stock=15,
         tag="残りわずか", description="高濃度ヒアルロン酸配合、無香料・無着色、敏感肌OK"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 建表 + 首次启动播种示例商品
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Product.id)))).scalar()
        if not count:
            for p in SEED_PRODUCTS:
                db.add(Product(**p))
            await db.commit()
            print("[seed] 示例商品已写入")
    os.makedirs(settings.audio_dir, exist_ok=True)
    await host.refresh_product_names()
    tasks = host.start() + [bot.start()]   # AI主播 worker ×2 + 气氛组机器人
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)  # 等待退出,避免关停告警


app = FastAPI(title="AI Live Commerce", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(admin.router)

BASE_DIR = os.path.dirname(__file__)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "admin.html"))


@app.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    """直播间 WebSocket

    客户端 → 服务端: {"type":"comment","token":"...","text":"..."}(发言需登录令牌)
    服务端 → 客户端:
      {"type":"viewers","count":N}                                   在线人数
      {"type":"comment","nickname","text"}                           观众/机器人弹幕
      {"type":"host_reply","nickname","text","product_id","emotion","audio_url"}  AI主播
      {"type":"purchase","nickname","items","stocks"}                购买播报
      {"type":"products_changed"}                                    商品变更
      {"type":"error","message"}                                     仅发给当前连接
    """
    await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # 畸形帧只丢弃,不断开连接
                continue
            if not isinstance(data, dict) or data.get("type") != "comment":
                continue
            text = (data.get("text") or "").strip()[:200]
            if not text:
                continue
            user = await resolve_token(data.get("token") or "")
            if not user:
                await ws.send_json({"type": "error", "message": "コメントするにはログインが必要です"})
                continue
            # 落库 → 广播 → 交给 AI 主播(混合应答策略判断是否回答)
            async with SessionLocal() as db:
                db.add(Comment(user_id=user.id, nickname=user.username, text=text))
                await db.commit()
            await manager.broadcast({"type": "comment", "nickname": user.username, "text": text})
            await host.on_comment(user.username, text)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)
