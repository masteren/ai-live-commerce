"""AI ライブコマース — FastAPI 主入口

启动: uvicorn app.main:app --reload
观众端:  http://127.0.0.1:8000/
管理后台: http://127.0.0.1:8000/admin
API 文档: http://127.0.0.1:8000/docs
"""
import asyncio
import json
import logging
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
from .database import SessionLocal
from .migrate import run_migrations
from .models import Comment, Product
from .ratelimit import SlidingWindowLimiter
from .routers import admin, auth, cart, orders, products
from .routers.auth import extract_token, resolve_session
from .tts import tts
from .ws import manager

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

comment_limiter = SlidingWindowLimiter(settings.comment_rate_limit, settings.comment_rate_window)

SEED_PRODUCTS = [
    dict(name="ワイヤレスイヤホン Pro", emoji="🎧", image_url="/static/products/earphones.png", price=4980, original_price=7980, stock=30,
         tag="タイムセール", description="ノイズキャンセリング搭載、最大32時間再生、IPX5防水"),
    dict(name="ステンレス保温マグ 480ml", emoji="🥤", image_url="/static/products/mug.png", price=1880, original_price=2480, stock=60,
         tag="人気No.1", description="12時間保温・保冷、ワンタッチ開閉、食洗機OK"),
    dict(name="ヒアルロン酸美容液 30ml", emoji="✨", image_url="/static/products/serum.png", price=2680, original_price=3980, stock=15,
         tag="残りわずか", description="高濃度ヒアルロン酸配合、無香料・無着色、敏感肌OK"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 表结构由 Alembic 迁移管理(migrations/);首次启动播种示例商品
    if settings.auto_migrate:
        await run_migrations()
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Product.id)))).scalar()
        if not count:
            for p in SEED_PRODUCTS:
                db.add(Product(**p))
            await db.commit()
            logger.info("示例商品已写入(首次启动)")
    os.makedirs(settings.audio_dir, exist_ok=True)
    await host.refresh_product_names()
    logger.info("TTS: %s / LLM: %s", tts.name,
                settings.anthropic_model if settings.anthropic_api_key else "なし(定型文モード)")
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


@app.get("/api/meta", tags=["meta"])
async def meta():
    """画面表示用の公開情報(音声クレジット・回答生成エンジン)"""
    return {
        "host_name": settings.host_name,
        "tts_credit": tts.credit,
        "llm": "Anthropic Claude API" if settings.anthropic_api_key else "定型文(デモモード)",
    }


@app.get("/admin", include_in_schema=False)
async def admin_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "admin.html"))


def _origin_allowed(ws: WebSocket) -> bool:
    """防止跨站 WebSocket 劫持(CSWSH)

    浏览器连接 WebSocket 时会自动附带 Cookie,但 WebSocket 不受同源策略/CORS 保护,
    恶意网站也能连到这里并借用用户的 Cookie。所以浏览器发来的握手必须检查 Origin。
    原生 App / 测试脚本通常不发送 Origin(它们用 Bearer 令牌认证),直接放行。
    """
    origin = ws.headers.get("origin")
    if not origin:
        return True
    allowed = [o.strip() for o in settings.cors_origins.split(",") if o.strip() and o.strip() != "*"]
    if origin in allowed:
        return True
    host = ws.headers.get("host", "")
    return origin in (f"http://{host}", f"https://{host}")


@app.websocket("/ws/live")
async def live_ws(ws: WebSocket):
    """直播间 WebSocket

    认证:握手时携带(未登录也可以观看,只是不能发言)
      浏览器 → 同源连接时自动附带 HttpOnly Cookie(登录/登出后前端会重新连接)
      原生 App → 握手请求头 Authorization: Bearer <token>
    客户端 → 服务端: {"type":"comment","text":"..."}
    服务端 → 客户端:
      {"type":"viewers","count":N}                                   在线人数
      {"type":"comment","nickname","text"}                           观众/机器人弹幕
      {"type":"host_reply","nickname","text","product_id","emotion","audio_url"}  AI主播
      {"type":"purchase","nickname","items","stocks"}                购买播报
      {"type":"products_changed"}                                    商品变更
      {"type":"error","message"}                                     仅发给当前连接
    """
    if not _origin_allowed(ws):
        await ws.close(code=1008)   # 握手前关闭 → 客户端收到 403
        return
    raw_token, _ = extract_token(ws.headers, ws.cookies)
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
            # 每条弹幕都重新校验会话:登出或过期后立即失去发言权限
            found = await resolve_session(raw_token)
            if not found:
                await ws.send_json({"type": "error", "message": "コメントするにはログインが必要です"})
                continue
            user = found[0]
            if not comment_limiter.hit(f"user:{user.id}"):
                await ws.send_json({"type": "error", "message": "コメントの送信が早すぎます。少し待ってから送信してください"})
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
