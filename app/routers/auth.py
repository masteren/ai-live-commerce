"""ユーザー認証:新規登録 / ログイン / ログアウト / 現在のユーザー

■ 2種類のクライアントと、それぞれのトークンの持ち方(セッション表 auth_sessions は共通)
  - Web ブラウザ: HttpOnly Cookie(lc_session)
      JS から Cookie を読めない → XSS が起きてもトークンは盗まれない(localStorage に置くと XSS で読まれる)
      SameSite=Lax + CSRF トークンの二重送信 → 他のサイトが Cookie を使ってリクエストを送るのを防ぐ(CSRF)
  - iOS などのアプリ: Authorization: Bearer <token>(POST /api/auth/token で取得)
      ブラウザはこのヘッダを自動で付けないので、CSRF は起きない。CSRF トークンは不要

■ 安全面の要点
  - パスワード:標準ライブラリの pbkdf2_hmac(sha256、60万回。回数もハッシュと一緒に保存)+ ランダムなソルト。
    ログイン成功時に古い形式のハッシュを自動で更新する
  - トークン:secrets で作るランダムな値。DB には SHA-256 のハッシュだけを保存。有効期限あり。ログアウトでセッションを削除
  - ログイン失敗の回数制限:同じ IP+ユーザー名 で短時間に失敗が多すぎる → 429(総当たり対策)

■ ゲスト(登録せずに参加)
  「ゲスト1234」という名前のユーザーを作り、そのままログインした状態にする。コメントも購入もできる。
  パスワードは持たない(password_hash に検証できない値を入れる)ので、ログアウトするとその名前には戻れない。
  「ゲスト」で始まる名前は新規登録では使えない(ゲストのふりをさせない)。作成は新規登録と同じ回数制限を受ける
"""
import asyncio
import hashlib
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import AuthSession, User, now_utc
from ..ratelimit import SlidingWindowLimiter, client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "lc_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

GUEST_PREFIX = "ゲスト"
NO_PASSWORD = "!"         # ゲストの password_hash。_parse_hash で形式エラーになるので、どんなパスワードとも一致しない

login_limiter = SlidingWindowLimiter(settings.login_fail_limit, settings.login_fail_window)
register_limiter = SlidingWindowLimiter(settings.register_limit, settings.register_window)


# ---------- パスワード ----------
def hash_password(password: str, iterations: Optional[int] = None) -> str:
    """形式: pbkdf2_sha256$反復回数$ソルト(hex)$ハッシュ(hex)
    反復回数をハッシュと一緒に保存する → 将来回数を増やしても、古いハッシュを検証できる"""
    n = iterations or settings.password_iterations
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
    return f"pbkdf2_sha256${n}${salt.hex()}${dk.hex()}"


def _parse_hash(stored: str) -> Tuple[int, bytes, str]:
    algo, n, salt, dk_hex = stored.split("$")          # 形が違えば ValueError
    if algo != "pbkdf2_sha256":
        raise ValueError("unknown password hash format")
    return int(n), bytes.fromhex(salt), dk_hex


def verify_password(password: str, stored: str) -> bool:
    try:
        n, salt, dk_hex = _parse_hash(stored)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except ValueError:
        return False


def needs_rehash(stored: str) -> bool:
    """保存されているハッシュの反復回数が、現在の設定より少ないか(回数を上げたあと、ログイン時に作り直す)"""
    try:
        return _parse_hash(stored)[0] < settings.password_iterations
    except ValueError:
        return True


