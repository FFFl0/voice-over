import json
import zipfile

import numpy as np
import pytest
import soundfile as sf

from voiceover.core import package
from voiceover.core.models import Line, Project


def director(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    p = Project(video_path=str(d / "ep01.mkv"), duration=20, path=str(d / "ep01.vop"))
    a, b = p.add_character("Наруто"), p.add_character("Сакура")
    p.lines += [Line(1, 2, "Я стану Хокаге!", a.id), Line(3, 4, "Наруто!", b.id),
                Line(5, 6, "Есть!", a.id, note="громко")]
    p.original_wav = "orig.wav"
    p.save()
    return p, a, b


def test_round_trip(tmp_path):
    p, a, b = director(tmp_path)
    pkg = tmp_path / "pkg.zip"
    assert package.export_actor_package(p, [a.id], str(pkg)) == 2
    with zipfile.ZipFile(pkg) as z:
        script = z.read("Сценарий.txt").decode()
        assert "Наруто — 2 реплик" in script and "(громко)" in script and "Сакура" not in script
    # актёр
    vop = package.open_actor_package(str(pkg), str(tmp_path / "actor"))
    q = Project.load(vop)
    assert q.package["characters"] == [a.id] and q.video_path == "ep01.mkv"
    (tmp_path / "ep01.mkv").write_bytes(b"x")          # своя копия серии рядом с пакетом
    q2 = Project.load(package.open_actor_package(str(pkg), str(tmp_path / "actor2")))
    assert q2.video_path == str((tmp_path / "ep01.mkv").resolve())
    assert q.original_wav == "" and all(not ln.rec_path for ln in q.lines)
    rec = q.project_dir / "rec"
    rec.mkdir(parents=True)
    for i, ln in enumerate(q.lines):
        sf.write(rec / f"{ln.id}_1.wav", np.full(4800, 0.1 * (i + 1), np.float32), 48000)
        ln.rec_path, ln.rec_offset = f"rec/{ln.id}_1.wav", 0.2
    out = tmp_path / "recs.zip"
    assert package.export_recordings(q, str(out)) == 2      # только роль из пакета
    # режиссёр тем временем сдвинул первую реплику на 0.5 с
    p.lines[0].start, p.lines[0].end = 1.5, 2.5
    rep = package.import_recordings(p, str(out))
    assert rep.imported == 2 and rep.unknown == 0 and rep.moved == ["Я стану Хокаге!"]
    l0, l2 = p.lines[0], p.lines[2]
    assert p.resolve(l0.rec_path).exists() and l0.rec_path.endswith("_1.wav")
    assert abs(l0.rec_offset - (0.2 - 0.5)) < 1e-9      # звук остался на месте в фильме
    assert abs(l2.rec_offset - 0.2) < 1e-9 and not p.lines[1].rec_path
    # повторная загрузка — новый дубль, старый остаётся
    package.import_recordings(p, str(out))
    assert l2.rec_path.endswith("_2.wav") and p.resolve(f"rec/{l2.id}_1.wav").exists()


def test_unknown_and_bad_archives(tmp_path):
    p, a, b = director(tmp_path)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(ValueError):
        package.import_recordings(p, str(bad))
    with pytest.raises(ValueError):
        package.open_actor_package(str(bad), str(tmp_path / "x"))
    other = tmp_path / "other.zip"
    with zipfile.ZipFile(other, "w") as z:
        z.writestr("rec/zzz.wav", b"RIFF")
        z.writestr(package.MANIFEST, json.dumps({"items": [
            {"id": "zzz", "file": "rec/zzz.wav"}, {"id": p.lines[0].id, "file": "../../evil.wav"}]}))
    rep = package.import_recordings(p, str(other))
    assert rep.imported == 0 and rep.unknown == 2


def test_package_paths_stay_inside(tmp_path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../escape.vop", json.dumps(Project().to_dict()))
    dest = tmp_path / "out"
    vop = package.open_actor_package(str(evil), str(dest))
    assert vop == str(dest / "escape.vop") and not (tmp_path.parent / "escape.vop").exists()
