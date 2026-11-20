from voiceover.core.translate import _chunks, translate_texts


class Fake:
    """Переводчик-заглушка: верхний регистр, считает запросы."""
    def __init__(self, break_lines=False):
        self.calls = 0
        self.break_lines = break_lines

    def translate(self, text):
        self.calls += 1
        if self.break_lines and "\n" in text:
            return text.replace("\n", " ").upper()   # склеил строки — должен сработать запасной путь
        return text.upper()


def test_batches_keep_order_and_count():
    texts = [f"line {i}" for i in range(50)] + ["", "multi\nline  text"]
    tr = Fake()
    out = translate_texts(texts, tr, chunk_chars=100)
    assert out[:3] == ["LINE 0", "LINE 1", "LINE 2"]
    assert out[50] == "" and out[51] == "MULTI LINE TEXT"
    assert len(out) == len(texts)
    assert tr.calls < 15            # пачками, а не по одной


def test_fallback_per_line():
    tr = Fake(break_lines=True)
    out = translate_texts(["a", "b", "c"], tr)
    assert out == ["A", "B", "C"]


def test_chunks():
    assert _chunks(["aaaa", "bbbb", "cc"], limit=9) == [[0], [1, 2]]
