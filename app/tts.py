"""VOICEVOX 语音合成客户端(支持情绪声线)

流程(VOICEVOX ENGINE 标准两段式 API):
  1. POST /audio_query?text=...&speaker=N  → 返回合成参数 JSON
  2. POST /synthesis?speaker=N (body=上一步JSON) → 返回 wav 音频

speaker/speed/pitch 可按情绪覆盖(见 ai_host.EMOTION_VOICE)。
结果按 文本+声线参数 的 md5 缓存到 app/static/audio/,同一句话不重复合成。
VOICEVOX 不可用时静默降级为 None(纯文字模式),不影响直播主流程。
"""
import hashlib
import os
import re
from typing import Optional

import httpx

from .config import settings

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F000-\U0001F02F"
    "\u2600-\u26FF\u2B00-\u2BFF"
    "]+",
    flags=re.UNICODE,
)


def _clean(text: str) -> str:
    """去掉表情符号等 TTS 读不了的字符"""
    return _EMOJI_RE.sub("", text).strip()


async def synthesize(text: str, speaker: Optional[int] = None,
                     speed: Optional[float] = None, pitch: float = 0.0) -> Optional[str]:
    """合成语音,返回前端可访问的 URL(/static/audio/xxx.wav);失败返回 None"""
    if not settings.voicevox_enabled:
        return None
    speech_text = _clean(text)
    if not speech_text:
        return None

    spk = speaker if speaker is not None else settings.voicevox_speaker
    spd = speed if speed is not None else settings.voicevox_speed

    key = hashlib.md5(f"{speech_text}|{spk}|{spd}|{pitch}".encode()).hexdigest()
    filename = f"{key}.wav"
    filepath = os.path.join(settings.audio_dir, filename)
    url = f"/static/audio/{filename}"
    if os.path.exists(filepath):
        return url

    try:
        async with httpx.AsyncClient(base_url=settings.voicevox_url, timeout=30) as client:
            q = await client.post("/audio_query", params={"text": speech_text, "speaker": spk})
            q.raise_for_status()
            query = q.json()
            query["speedScale"] = spd
            query["pitchScale"] = query.get("pitchScale", 0.0) + pitch

            s = await client.post("/synthesis", params={"speaker": spk}, json=query)
            s.raise_for_status()

        os.makedirs(settings.audio_dir, exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(s.content)
        return url
    except Exception as e:  # VOICEVOX 未启动/超时等:降级为纯文字
        print(f"[tts] VOICEVOX 合成失败,降级纯文字: {e}")
        return None
