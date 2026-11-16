# アプリ本体のイメージ(サーバ用。deploy/compose.yml から使う)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AUDIO_DIR=/data/audio

WORKDIR /app
# 依存を先に入れる(コードだけ変えたときは、このレイヤーが再利用されてビルドが速い)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

# root で動かさない
RUN useradd --create-home appuser && mkdir -p /data/audio && chown appuser /data/audio
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/meta', timeout=4)"
# --proxy-headers:Nginx が付ける X-Forwarded-For / -Proto を信用し、利用者の本当の IP と https を使う
#   (付けないとレート制限が全員「Nginx の IP」になる)。コンテナのポートは 127.0.0.1 にしか公開しないので、
#   ここに届くのはサーバ上の Nginx だけ → forwarded-allow-ips は * でよい
# ワーカーは1つ(WebSocket の接続一覧・AI のキュー・レート制限がメモリ上にあるため)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