# ---------- セッション ----------
def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _create_session(db: AsyncSession, user: User) -> Tuple[str, AuthSession]:
    """セッションを作り、(元のトークン, セッションの行) を返す。元のトークンが出てくるのはここだけで、DB にはハッシュだけを置く"""
    now = now_utc()
    # ついでに期限切れのセッションを消す(表が増え続けないように)
    await db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    raw = secrets.token_urlsafe(32)
    sess = AuthSession(
        user_id=user.id,
        token_hash=_hash_token(raw),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(sess)
    await db.commit()
    return raw, sess


async def resolve_session(raw: str, db: Optional[AsyncSession] = None) -> Optional[Tuple[User, AuthSession]]:
    """元のトークン → (User, AuthSession)。無効・期限切れなら None。WebSocket など依存性注入が使えない場所からも呼べる"""
    if not raw or len(raw) < 32:
        return None
    stmt = (
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        # 期限切れの判定は SQL の中で行う(SQLite から取り出した時刻にはタイムゾーンがなく、Python で比べると間違えやすい)
        .where(AuthSession.token_hash == _hash_token(raw), AuthSession.expires_at > now_utc())
    )
    if db is not None:
        row = (await db.execute(stmt)).first()
    else:
        async with SessionLocal() as session:
            row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


def extract_token(headers, cookies) -> Tuple[str, str]:
    """リクエストからトークンを取り出す:Authorization: Bearer(アプリ)を優先し、なければ Cookie(ブラウザ)。(トークン, 方式) を返す"""
    authz = headers.get("authorization", "")
    if authz[:7].lower() == "bearer ":
        return authz[7:].strip(), "bearer"
    return cookies.get(COOKIE_NAME, ""), "cookie"


def _set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        COOKIE_NAME, raw,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,                  # JS から読めない
        samesite="lax",                 # 他サイトからの POST などには Cookie を付けない
        secure=settings.cookie_secure,  # HTTPS で運用するときは、暗号化された通信でだけ送る
        path="/",
    )


# ---------- 依存性注入(各エンドポイントで使うログイン確認) ----------
async def get_current_session(request: Request, db: AsyncSession = Depends(get_db)) -> Tuple[User, AuthSession]:
    raw, via = extract_token(request.headers, request.cookies)
    found = await resolve_session(raw, db)
    if not found:
        raise HTTPException(401, "ログインが必要です")
    user, sess = found
    # Cookie で認証していて、データを変更するリクエスト → 正しい CSRF トークンが必要(二重送信方式)
    if via == "cookie" and request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, sess.csrf_token):
            raise HTTPException(403, "CSRFトークンが正しくありません")
    return user, sess


async def get_current_user(ctx: Tuple[User, AuthSession] = Depends(get_current_session)) -> User:
    return ctx[0]


# ---------- リクエスト・レスポンスの型 ----------
class AuthIn(BaseModel):
    username: str = Field(min_length=2, max_length=20, pattern=r"^[\w\-ぁ-んァ-ヶ一-龠ー]+$")
    password: str = Field(min_length=6, max_length=64)


class WebAuthOut(BaseModel):
    username: str
    csrf_token: str      # 画面側はメモリに持ち、変更系のリクエストで X-CSRF-Token ヘッダに入れる


class TokenOut(BaseModel):
    username: str
    access_token: str
    token_type: str = "bearer"
    expires_at: str      # ISO8601(UTC)


async def _authenticate(body: AuthIn, request: Request, db: AsyncSession) -> User:
    key = f"{client_ip(request)}:{body.username}"
    if login_limiter.is_limited(key):
        raise HTTPException(429, "ログイン試行回数が多すぎます。しばらくしてから再度お試しください")
    row = await db.execute(select(User).where(User.username == body.username))
    user = row.scalar_one_or_none()
    # ハッシュ計算は CPU を数十ms 使う。イベントループ上で直接実行すると、その間ほかの全リクエストと
    # WebSocket の配信が止まるので、別スレッドで実行する
    ok = bool(user) and await asyncio.to_thread(verify_password, body.password, user.password_hash)
    if not ok:
        login_limiter.hit(key)
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    login_limiter.reset(key)
    if needs_rehash(user.password_hash):
        # ログイン成功時(=平文パスワードが手元にある唯一のタイミング)に、現在の強度でハッシュし直す
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        await db.commit()
    return user


async def _create_guest(request: Request, db: AsyncSession) -> User:
    if not register_limiter.hit(client_ip(request)):
        raise HTTPException(429, "参加の回数が多すぎます。しばらくしてから再度お試しください")
    # 番号はランダム。すでに使われていたら(一意制約に当たったら)別の番号で作り直す。
    # 先に「使われているか」を調べる方法だと、同時に同じ番号を選んだときに重なるので、DB の一意制約で判定する
    for digits in (4, 4, 4, 6, 6, 6):
        name = GUEST_PREFIX + "".join(secrets.choice("0123456789") for _ in range(digits))
        user = User(username=name, password_hash=NO_PASSWORD, is_guest=True)
        db.add(user)
        try:
            await db.commit()
            return user
        except IntegrityError:
            await db.rollback()
    raise HTTPException(503, "ただいま混み合っています。もう一度お試しください")


