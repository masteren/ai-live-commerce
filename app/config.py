"""設定を1か所にまとめる:すべて環境変数 / .env で上書きできる"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- データベース ----
    # 開発は MySQL(docker-compose.yml の db サービス。.env.example に設定例):
    #   mysql+aiomysql://liveuser:livepass@127.0.0.1:3306/livecommerce?charset=utf8mb4
    # .env がなくても起動できるよう、既定値は SQLite(単体テストも SQLite の一時ファイルを使う)
    database_url: str = "sqlite+aiosqlite:///./live.db"
    auto_migrate: bool = True            # 起動時に alembic upgrade head を自動実行

    # ---- AI 主播の LLM ----
    # auto: キーが設定されている方を使う(両方あれば Anthropic)/ anthropic / openai / none(ルール応答のみ)
    llm_provider: str = "auto"
    anthropic_api_key: str = ""          # 空なら LLM を使わず、ルール応答で動く
    anthropic_model: str = "claude-sonnet-5"   # 使うモデルは .env の ANTHROPIC_MODEL で変更できる
    host_name: str = "ハナ"
    reply_max_chars: int = 90            # 1回の返答の文字数の上限(プロンプトで指示し、コードでも切り詰める)
    reply_min_interval: float = 4.0      # AI の返答と返答の最小間隔(秒)。連続で話しすぎない・費用を抑える
    idle_interval: float = 45.0          # コメントがないとき、ホストが自動で話す間隔(秒)
    chat_reply_prob: float = 0.3         # 雑談コメントに答える確率(質問には必ず答える)
    always_reply_max_viewers: int = 3    # 視聴者がこの人数以下なら、雑談にも必ず答える(人が少ないのに返事がないと寂しいため)

    # ---- OpenAI 互換 API(AI ホストと、BOT のコメント生成に使う) ----
    # キーが空なら BOT は組み込みの定型コメントだけを使う。互換 API を使う場合は base_url を変える
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    bot_pool_batch: int = 60             # BOT 用のコメントを1回にまとめて作る件数(雑談+質問の合計)

    # ---- TTS ----
    tts_provider: str = "voicevox"       # voicevox / none(文字のみ。音声を止めるスイッチはこれ1つ)。新しいエンジンは tts.build_tts() に登録
    tts_credit: str = "VOICEVOX:ずんだもん"   # 画面に出す音声のクレジット表記(声を変えたら一緒に変える)

    # ---- VOICEVOX TTS ----
    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 3            # 3=ずんだもん(ノーマル) 8=春日部つむぎ 2=四国めたん。一覧は GET {voicevox_url}/speakers
    voicevox_speed: float = 1.05

    # ---- 認証・セッション ----
    session_ttl_hours: int = 24 * 7      # セッションの有効期限(使うたびに延長。Web の Cookie と iOS の Bearer で共通)
    cookie_secure: bool = False          # ローカルの http 開発では False。HTTPS で公開するときは必ず True
    # PBKDF2 の反復回数。OWASP の推奨(PBKDF2-HMAC-SHA256 は 60 万回)に合わせる。テストでは小さくして高速化
    password_iterations: int = 600_000
    register_limit: int = 20             # 同一 IP からの新規登録の上限(ウィンドウ内)。大量アカウント作成の防止
    register_window: float = 600.0
    admin_fail_limit: int = 10           # 管理トークンの誤りを同一 IP から許す回数(総当たり対策)
    admin_fail_window: float = 300.0
    login_fail_limit: int = 5            # 同じ IP+ユーザー名 で許すログイン失敗の回数(総当たり対策)
    login_fail_window: float = 300.0     # 秒
    comment_rate_limit: int = 5          # 1人がウィンドウ内に送れるコメントの数(連投の防止)
    comment_rate_window: float = 10.0    # 秒

    # ---- 管理画面 ----
    admin_token: str = "change-me-admin-token"   # 公開するときは必ず変更する

    # ---- その他 ----
    log_level: str = "INFO"              # DEBUG / INFO / WARNING / ERROR
    audio_cache_max_files: int = 500     # 音声キャッシュ(wav)の上限。超えたら最後に使われたのが古い順に消す
    # TTS 音声のキャッシュ置き場(/static/audio/ で配信)。既定はプロジェクト内(起動場所に左右されない絶対パス)
    audio_dir: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "audio")
    # 別オリジンのフロントエンドから API を呼ぶ場合だけ、許可するオリジンをカンマ区切りで指定する。
    # 既定は空 = 同一オリジンのみ(このアプリはフロントも同じサーバから配信しているため CORS は不要)
    cors_origins: str = ""


settings = Settings()
