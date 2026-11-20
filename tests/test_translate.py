import pytest

from voiceover.core.translate import (GoogleFree, PartialTranslation, RateLimited, _chunks,
                                      parse_gtx, translate_texts)


class Fake:
    """Переводчик-заглушка: верхний регистр, считает запросы."""
    def __init__(self, break_lines=False, fail_after=None):
        self.calls = 0
        self.break_lines = break_lines
        self.fail_after = fail_after

    def translate(self, text):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("сеть упала")
        if self.break_lines and "\n" in text:
            return text.replace("\n", " ").upper()   # склеил строки
        return text.upper()


def test_batches_keep_order_and_count():
    texts = [f"line {i}" for i in range(50)] + ["", "multi\nline  text"]
    tr = Fake()
    out = translate_texts(texts, tr, chunk_chars=100)
    assert out[:3] == ["LINE 0", "LINE 1", "LINE 2"]
    assert out[50] == "" and out[51] == "MULTI LINE TEXT"
    assert len(out) == len(texts)
    assert tr.calls < 15            # пачками, а не по одной


def test_403_lines_take_few_requests():
    texts = [f"Реплика номер {i}, довольно обычная по длине." for i in range(403)]
    tr = Fake()
    translate_texts(texts, tr)
    assert tr.calls <= 6


def test_mismatch_splits_in_halves_not_lines():
    tr = Fake(break_lines=True)
    out = translate_texts(["a", "b", "c", "d"], tr)
    assert out == ["A", "B", "C", "D"]


def test_partial_result_on_failure():
    texts = [f"x{i}" for i in range(10)]
    with pytest.raises(PartialTranslation) as ei:
        translate_texts(texts, Fake(fail_after=1), chunk_chars=12)
    e = ei.value
    assert sum(e.done) >= 3 and not all(e.done)
    assert all(r == t.upper() for r, t, d in zip(e.result, texts, e.done) if d)


def test_chunks():
    assert _chunks(["aaaa", "bbbb", "cc"], limit=9) == [[0], [1, 2]]


def test_parse_gtx():
    data = [[["Привет\n", "Hello\n", None], ["мир", "world", None]], None, "en"]
    assert parse_gtx(data) == "Привет\nмир"


class Resp:
    def __init__(self, code, data=None):
        self.status_code = code
        self._data = data

    def json(self):
        return self._data


def test_google_waits_on_429_then_succeeds():
    replies = [Resp(429), Resp(429), Resp(200, [[["да", "yes"]]])]
    waits, sleeps, calls = [], [], []

    def post(url, params, data, timeout):
        calls.append((params["sl"], params["tl"], data["q"]))
        return replies.pop(0)

    g = GoogleFree("en", "ru", min_interval=0, post=post, sleep=sleeps.append,
                   on_wait=waits.append)
    assert g.translate("yes") == "да"
    assert waits == [5.0, 10.0] and calls[0] == ("en", "ru", "yes")


def test_google_gives_up_after_limit():
    g = GoogleFree("auto", "ru", min_interval=0, max_waits=2, post=lambda *a, **k: Resp(429),
                   sleep=lambda s: None)
    with pytest.raises(RateLimited):
        g.translate("x")


# ----------------------------------------------------------------- локальный NLLB и DeepL
from voiceover.core import translate as T  # noqa: E402


def test_detect_lang():
    assert T.detect_lang("こんにちは、元気？") == "ja"
    assert T.detect_lang("안녕하세요") == "ko"
    assert T.detect_lang("你好世界") == "zh-CN"
    assert T.detect_lang("Привет, мир") == "ru"
    assert T.detect_lang("Привіт, їжак") == "uk"
    assert T.detect_lang("Hello there") == "en"


class FakeSP:
    def __init__(self, model_file):
        self.model_file = model_file

    def encode(self, text, out_type=str):
        return ["▁" + w for w in text.split()]

    def decode(self, toks):
        return " ".join(t.lstrip("▁") for t in toks)


class FakeCT2:
    calls = []

    def __init__(self, path, device="cpu", compute_type="int8", intra_threads=1):
        self.device = device

    def translate_batch(self, sources, target_prefix, **kw):
        FakeCT2.calls.append((sources, target_prefix, kw))
        from types import SimpleNamespace
        # «перевод»: целевой код + слова в верхнем регистре
        return [SimpleNamespace(hypotheses=[[tp[0]] + [w.upper() for w in src[1:-1]]])
                for src, tp in zip(sources, target_prefix)]


