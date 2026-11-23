"""Сборка для магазинов: LGPL-кодировщики, лицензии, редакция."""
import sys

from voiceover.core import ffmpeg as F


def _with_encoders(monkeypatch, text):
    monkeypatch.setattr(F, "_encoders_cache", {F.find_ffmpeg(): text})


def test_h264_prefers_x264_then_lgpl_encoders(monkeypatch):
    _with_encoders(monkeypatch, " V....D libx264  H.264\n V....D libopenh264 x\n")
    assert F.h264_args(20, 1920, 1080, 25)[:2] == ["-c:v", "libx264"]
    _with_encoders(monkeypatch, " V....D libopenh264  OpenH264\n V....D h264_mf  MF\n")
    a = F.h264_args(20, 1920, 1080, 25)
    assert a[:2] == ["-c:v", "libopenh264"] and a[a.index("-b:v") + 1] == "5184k"
    lower = F.h264_args(26, 1920, 1080, 25)                 # больше crf — меньше битрейт
    assert int(lower[lower.index("-b:v") + 1][:-1]) < 5184
    _with_encoders(monkeypatch, " V....D h264_mf  MediaFoundation\n")
    assert F.h264_args(20, 1280, 720, 24)[:2] == ["-c:v", "h264_mf"]
    _with_encoders(monkeypatch, "")
    assert F.h264_args(20, 640, 360, 25)[:2] == ["-c:v", "mpeg4"]


def test_licenses_shipped():
    from voiceover.edition import licenses_dir
    d = licenses_dir()
    for name in ("THIRD_PARTY.md", "LGPL-3.0.txt", "GPL-3.0.txt", "LGPL-2.1.txt", "OFL-1.1-Manrope.txt"):
        assert (d / name).is_file(), name


def test_whisper_imports_without_pyav(monkeypatch):
    class Block:
        def find_spec(self, name, path=None, target=None):
            if name == "av" or name.startswith("av."):
                raise ImportError("blocked")
    for m in [m for m in sys.modules if m == "av" or m.startswith(("av.", "faster_whisper"))]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(sys, "meta_path", [Block()] + sys.meta_path)
    from voiceover.core.transcribe import whisper_available
    ok, _ = whisper_available()
    import importlib.util
    if importlib.util.find_spec("faster_whisper") is not None:
        assert ok and not hasattr(sys.modules["av"], "open")


def test_steam_vdf(tmp_path, monkeypatch):
    sys.path.insert(0, "steam")
    try:
        import make_vdf
    finally:
        sys.path.remove("steam")
    monkeypatch.setattr(make_vdf, "OUT", tmp_path)
    app = make_vdf.main(["make_vdf.py", "1234560", "1234561", 'v1 "beta"']).read_text()
    depot = (tmp_path / "depot_build_1234561.vdf").read_text()
    assert '"AppID" "1234560"' in app and '"1234561" "depot_build_1234561.vdf"' in app
    assert '"SetLive" ""' in app and "\\" not in app and "\"v1 'beta'\"" in app
    assert '"DepotID" "1234561"' in depot and '"Recursive" "1"' in depot
    assert app.count("{") == app.count("}") and depot.count("{") == depot.count("}")
    import pytest
    with pytest.raises(SystemExit):
        make_vdf.main(["make_vdf.py", "abc", "1"])
