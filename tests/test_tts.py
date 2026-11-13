"""TTS Provider:VOICEVOX との通信は httpx.MockTransport で置き換える(実エンジン不要)"""
import json

import httpx

from app.tts import NullTTS, VoicevoxTTS, build_tts, clean_text


def _mock_engine(calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/audio_query":
            return httpx.Response(200, json={"speedScale": 1.0, "pitchScale": 0.0})
        if request.url.path == "/synthesis":
            return httpx.Response(200, content=b"RIFF....WAVE")
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_build_tts_respects_settings():
    assert isinstance(build_tts(), NullTTS)       # conftest で TTS_PROVIDER=none


def test_clean_text_removes_emoji():
    assert clean_text("こんにちは🎧✨") == "こんにちは"


async def test_null_tts_returns_none():
    assert await NullTTS().synthesize("テスト") is None


async def test_voicevox_applies_emotion_and_caches(tmp_path):
    calls = []
    v = VoicevoxTTS("http://engine", speaker=3, speed=1.05, audio_dir=str(tmp_path),
                    credit="VOICEVOX:ずんだもん", transport=_mock_engine(calls))
    url = await v.synthesize("ありがとうございます", "excited")
    assert url and url.startswith("/static/audio/") and len(list(tmp_path.iterdir())) == 1
    synth_body = json.loads(calls[1].content)
    assert abs(synth_body["speedScale"] - 1.05 * 1.08) < 1e-9       # 基本速度 × 感情係数
    assert synth_body["pitchScale"] == 0.02
    # 同じ文・同じ感情 → キャッシュ命中、エンジンに再リクエストしない
    assert await v.synthesize("ありがとうございます", "excited") == url
    assert len(calls) == 2


async def test_voicevox_failure_degrades_to_text(tmp_path):
    def down(request):
        raise httpx.ConnectError("engine is down")
    v = VoicevoxTTS("http://engine", 3, 1.0, str(tmp_path), "", transport=httpx.MockTransport(down))
    assert await v.synthesize("こんにちは") is None                  # 例外を投げず None(文字だけ)


async def test_emoji_only_text_is_not_sent(tmp_path):
    calls = []
    v = VoicevoxTTS("http://engine", 3, 1.0, str(tmp_path), "", transport=_mock_engine(calls))
    assert await v.synthesize("🎧✨") is None and calls == []


def test_vendor_files_exist_for_offline_demo():
    """importmap が参照するローカルファイルが揃っている(CDN なしで 3D アバターが動く)"""
    import os
    import re
    html = open("app/static/index.html", encoding="utf-8").read()
    paths = re.findall(r'"(/static/vendor/[^"]+)"', html)
    assert paths and not re.search(r"cdn\.jsdelivr|unpkg", html)
    for p in paths:
        local = "app" + p
        assert os.path.exists(local if not local.endswith("/") else local + "loaders/GLTFLoader.js"), p


async def test_audio_is_served_from_configured_audio_dir(client):
    """合成した音声は設定の AUDIO_DIR から /static/audio/ で配信される(以前は AUDIO_DIR を変えると 404)"""
    import os
    from app.config import settings
    with open(os.path.join(settings.audio_dir, "probe.wav"), "wb") as f:
        f.write(b"RIFF0000WAVE")
    r = await client.get("/static/audio/probe.wav")
    assert r.status_code == 200 and r.content == b"RIFF0000WAVE"
    assert (await client.get("/static/index.html")).status_code == 200      # 通常の静的ファイルも引き続き配信
