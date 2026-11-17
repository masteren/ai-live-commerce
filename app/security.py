"""セキュリティ関連の共通処理:入力エラーの日本語化・Content-Security-Policy・応答ヘッダ

main.py から install(app) で組み込む。
"""
import base64
import hashlib
import os
import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

# 入力チェック(Pydantic)のエラーは、既定では英語の文と内部の項目名が配列で返る。
# 画面にそのまま出てしまうので、利用者向けの日本語1文にして返す(Web・iOS 共通)
_FIELD_MESSAGES = {
    "username": "ユーザー名は2〜20文字で入力してください(英数字・ひらがな・カタカナ・漢字・_・- が使えます)",
    "password": "パスワードは6〜64文字で入力してください",
    "quantity": "数量は1〜99で指定してください",
}


async def validation_error(request: Request, exc: RequestValidationError):
    fields = [str(e["loc"][-1]) for e in exc.errors() if e.get("loc")]
    message = next((_FIELD_MESSAGES[f] for f in fields if f in _FIELD_MESSAGES), "入力内容を確認してください")
    return JSONResponse(status_code=422, content={"detail": message})


def _content_security_policy(html_file: str) -> str:
    """画面(HTML)に付ける Content-Security-Policy を作る

    スクリプトは自分のサーバのファイル(/static/js/...)だけを実行させ、ページに埋め込まれたスクリプトは実行させない。
    → 万一 XSS でスクリプトが差し込まれても、ブラウザが実行を拒否する(エスケープに加えた二重の守り)。
    例外は importmap(3D アバターのモジュールの読み込み先)。仕様上ページに埋め込むしかないので、
    中身の SHA-256 を許可リストに入れる。中身が変われば起動時に計算し直すので、手で更新する必要はない。
    """
    html = open(os.path.join(STATIC_DIR, html_file), encoding="utf-8").read()
    hashes = [
        "'sha256-" + base64.b64encode(hashlib.sha256(m.encode("utf-8")).digest()).decode() + "'"
        for m in re.findall(r'<script type="importmap">(.*?)</script>', html, re.S)
    ]
    return "; ".join([
        "default-src 'self'",
        "script-src 'self' " + " ".join(hashes) if hashes else "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",          # style 属性は画面のあちこちで使っているので許可(スクリプトほど危険ではない)
        "img-src 'self' data: blob:",                # data: はファビコン、blob: は 3D モデルのテクスチャ
        "media-src 'self' blob:",
        "connect-src 'self' blob:",                  # 'self' には同じホストの WebSocket(ws/wss)も含まれる
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


# 画面の URL → その画面に付ける CSP(起動時に一度だけ作る)
_PAGE_CSP = {}
for _path, _file in (("/", "index.html"), ("/static/index.html", "index.html"),
                     ("/stage", "stage.html"), ("/static/stage.html", "stage.html"),
                     ("/admin", "admin.html"), ("/static/admin.html", "admin.html")):
    _PAGE_CSP[_path] = _content_security_policy(_file)


async def security_headers(request, call_next):
    """全レスポンスに基本的なセキュリティヘッダを付ける。画面(HTML)には CSP も付ける"""
    response = await call_next(request)
    csp = _PAGE_CSP.get(request.url.path)
    if csp:
        response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Frame-Options", "DENY")               # 他サイトの iframe に埋め込ませない(クリックジャッキング対策)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")     # Content-Type を無視した解釈をさせない
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def install(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, validation_error)
    app.middleware("http")(security_headers)
