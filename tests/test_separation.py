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
