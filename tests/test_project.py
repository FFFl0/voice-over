import json

from voiceover.core.models import Line, MixMode, Project, line_status


def test_roundtrip(tmp_path):
    p = Project(video_path="x.mkv", duration=10)
    a = p.add_character("Аня", actor="Маша", volume_db=-2)
    b = p.add_character("Борис")
    p.lines.append(Line(1, 2, "Привет", a.id))
    p.lines.append(Line(3, 4, "Пока", b.id))
    p.mix.mode = MixMode.DUBBING
    path = tmp_path / "t.vop"
    p.save(str(path))
    q = Project.load(str(path))
    assert q.mix.mode == MixMode.DUBBING
    assert q.characters[0].actor == "Маша" and q.characters[0].volume_db == -2
    assert q.lines[1].text == "Пока"
    assert q.project_dir == tmp_path / "t_files"


def test_old_project_with_tts_fields_opens(tmp_path):
    """Проект прежней версии (с настройками синтеза) открывается, лишние поля игнорируются."""
    old = {"version": 1, "video_path": "", "mix": {"mode": "voiceover", "max_speedup": 1.35,
                                                   "fit_by_rate": True},
           "characters": [{"name": "A", "id": "c1", "color": "#fff", "source": "tts",
                           "engine": "edge", "voice": "ru-RU-DmitryNeural", "rate": 5,
                           "pitch": 0, "volume_db": 0, "actor": "Петя"}],
           "lines": [{"start": 0, "end": 1, "text": "x", "character_id": "c1",
                      "tts_path": "tts/a.mp3", "tts_hash": "abc", "rec_path": ""}]}
    f = tmp_path / "old.vop"
    f.write_text(json.dumps(old), encoding="utf-8")
    p = Project.load(str(f))
    assert p.characters[0].actor == "Петя" and p.lines[0].character_id == "c1"
    assert line_status(p, p.lines[0]) == "none"     # синтез больше не считается озвучкой


def test_status(tmp_path):
    p = Project(path=str(tmp_path / "t.vop"))
    a = p.add_character("A")
    ln = Line(0, 1, "текст", a.id)
    p.lines.append(ln)
    assert line_status(p, ln) == "none"
    p.project_dir.mkdir()
    (p.project_dir / "rec").mkdir()
    (p.project_dir / "rec" / "x.wav").write_bytes(b"1")
    ln.rec_path = "rec/x.wav"
    assert line_status(p, ln) == "rec"
    ln.enabled = False
    assert line_status(p, ln) == "off"
    ln.enabled = True
    p.remove_character(a.id)
    assert line_status(p, ln) == "nochar"
