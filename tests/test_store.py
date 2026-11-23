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


class _Resp:
    def __init__(self, data):
        self.data = data

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_dictionary_notice_downloaded_with_dictionary(tmp_path, monkeypatch):
    from voiceover.core import spell
    urls = []

    def fake_urlopen(url, timeout=0):
        urls.append(url)
        if url.endswith("README_ru_RU.txt"):
            return _Resp("Copyright (c) 1997-2008, Alexander I. Lebedev\n".encode())
        return _Resp(b"1\nslovo\n")

    monkeypatch.setattr(spell.urllib.request, "urlopen", fake_urlopen)
    from voiceover.core.spell import ensure_notice, downloaded_notices
    (tmp_path / "ru_RU.dic").write_text("1\nslovo\n")
    assert downloaded_notices(tmp_path) == []          # README ещё не скачан
    assert ensure_notice("ru", tmp_path).name == "README_ru_RU.txt"
    assert urls[-1].endswith("/ru_RU/README_ru_RU.txt")
    assert downloaded_notices(tmp_path) == [("README_ru_RU.txt",
                                             "Copyright (c) 1997-2008, Alexander I. Lebedev\n")]
    n = len(urls)
    ensure_notice("ru", tmp_path)                       # уже есть — не качаем снова
    assert len(urls) == n


def test_dictionary_notice_offline_is_harmless(tmp_path, monkeypatch):
    from voiceover.core import spell

    def offline(url, timeout=0):
        raise OSError("no network")

    monkeypatch.setattr(spell.urllib.request, "urlopen", offline)
    assert spell.ensure_notice("en", tmp_path) is None
    assert not list(tmp_path.iterdir())


def test_licenses_include_model_and_dictionary_texts():
    from voiceover.edition import licenses_dir
    d = licenses_dir()
    third = (d / "THIRD_PARTY.md").read_text(encoding="utf-8")
    assert (d / "CC-BY-4.0.txt").read_text().startswith("Creative Commons Attribution 4.0")
    assert "Mozilla Public License" in (d / "MPL-1.1.txt").read_text()
    for s in ("wespeaker-voxceleb-resnet34-LM", "CC-BY-4.0.txt", "Alexander I. Lebedev",
              "Kevin Atkinson", "J Ross Beresford", "Andriy Rysin", "MPL-1.1.txt"):
        assert s in third, s
