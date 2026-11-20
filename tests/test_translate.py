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
