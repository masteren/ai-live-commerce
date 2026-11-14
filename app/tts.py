"""语音合成(TTS):可替换的 Provider 结构

调用方(ai_host)只依赖 TTSProvider 接口:
    audio_url = await tts.synthesize(text, emotion)   # 失败或未启用时返回 None(纯文字模式)

现有实现:
  - VoicevoxTTS : VOICEVOX ENGINE(日语专用,情绪 → 语速/音高微调)
  - NullTTS     : 不发声(纯文字模式;测试、无引擎环境用)
新增引擎(例如 OmniVoice)时只需再写一个类并在 build_tts() 注册,其他代码不用改。
用 .env 的 TTS_PROVIDER 切换。

VOICEVOX 两段式 API:
  1. POST /audio_query?text=...&speaker=N  → 合成参数 JSON
  2. POST /synthesis?speaker=N (body=上一步JSON) → wav
结果按「文本+声线参数」的 md5 缓存到 app/static/audio/,同一句话不重复合成。
"""
import hashlib
import logging
import os
import re
from typing import Optional, Protocol

import httpx

from .config import settings

logger = logging.getLogger(__name__)

EMOTIONS = ("normal", "happy", "excited", "troubled", "shy")

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F000-\U0001F02F"
    "☀-⛿⬀-⯿"
    "]+",
    flags=re.UNICODE,
)


def clean_text(text: str) -> str:
    """去掉表情符号等 TTS 读不了的字符"""
    return _EMOJI_RE.sub("", text).strip()


class TTSProvider(Protocol):
    name: str
    credit: str      # 页面上显示的署名(例:VOICEVOX:ずんだもん);无需署名则为空

    async def synthesize(self, text: str, emotion: str = "normal") -> Optional[str]:
        """合成语音,返回前端可访问的 URL;失败返回 None"""
        ...


class NullTTS:
    name = "none"
    credit = ""

    async def synthesize(self, text: str, emotion: str = "normal") -> Optional[str]:
        return None


class VoicevoxTTS:
    name = "voicevox"

    # 情绪 → 语速/音高的微调(声线统一以保持专业感;speaker=None 表示沿用 .env 设置)
    EMOTION_VOICE = {
        "normal":   {"speaker": None, "speed": 1.0,  "pitch": 0.0},
        "happy":    {"speaker": None, "speed": 1.04, "pitch": 0.01},
        "excited":  {"speaker": None, "speed": 1.08, "pitch": 0.02},
        "troubled": {"speaker": None, "speed": 0.95, "pitch": -0.01},
        "shy":      {"speaker": None, "speed": 0.97, "pitch": 0.0},
    }

    def __init__(self, base_url: str, speaker: int, speed: float, audio_dir: str, credit: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.base_url = base_url
        self.speaker = speaker
        self.speed = speed
        self.audio_dir = audio_dir
        self.credit = credit
        self.transport = transport   # 测试时注入 httpx.MockTransport,不连真实引擎

    async def synthesize(self, text: str, emotion: str = "normal") -> Optional[str]:
        speech_text = clean_text(text)
        if not speech_text:
            return None
        voice = self.EMOTION_VOICE.get(emotion, self.EMOTION_VOICE["normal"])
        spk = voice["speaker"] if voice["speaker"] is not None else self.speaker
        spd = self.speed * voice["speed"]
        pitch = voice["pitch"]

        key = hashlib.md5(f"{speech_text}|{spk}|{spd}|{pitch}".encode()).hexdigest()
        filename = f"{key}.wav"
        filepath = os.path.join(self.audio_dir, filename)
        url = f"/static/audio/{filename}"
        if os.path.exists(filepath):
            return url

        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30, transport=self.transport) as client:
                q = await client.post("/audio_query", params={"text": speech_text, "speaker": spk})
                q.raise_for_status()
                query = q.json()
                query["speedScale"] = spd
                query["pitchScale"] = query.get("pitchScale", 0.0) + pitch
                s = await client.post("/synthesis", params={"speaker": spk}, json=query)
                s.raise_for_status()
            os.makedirs(self.audio_dir, exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(s.content)
            return url
        except Exception as e:  # VOICEVOX 未启动/超时等:降级为纯文字,直播不中断
            logger.warning("VOICEVOX 合成失败,降级为纯文字: %s", e)
            return None


def build_tts() -> TTSProvider:
    provider = settings.tts_provider.lower()
    if provider == "voicevox":
        return VoicevoxTTS(settings.voicevox_url, settings.voicevox_speaker, settings.voicevox_speed,
                           settings.audio_dir, settings.tts_credit)
    return NullTTS()


tts: TTSProvider = build_tts()
