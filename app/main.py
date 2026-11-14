"""AI ライブコマース — FastAPI のエントリポイント

起動: uvicorn app.main:app --reload
視聴者ページ: http://127.0.0.1:8000/
管理画面:     http://127.0.0.1:8000/admin
API ドキュメント: http://127.0.0.1:8000/docs
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
from .bot import bot, bot_http
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
    # テーブル構造は Alembic のマイグレーション(migrations/)で管理。初回起動時は見本の商品を入れる
    if settings.auto_migrate:
        await run_migrations()
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Product.id)))).scalar()
        if not count:
            for p in SEED_PRODUCTS:
                db.add(Product(**p))
            await db.commit()
            logger.info("見本の商品を登録しました(初回起動)")
    os.makedirs(settings.audio_dir, exist_ok=True)
    await host.refresh_products()
    logger.info("TTS: %s / LLM: %s", tts.name,
                host.llm.name if host.llm else "なし(ルール応答モード)")
    tasks = host.start() + [bot.start()]   # AI ホストの worker ×2 + 盛り上げ用 BOT
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)  # 終了を待つ(待たないと終了時に警告が出る)
    # 使い回していた HTTP 接続を閉じる(app/http_pool.py)
    await tts.aclose()
    if host.llm:
        await host.llm.aclose()
    await bot_http.aclose()


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
    """他サイトからの WebSocket 乗っ取り(CSWSH)を防ぐ

    ブラウザは WebSocket の接続時にも Cookie を自動で付けるが、WebSocket は同一オリジンポリシー/CORS の対象外。
    そのため悪意のあるサイトからも接続でき、利用者の Cookie を使われてしまう。ブラウザからの接続は Origin を確認する。
    アプリやテスト用スクリプトは通常 Origin を送らない(Bearer トークンで認証する)ので、そのまま通す。
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
    """ライブ配信の WebSocket

    認証:接続時に付ける(ログインしていなくても視聴はできる。コメントは不可)
      ブラウザ → 同一オリジンなので HttpOnly Cookie が自動で付く(ログイン・ログアウト後は画面側が接続し直す)
      アプリ   → 接続時のヘッダ Authorization: Bearer <token>
    クライアント → サーバ: {"type":"comment","text":"..."}
    サーバ → クライアント:
      {"type":"viewers","count":N}                                   視聴者数
      {"type":"comment","nickname","text"}                           視聴者・BOT のコメント
      {"type":"host_reply","id","nickname","text","product_id","emotion","audio_pending"}  AI主播(文字。先に届く)
      {"type":"host_audio","id","audio_url"}                         上の id の発言の音声(合成が終わってから届く)
      {"type":"purchase","nickname","items","stocks"}                購入のお知らせ
      {"type":"products_changed"}                                    商品の変更
      {"type":"error","message"}                                     エラー(この接続にだけ送る)
    """
    if not _origin_allowed(ws):
        await ws.close(code=1008)   # 接続を受け付ける前に閉じる → クライアントには 403 が返る
        return
    raw_token, _ = extract_token(ws.headers, ws.cookies)
    await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                # 形式の壊れたメッセージは捨てるだけで、接続は切らない
                continue
            if not isinstance(data, dict) or data.get("type") != "comment":
                continue
            text = (data.get("text") or "").strip()[:200]
            if not text:
                continue
            # コメントのたびにセッションを確認する:ログアウト・期限切れになったらすぐにコメントできなくなる
            found = await resolve_session(raw_token)
            if not found:
                await ws.send_json({"type": "error", "message": "コメントするにはログインが必要です"})
                continue
            user = found[0]
            if not comment_limiter.hit(f"user:{user.id}"):
                await ws.send_json({"type": "error", "message": "コメントの送信が早すぎます。少し待ってから送信してください"})
                continue
            # 保存 → 全員に配信 → AI ホストに渡す(答えるかどうかは応答の方針で決める)
            async with SessionLocal() as db:
                db.add(Comment(user_id=user.id, nickname=user.username, text=text))
                await db.commit()
            await manager.broadcast({"type": "comment", "nickname": user.username, "text": text})
            await host.on_comment(user.username, text)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(ws)
