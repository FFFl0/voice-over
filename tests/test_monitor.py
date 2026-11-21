import numpy as np

from voiceover.core.monitor import LIVE, LiveFx

SR = 48000


def tone(f, sec=0.5, amp=0.3):
    t = np.arange(int(sec * SR)) / SR
    return (amp * np.sin(2 * np.pi * f * t)).astype(np.float32)


def run_blocks(fx, x, n=256):
    return np.concatenate([fx.process(x[i:i + n]) for i in range(0, len(x), n)])


def rms(x):
    return float(np.sqrt(np.mean(x ** 2)))


def test_blocks_equal_whole():
    """Обработка блоками по 256 равна обработке целиком — без щелчков на стыках."""
    x = np.random.default_rng(0).normal(0, 0.1, SR // 2).astype(np.float32)
    for key in ("phone", "hall", "thoughts"):
        a = run_blocks(LiveFx(key, SR), x)
        b = LiveFx(key, SR).process(x)
        assert np.max(np.abs(a - b)) < 1e-5, key


def test_phone_band():
    fx = LiveFx("phone", SR)
    lo = run_blocks(fx, tone(100))[SR // 10:]
    fx = LiveFx("phone", SR)
    mid = run_blocks(fx, tone(1000))[SR // 10:]
    assert rms(lo) < rms(mid) * 0.1 and abs(rms(mid) / rms(tone(1000)) - 1.12) < 0.1


def test_echo_delay():
    x = np.zeros(SR // 2, np.float32)
    x[100] = 0.5
    y = run_blocks(LiveFx("room", SR), x)
    d = int(0.023 * SR)
    assert abs(y[100 + d] - 0.5 * 0.22) < 1e-6 and abs(y[100] - 0.5) < 1e-6


def test_all_effects_finite():
    x = tone(440)
    for key in LIVE:
        y = run_blocks(LiveFx(key, SR), x)
        assert len(y) == len(x) and np.isfinite(y).all() and np.abs(y).max() <= 1.0


class FakeStream:
    opened = []

    def __init__(self, **kw):
        self.kw = kw
        FakeStream.opened.append(self)

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def fake_sd(monkeypatch):
    import sys
    import types
    sd = types.SimpleNamespace(
        InputStream=lambda **kw: FakeStream(kind="in", **kw),
        Stream=lambda **kw: FakeStream(kind="duplex", **kw),
        default=types.SimpleNamespace(device=(0, 1)),
        query_devices=lambda d=None: {"hostapi": 0},
        query_hostapis=lambda i=None: {"default_output_device": 5})
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    FakeStream.opened = []


def test_recorder_duplex(monkeypatch):
    fake_sd(monkeypatch)
    from voiceover.core.recorder import Recorder
    r = Recorder(device=2)
    r.start_monitor()
    assert FakeStream.opened[-1].kw["kind"] == "in"
    r.set_monitor(LiveFx("room", SR))
    s = FakeStream.opened[-1]
    assert s.kw["kind"] == "duplex" and s.kw["device"] == (2, 5) and s.kw["channels"] == (1, 2)
    inp = np.zeros((256, 1), np.float32)
    inp[10, 0] = 0.5
    out = np.zeros((256, 2), np.float32)
    r._duplex_callback(inp, out, 256, None, None)
    assert out[10, 0] == out[10, 1] == 0.5 and r.level == 0.5
    n = len(FakeStream.opened)
    r.set_monitor(LiveFx("phone", SR))          # смена эффекта без переоткрытия потока
    assert len(FakeStream.opened) == n
    r.set_monitor(None)
    assert FakeStream.opened[-1].kw["kind"] == "in"
    r._running = True
    import pytest
    with pytest.raises(RuntimeError):
        r.set_monitor(LiveFx("", SR))
    r._running = False
