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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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
    # ↓ あとから追加した3点。運用中の DB にはマイグレーション d4e8a2b91c07 が同じ内容を追加する
    dict(name="モバイルバッテリー 10000mAh", emoji="🔋", image_url="/static/products/battery.png", price=2980, original_price=3980, stock=40,
         tag="新着", description="薄型・軽量180g、2台同時充電、残量ランプつき"),
    dict(name="アロマディフューザー", emoji="💧", image_url="/static/products/diffuser.png", price=3480, original_price=4980, stock=25,
         tag="ギフトに人気", description="超音波式で静か、やさしい灯りつき、最大8時間の連続運転"),
    dict(name="コットンフェイスタオル 2枚組", emoji="🧺", image_url="/static/products/towels.png", price=1480, original_price=1980, stock=80,
         tag="まとめ買い", description="オーガニックコットン100%、ふんわり厚手、生成りとセージの2色"),
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
    await host.refresh_products()
    logger.info("TTS: %s / LLM: %s", tts.name,
                host.llm.name if host.llm else "なし(ルール応答モード)")
    tasks = host.start() + [bot.start()]   # AI主播 worker ×2 + 气氛组机器人
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)  # 等待退出,避免关停告警


app = FastAPI(title="AI Live Commerce", version="1.0.0", lifespan=lifespan)
_cors = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _cors:   # 既定は空 = CORS ヘッダを出さない(同一オリジンのみ)
    app.add_middleware(CORSMiddleware, allow_origins=_cors, allow_methods=["*"], allow_headers=["*"])


# 入力チェック(Pydantic)のエラーは、既定では英語の文と内部の項目名が配列で返る。
# 画面にそのまま出てしまうので、利用者向けの日本語1文にして返す(Web・iOS 共通)
_FIELD_MESSAGES = {
    "username": "ユーザー名は2〜20文字で入力してください(英数字・ひらがな・カタカナ・漢字・_・- が使えます)",
    "password": "パスワードは6〜64文字で入力してください",
    "quantity": "数量は1〜99で指定してください",
}


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    fields = [str(e["loc"][-1]) for e in exc.errors() if e.get("loc")]
    message = next((_FIELD_MESSAGES[f] for f in fields if f in _FIELD_MESSAGES), "入力内容を確認してください")
    return JSONResponse(status_code=422, content={"detail": message})


@app.middleware("http")
async def security_headers(request, call_next):
    """全レスポンスに基本的なセキュリティヘッダを付ける"""
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")               # 他サイトの iframe に埋め込ませない(クリックジャッキング対策)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")     # Content-Type を無視した解釈をさせない
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

app.include_router(auth.router)
app.include_router(products.router)
app.include_router(cart.router)
app.include_router(orders.router)
app.include_router(admin.router)

BASE_DIR = os.path.dirname(__file__)
# 音声キャッシュは設定の AUDIO_DIR から配信する(/static より先に登録 → こちらが優先される)。
# 以前は /static(app/static)にあるものとして URL を作っていたため、AUDIO_DIR を変えたり
# 別のディレクトリから起動したりすると、音声は合成されるのに 404 になっていた
AUDIO_DIR = os.path.abspath(settings.audio_dir)
os.makedirs(AUDIO_DIR, exist_ok=True)
app.mount("/static/audio", StaticFiles(directory=AUDIO_DIR), name="audio")
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
        "llm": host.llm.name if host.llm else "ルール応答(デモモード)",
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
