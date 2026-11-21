import numpy as np
import soundfile as sf

from voiceover.core import stems
from voiceover.core.models import Line

from .test_mixer import SR, make_project, tone


def test_export_stems(tmp_path):
    p = make_project(tmp_path)
    b = p.add_character("Б/В:*")                      # имя с недопустимыми для файла знаками
    sf.write(p.project_dir / "w.wav", tone(1.0, 500, 0.3), SR)
    p.lines.append(Line(4.0, 5.0, 'Фраза "в кавычках"', b.id, rec_path="w.wav"))
    out = tmp_path / "stems"
    files = stems.export_stems(p, str(out))
    names = sorted(f.split("/")[-1] for f in files)
    assert names == sorted(["Фон.wav", "Голоса — A.wav", "Голоса — Б_В__.wav", "Микс.wav",
                            "Озвучка.RPP"])
    mix, _ = sf.read(out / "Микс.wav")
    parts = sum(sf.read(out / n)[0] for n in ("Фон.wav", "Голоса — A.wav", "Голоса — Б_В__.wav"))
    assert mix.shape == parts.shape and np.max(np.abs(mix - parts)) < 2e-3   # дорожки = микс
    rpp = (out / "Озвучка.RPP").read_text(encoding="utf-8")
    assert rpp.count("<TRACK") == 3 and rpp.count("<ITEM") == 3
    assert 'FILE "Реплики/0001 A.wav"' in rpp and "Фраза 'в кавычках'" in rpp
    assert len(list((out / "Реплики").glob("*.wav"))) == 2
    assert rpp.count("<") == rpp.count(">")
