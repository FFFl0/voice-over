import numpy as np
import soundfile as sf

from voiceover.core import synth
from voiceover.core.models import Line, Project, tts_hash
from voiceover.tts.base import TTSEngine

SR = 24000


class RateEngine(TTSEngine):
    """Длина речи = 0.1 с на символ / (1 + темп), плюс тишина по краям — как у настоящих движков."""
    id = "fake"
    title = "fake"

    def __init__(self):
        self.calls = []

    def synthesize(self, text, voice, out_path, rate=0, pitch=0):
        self.calls.append((text, rate))
        n = int(SR * 0.1 * len(text) / (1 + rate / 100))
        x = np.concatenate([np.zeros(SR // 5), 0.3 * np.sin(np.arange(n) * 0.1), np.zeros(SR // 5)])
        p = out_path + ".wav"
        sf.write(p, x.astype(np.float32), SR)
        return p


def make(tmp_path, fit=True):
    p = Project(path=str(tmp_path / "p.vop"), duration=30)
    p.project_dir.mkdir(exist_ok=True)
    c = p.add_character("A")
    c.engine = type(c.engine)("edge")
    p.mix.fit_by_rate = fit
    p.lines = [Line(0, 1.0, "x" * 13, c.id),      # 1.3 с речи в окне ~1.15 с — подгоняется
               Line(1.2, 2.2, "y" * 30, c.id),    # 3.0 с в окне ~1.75 с — упрётся в +40%
               Line(3.0, 5.0, "z" * 5, c.id)]     # 0.5 с в окне 2 с — не трогаем
    return p, c


def run(p, monkeypatch, eng):
    monkeypatch.setattr(synth, "get_engine", lambda eid: eng)
    res = {}
    errs = synth.synthesize_lines(p, p.lines, lambda r: res.__setitem__(r.line_id, r))
    return errs, res


def test_fit_rate_math():
    assert synth.fit_rate(0, 1.0, 1.0, 40) is None           # влезает
    assert synth.fit_rate(0, 1.3, 1.15, 40) == 18             # 1.3/1.15·1.04 ≈ +18%
    assert synth.fit_rate(10, 3.0, 1.0, 40) == 50             # потолок: свой темп + 40
    assert synth.fit_rate(0, 3.0, 1.0, 0) is None             # подгонка выключена


def test_long_lines_are_resynthesized_faster(tmp_path, monkeypatch):
    p, c = make(tmp_path)
    eng = RateEngine()
    errs, res = run(p, monkeypatch, eng)
    assert errs == 0 and len(res) == 3
    assert [r for t, r in eng.calls if t.startswith("z")] == [0]          # короткую не трогали
    xs = [r for t, r in eng.calls if t.startswith("x")]
    ys = [r for t, r in eng.calls if t.startswith("y")]
    assert xs[0] == 0 and 15 <= xs[1] <= 30 and ys == [0, 40]
    a, b, z = p.lines
    assert f"_r{xs[1]}" in res[a.id].path and "_r40" in res[b.id].path and "_r" not in res[z.id].path
    # подогнанная реплика теперь влезает в своё время
    assert synth.speech_duration(str(p.resolve(res[a.id].path))) <= synth.line_slot(p, a) * 1.02
    # статус реплики остаётся «готово»: хэш — по тексту и голосу персонажа
    assert res[a.id].hash == tts_hash(a.text, c.tts_params())
    # повторный запуск — всё из кэша, без синтеза
    eng2 = RateEngine()
    run(p, monkeypatch, eng2)
    assert eng2.calls == []


def test_fit_disabled(tmp_path, monkeypatch):
    p, _ = make(tmp_path, fit=False)
    eng = RateEngine()
    run(p, monkeypatch, eng)
    assert all(r == 0 for _, r in eng.calls) and len(eng.calls) == 3
