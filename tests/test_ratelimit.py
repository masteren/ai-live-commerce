"""SlidingWindowLimiter の単体テスト(時間は monkeypatch で進める)"""
from app import ratelimit
from app.ratelimit import SlidingWindowLimiter


def test_allows_up_to_limit_then_blocks(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    lim = SlidingWindowLimiter(limit=3, window=10)
    assert [lim.hit("u") for _ in range(4)] == [True, True, True, False]
    assert lim.is_limited("u")


def test_window_expiry_allows_again(monkeypatch):
    t = [100.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    lim = SlidingWindowLimiter(limit=2, window=10)
    lim.hit("u"); lim.hit("u")
    assert not lim.hit("u")
    t[0] += 10.0                      # ウィンドウ経過
    assert lim.hit("u")


def test_keys_are_independent_and_reset(monkeypatch):
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: 0.0)
    lim = SlidingWindowLimiter(limit=1, window=10)
    assert lim.hit("a") and lim.hit("b")
    assert not lim.hit("a")
    lim.reset("a")
    assert lim.hit("a")


def test_empty_keys_are_removed(monkeypatch):
    t = [0.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: t[0])
    lim = SlidingWindowLimiter(limit=1, window=5)
    lim.hit("x")
    t[0] = 6.0
    assert not lim.is_limited("x")
    assert "x" not in lim._hits       # メモリが増え続けない
