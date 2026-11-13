"""集中配置:全部通过环境变量 / .env 覆盖"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ---- 数据库 ----
    # 本地开发默认 SQLite;生产环境改为:
    #   postgresql+asyncpg://liveuser:password@localhost:5432/livecommerce
    database_url: str = "sqlite+aiosqlite:///./live.db"

    # ---- AI 主播 (Anthropic API) ----
    anthropic_api_key: str = ""          # 留空则使用内置降级回复(无 LLM)
    anthropic_model: str = "claude-sonnet-5"   # 更高档可换 claude-opus-4-8
    host_name: str = "ハナ"
    reply_max_chars: int = 90            # 主播单条回答长度上限(提示词约束)
    reply_min_interval: float = 4.0      # 两次 AI 回答之间的最小间隔(秒),防刷屏/控成本
    idle_interval: float = 45.0          # 无人互动时,主播自动播报的间隔(秒)
    chat_reply_prob: float = 0.3         # 闲聊弹幕的随机接话概率(问题类弹幕必答)

    # ---- 弹幕池 (OpenAI 兼容 API) ----
    # 留空则机器人只用内置固定弹幕;DeepSeek/通义等兼容接口改 base_url 即可
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    bot_pool_batch: int = 60             # 每次批量生成的弹幕条数(闲聊+提问合计)

    # ---- VOICEVOX TTS ----
    voicevox_url: str = "http://127.0.0.1:50021"
    voicevox_speaker: int = 3            # 3=ずんだもん(ノーマル) 8=春日部つむぎ 2=四国めたん;GET {voicevox_url}/speakers 可查全部
    voicevox_enabled: bool = True        # False 则纯文字模式
    voicevox_speed: float = 1.05

    # ---- 管理后台 ----
    admin_token: str = "change-me-admin-token"   # 生产环境务必修改!

    # ---- 其他 ----
    audio_dir: str = "app/static/audio"  # TTS 音频缓存目录
    cors_origins: str = "*"


settings = Settings()
