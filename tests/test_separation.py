import numpy as np
import soundfile as sf

from voiceover.core.separation import process_in_chunks

SR = 8000


def test_chunks_seamless(tmp_path):
    """Кусочная обработка с кроссфейдом должна давать тот же результат, что и целиком."""
    rng = np.random.default_rng(1)
    x = (rng.standard_normal((SR * 7 + 123, 2)) * 0.1).astype(np.float32)
    src = tmp_path / "in.wav"
    sf.write(src, x, SR, subtype="FLOAT")
    calls = []

    def fake_vocals(chunk, sr):
        calls.append(len(chunk))
        return chunk * 0.25   # «голоса» — четверть сигнала

    bg, vo = tmp_path / "bg.wav", tmp_path / "vo.wav"
    process_in_chunks(str(src), str(bg), str(vo), fake_vocals,
                      segment_sec=2.0, overlap_sec=0.5)
    assert len(calls) > 3
    b, _ = sf.read(bg, dtype="float32")
    v, _ = sf.read(vo, dtype="float32")
    assert b.shape == x.shape and v.shape == x.shape
    assert np.max(np.abs(b - x * 0.75)) < 2e-4   # погрешность PCM16
    assert np.max(np.abs(v - x * 0.25)) < 2e-4
    assert not (tmp_path / "bg.wav.part").exists()


def test_short_file(tmp_path):
    x = np.zeros((100, 1), np.float32)
    src = tmp_path / "in.wav"
    sf.write(src, x, SR)
    process_in_chunks(str(src), str(tmp_path / "bg.wav"), None, lambda c, sr: c * 0)
    assert sf.info(str(tmp_path / "bg.wav")).frames == 100


# ---------------------------------------------------------------- отдельный процесс
import json
import sys
import time

import pytest

from voiceover.core import separation
from voiceover.core.ffmpeg import Cancelled

FAKE_WORKER = r'''
import json, os, sys, time
a = json.loads(sys.argv[1])
open(a["log"], "a").write(a["device"] + "\n")
mode = a["mode"]
if mode == "slow":
    time.sleep(30)
if mode == "crash" or (mode == "gpu_crash" and a["device"] != "cpu"):
    print("нативный сбой", file=sys.stderr)
    sys.stderr.flush()
    os._exit(134)
if mode == "error":
    print(json.dumps({"error": "файл повреждён", "cuda": False}), flush=True)
    sys.exit(2)
print("посторонний вывод библиотеки", flush=True)
print(json.dumps({"p": 0.5, "t": "половина"}), flush=True)
open(a["bg"], "w").write("ok")
print(json.dumps({"done": 1, "device": a["device"]}), flush=True)
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    script = tmp_path / "worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    calls = tmp_path / "calls.txt"
    calls.write_text("")
    state = {"mode": "ok"}

    def cmd(payload):
        a = json.loads(payload)
        a.update(mode=state["mode"], log=str(calls))
        return [sys.executable, str(script), json.dumps(a)]

    monkeypatch.setattr(separation, "worker_command", cmd)
    monkeypatch.setattr(separation, "demucs_available", lambda: (True, ""))
    state["calls"] = lambda: calls.read_text().split()
    return state


def test_worker_success(tmp_path, fake):
    seen = []
    bg = tmp_path / "bg.wav"
    separation.separate_vocals("in.wav", str(bg), None, progress=lambda f, t: seen.append((f, t)))
    assert bg.read_text() == "ok" and seen == [(0.5, "половина")]
    assert fake["calls"]() == ["auto"]


def test_worker_crash_on_gpu_retries_on_cpu(tmp_path, fake):
    """Аварийное завершение процесса не роняет программу, а повтор идёт на процессоре."""
    fake["mode"] = "gpu_crash"
    msgs = []
    separation.separate_vocals("in.wav", str(tmp_path / "bg.wav"),
                               progress=lambda f, t: msgs.append(t))
    assert fake["calls"]() == ["auto", "cpu"]
    assert any("процессоре" in m for m in msgs)


def test_worker_crash_reports_log(tmp_path, fake):
    fake["mode"] = "crash"
    with pytest.raises(RuntimeError) as e:
        separation.separate_vocals("in.wav", str(tmp_path / "bg.wav"))
    assert "аварийно" in str(e.value) and "нативный сбой" in str(e.value)
    assert fake["calls"]() == ["auto", "cpu"]


def test_worker_error_no_retry(tmp_path, fake):
    fake["mode"] = "error"
    with pytest.raises(RuntimeError, match="файл повреждён"):
        separation.separate_vocals("in.wav", str(tmp_path / "bg.wav"))
    assert fake["calls"]() == ["auto"]   # обычная ошибка — повтор на процессоре не поможет


def test_worker_cancel_kills_process(tmp_path, fake):
    fake["mode"] = "slow"
    t0 = time.time()
    with pytest.raises(Cancelled):
        separation.separate_vocals("in.wav", str(tmp_path / "bg.wav"),
                                   cancel=lambda: time.time() - t0 > 0.5)
    assert time.time() - t0 < 5


def test_worker_command_real_entry():
    cmd = separation.worker_command("{}")
    assert cmd[-1] == "{}" and "worker_main" in cmd[2]
