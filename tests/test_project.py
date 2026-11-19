from voiceover.core.models import (Engine, LineSource, MixMode, Project, line_status,
                                   tts_hash, Line)


def test_roundtrip(tmp_path):
    p = Project(video_path="x.mkv", duration=10)
    a = p.add_character("Аня", engine=Engine.SILERO, voice="baya")
    b = p.add_character("Борис", source=LineSource.RECORDING)
    p.lines.append(Line(1, 2, "Привет", a.id))
    p.lines.append(Line(3, 4, "Пока", b.id))
    p.mix.mode = MixMode.DUBBING
    path = tmp_path / "t.vop"
    p.save(str(path))
    q = Project.load(str(path))
    assert q.mix.mode == MixMode.DUBBING
    assert q.characters[0].engine == Engine.SILERO
    assert q.characters[1].source == LineSource.RECORDING
    assert q.lines[1].text == "Пока"
    assert q.project_dir == tmp_path / "t_files"


def test_status(tmp_path):
    p = Project(path=str(tmp_path / "t.vop"))
    a = p.add_character("A")
    ln = Line(0, 1, "текст", a.id)
    p.lines.append(ln)
    assert line_status(p, ln) == "none"
    p.project_dir.mkdir()
    (p.project_dir / "x.mp3").write_bytes(b"1")
    ln.tts_path = "x.mp3"
    ln.tts_hash = tts_hash("текст", a.tts_params())
    assert line_status(p, ln) == "ok"
    ln.text = "другой"
    assert line_status(p, ln) == "stale"
    p.remove_character(a.id)
    assert line_status(p, ln) == "nochar"