# ---------- エンドポイント ----------
@router.post("/guest", response_model=WebAuthOut)
async def guest(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Web 用:登録せずにゲストとして参加する(Cookie を設定)"""
    user = await _create_guest(request, db)
    raw, sess = await _create_session(db, user)
    _set_session_cookie(response, raw)
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)


@router.post("/guest/token", response_model=TokenOut)
async def guest_token(request: Request, db: AsyncSession = Depends(get_db)):
    """アプリ用:ゲストとして参加し、Bearer トークンを返す"""
    user = await _create_guest(request, db)
    raw, sess = await _create_session(db, user)
    return TokenOut(username=user.username, access_token=raw, expires_at=sess.expires_at.isoformat())


@router.post("/register", response_model=WebAuthOut)
async def register(body: AuthIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Web 用:新規登録して、そのままログインする(Cookie を設定)"""
    if body.username.startswith(GUEST_PREFIX):
        raise HTTPException(400, f"「{GUEST_PREFIX}」で始まるユーザー名は使えません")
    if not register_limiter.hit(client_ip(request)):
        raise HTTPException(429, "登録の回数が多すぎます。しばらくしてから再度お試しください")
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none():
        raise HTTPException(409, "このユーザー名は既に使われています")
    user = User(username=body.username, password_hash=await asyncio.to_thread(hash_password, body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # 同じ名前の登録が同時に来た場合:重複チェックは通っても、commit で一意制約に当たる → 409 にそろえる
        await db.rollback()
        raise HTTPException(409, "このユーザー名は既に使われています")
    raw, sess = await _create_session(db, user)
    _set_session_cookie(response, raw)
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)


@router.post("/login", response_model=WebAuthOut)
async def login(body: AuthIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Web 用のログイン(HttpOnly Cookie を設定する。トークン自体はレスポンスの本文に出さない)"""
    user = await _authenticate(body, request, db)
    raw, sess = await _create_session(db, user)
    _set_session_cookie(response, raw)
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)


@router.post("/token", response_model=TokenOut)
async def issue_token(body: AuthIn, request: Request, db: AsyncSession = Depends(get_db)):
    """アプリ用:Bearer トークンを返す(Cookie は設定しない)"""
    user = await _authenticate(body, request, db)
    raw, sess = await _create_session(db, user)
    return TokenOut(username=user.username, access_token=raw, expires_at=sess.expires_at.isoformat())


@router.post("/logout")
async def logout(response: Response,
                 ctx: Tuple[User, AuthSession] = Depends(get_current_session),
                 db: AsyncSession = Depends(get_db)):
    """ログアウト:このセッションだけを削除する(他の端末のログインはそのまま)"""
    await db.execute(delete(AuthSession).where(AuthSession.id == ctx[1].id))
    await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=WebAuthOut)
async def me(request: Request, response: Response,
             ctx: Tuple[User, AuthSession] = Depends(get_current_session),
             db: AsyncSession = Depends(get_db)):
    """現在のユーザー。画面を再読み込みしたとき、ログイン状態と CSRF トークンをここから復元する

    ログイン状態の維持:Web もアプリも起動(再読み込み)のたびにここを呼ぶので、このとき有効期限を延長する。
    使い続けている限りログインしたままになり、SESSION_TTL_HOURS の間まったく使わなければ切れる(スライディング方式)。
    毎回書き込むと無駄なので、残りが半分を切ったときだけ延長する。"""
    user, sess = ctx
    username, csrf, sess_id = user.username, sess.csrf_token, sess.id
    now, ttl = now_utc(), timedelta(hours=settings.session_ttl_hours)
    extended = await db.execute(
        update(AuthSession)
        .where(AuthSession.id == sess_id, AuthSession.expires_at < now + ttl / 2)   # 判定は SQL 側で(SQLite の時刻は時差情報を持たない)
        .values(expires_at=now + ttl)
        .execution_options(synchronize_session=False)
    )
    if extended.rowcount:
        await db.commit()
        raw, via = extract_token(request.headers, request.cookies)
        if via == "cookie":
            _set_session_cookie(response, raw)       # ブラウザ側の Cookie の期限も延ばす
    return WebAuthOut(username=username, csrf_token=csrf)
