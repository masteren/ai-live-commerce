"""音声合成(TTS):差し替えできる Provider 構造

呼び出し側(ai_host)は TTSProvider のインターフェースだけに依存する:
    audio_url = await tts.synthesize(text, emotion)   # 失敗・無効のときは None(文字だけで続ける)

実装:
  - VoicevoxTTS : VOICEVOX ENGINE(日本語。感情に応じて話す速さ・高さを少し変える)
  - NullTTS     : 音を出さない(文字だけ。テストやエンジンのない環境用)
新しいエンジンを足すときは、クラスを1つ書いて build_tts() に登録するだけで、ほかのコードは変えなくてよい。
.env の TTS_PROVIDER で切り替える。

VOICEVOX の API は2段階:
  1. POST /audio_query?text=...&speaker=N  → 合成用のパラメータ(JSON)
  2. POST /synthesis?speaker=N (本文=1 の JSON) → wav
結果は「文章+声の設定」の md5 を名前にして AUDIO_DIR に保存し、同じ文章は合成し直さない。
キャッシュは AUDIO_CACHE_MAX_FILES 件まで。超えたら「最後に使われたのが古いもの」から消す(LRU)。
"""
import asyncio
import hashlib
import logging
import os
import re
from typing import Optional, Protocol

import httpx

from .config import settings
from .http_pool import SharedClient

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
    """絵文字など、TTS が読めない文字を取り除く"""
    return _EMOJI_RE.sub("", text).strip()


class TTSProvider(Protocol):
    name: str
    credit: str      # 画面に出すクレジット(例:VOICEVOX:ずんだもん)。不要なら空

    async def synthesize(self, text: str, emotion: str = "normal") -> Optional[str]:
        """音声を合成し、画面から取得できる URL を返す。失敗したら None"""
        ...

    async def aclose(self) -> None:
        """使い回している HTTP 接続を閉じる(サーバ終了時)"""
        ...


class NullTTS:
    name = "none"
    credit = ""

    async def synthesize(self, text: str, emotion: str = "normal") -> Optional[str]:
        return None

    async def aclose(self) -> None:
        pass


class VoicevoxTTS:
    name = "voicevox"

    # 感情 → 話す速さ・高さの微調整(声は1つにそろえて落ち着いた印象に。speaker=None は .env の設定を使う)
    EMOTION_VOICE = {
        "normal":   {"speaker": None, "speed": 1.0,  "pitch": 0.0},
        "happy":    {"speaker": None, "speed": 1.04, "pitch": 0.01},
        "excited":  {"speaker": None, "speed": 1.08, "pitch": 0.02},
        "troubled": {"speaker": None, "speed": 0.95, "pitch": -0.01},
        "shy":      {"speaker": None, "speed": 0.97, "pitch": 0.0},
    }

    def __init__(self, base_url: str, speaker: int, speed: float, audio_dir: str, credit: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None, max_files: int = 500) -> None:
        self.base_url = base_url
        self.speaker = speaker
        self.speed = speed
        self.audio_dir = audio_dir
        self.credit = credit
        self.max_files = max_files
        # 接続を使い回す(app/http_pool.py)。テストでは transport に httpx.MockTransport を渡し、実エンジンにつながない
        self.http = SharedClient(30, base_url=base_url, transport=transport)

    async def aclose(self) -> None:
        await self.http.aclose()

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
        if await asyncio.to_thread(_touch_if_exists, filepath):
            return url                     # キャッシュにあった(使った印に更新時刻を新しくする → 消されにくくなる)

        try:
            client = self.http.get()
            q = await client.post("/audio_query", params={"text": speech_text, "speaker": spk})
            q.raise_for_status()
            query = q.json()
            query["speedScale"] = spd
            query["pitchScale"] = query.get("pitchScale", 0.0) + pitch
            s = await client.post("/synthesis", params={"speaker": spk}, json=query)
            s.raise_for_status()
            # ファイル操作は別スレッドで(その間も配信を止めない)
            await asyncio.to_thread(_save_and_prune, self.audio_dir, filepath, s.content, self.max_files)
            return url
        except Exception as e:  # VOICEVOX が起動していない・時間切れなど:文字だけにして、配信は止めない
            logger.warning("VOICEVOX の合成に失敗したため、文字だけにします: %s", e)
            return None


def _touch_if_exists(path: str) -> bool:
    try:
        os.utime(path)
        return True
    except FileNotFoundError:
        return False


def _save_and_prune(audio_dir: str, path: str, content: bytes, max_files: int) -> None:
    """音声を保存し、キャッシュが上限を超えていたら、最後に使われたのが古いものから消す"""
    os.makedirs(audio_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)          # 書きかけのファイルを配信しないよう、書き終えてから名前を付ける
    files = []
    for entry in os.scandir(audio_dir):
        if entry.name.endswith(".wav") and entry.path != path:
            try:
                files.append((entry.stat().st_mtime, entry.path))
            except FileNotFoundError:
                pass
    excess = len(files) + 1 - max_files
    if excess > 0:
        for _, old in sorted(files)[:excess]:
            try:
                os.remove(old)
            except FileNotFoundError:
                pass
        logger.info("音声キャッシュを %d 件削除しました(上限 %d 件)", excess, max_files)


def build_tts() -> TTSProvider:
    provider = settings.tts_provider.lower()
    if provider == "voicevox":
        return VoicevoxTTS(settings.voicevox_url, settings.voicevox_speaker, settings.voicevox_speed,
                           settings.audio_dir, settings.tts_credit, max_files=settings.audio_cache_max_files)
    return NullTTS()


tts: TTSProvider = build_tts()