def test_nllb_builds_requests(monkeypatch, tmp_path):
    import types
    ct2 = types.SimpleNamespace(Translator=FakeCT2, get_cuda_device_count=lambda: 0)
    spm = types.SimpleNamespace(SentencePieceProcessor=FakeSP)
    monkeypatch.setitem(__import__("sys").modules, "ctranslate2", ct2)
    monkeypatch.setitem(__import__("sys").modules, "sentencepiece", spm)
    monkeypatch.setattr(T, "ensure_nllb", lambda size, progress=None: tmp_path)
    FakeCT2.calls.clear()
    tr = T.NllbLocal("auto", "ru")
    out = T.translate_texts(["hello world", "こんにちは 世界", "", "bye"], tr)
    assert out == ["▁HELLO ▁WORLD".replace("▁", ""), "こんにちは 世界".upper(), "", "BYE"]
    sources, prefix, kw = FakeCT2.calls[0]
    assert sources[0] == ["eng_Latn", "▁hello", "▁world", "</s>"]
    assert sources[1][0] == "jpn_Jpan"            # язык определён по письменности
    assert prefix[0] == ["rus_Cyrl"] and kw["beam_size"] == 4
    assert len(FakeCT2.calls) == 1                # одна пачка, без склейки через \n


def test_nllb_unsupported_target(monkeypatch, tmp_path):
    import types
    monkeypatch.setitem(__import__("sys").modules, "ctranslate2",
                        types.SimpleNamespace(Translator=FakeCT2, get_cuda_device_count=lambda: 0))
    monkeypatch.setitem(__import__("sys").modules, "sentencepiece",
                        types.SimpleNamespace(SentencePieceProcessor=FakeSP))
    with pytest.raises(RuntimeError):
        T.NllbLocal("en", "xx")


def test_deepl_batches_and_errors():
    sent = []

    def post(url, data, timeout, headers):
        sent.append((url, data, headers))
        texts = [v for k, v in data if k == "text"]
        return Resp(200, {"translations": [{"text": t.upper()} for t in texts]})

    d = T.DeepLApi("abc:fx", "ja", "ru", post=post)
    out = T.translate_texts([f"t{i}" for i in range(90)], d)
    assert out[0] == "T0" and len(out) == 90
    assert len(sent) == 3                                   # по 40 реплик
    url, data, headers = sent[0]
    assert url.startswith("https://api-free.deepl.com")
    assert ("target_lang", "RU") in data and ("source_lang", "JA") in data
    assert headers["Authorization"] == "DeepL-Auth-Key abc:fx"
    bad = T.DeepLApi("k", "auto", "ru", post=lambda *a, **k: Resp(456))
    with pytest.raises(PartialTranslation) as ei:
        T.translate_texts(["x"], bad)
    assert "лимит" in str(ei.value)


def test_detect_vietnamese():
    assert T.detect_lang("Hiện giờ, mình đang đi du lịch khắp nơi để dạy bóng đá cho trẻ em.") == "vi"
    assert T.detect_lang("Đi đâu?") == "vi"
    assert T.detect_lang("Where are you going?") == "en"


def test_std_streams_without_console(monkeypatch, tmp_path):
    """pythonw: stderr = None — загрузчики с полосой прогресса не должны падать."""
    import sys
    from voiceover import app
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    app.ensure_std_streams()
    assert sys.stderr is not None and sys.stdout is not None
    from tqdm import tqdm
    for _ in tqdm(range(3), disable=False, file=None):   # как в huggingface_hub/vosk-tts
        pass
    print("в журнал")
    sys.stdout.flush()
    log = tmp_path / "VoiceOverStudio" / "voiceover.log"
    assert "в журнал" in log.read_text(encoding="utf-8")


def test_deepl_tries_other_server_on_403():
    urls = []

    def post(url, data, timeout, headers):
        urls.append(url)
        if url.startswith("https://api.deepl.com"):
            return Resp(403)                       # ключ от бесплатного сервера
        return Resp(200, {"translations": [{"text": "ок"}]})

    d = T.DeepLApi("dev-key-without-fx", "vi", "ru", post=post)
    assert d.translate_batch(["xin chào"]) == ["ок"]
    assert d.translate_batch(["lần nữa"]) == ["ок"]
    assert urls == ["https://api.deepl.com/v2/translate",
                    "https://api-free.deepl.com/v2/translate",
                    "https://api-free.deepl.com/v2/translate"]   # запомнил рабочий сервер
    bad = T.DeepLApi("wrong", "auto", "ru", post=lambda *a, **k: Resp(403))
    with pytest.raises(RuntimeError, match="неверный ключ"):
        bad.translate_batch(["x"])
