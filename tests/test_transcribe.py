from types import SimpleNamespace

import numpy as np
import soundfile as sf

from voiceover.core.transcribe import Segment, Word, segments_to_lines, split_segment, transcribe


def words(spec):
    """spec: [(start, end, word)]"""
    return [Word(a, b, w) for a, b, w in spec]


def test_split_by_sentence():
    seg = Segment(0, 4, "", words([(0, .5, " Привет,"), (.5, 1.2, " Наруто."),
                                   (1.4, 2.0, " Как"), (2.0, 2.4, " дела?"), (2.5, 4, " Отлично!")]))
    out = split_segment(seg)
    assert [s.text for s in out] == ["Привет, Наруто.", "Как дела?", "Отлично!"]
    assert out[1].start == 1.4 and out[1].end == 2.4


def test_split_by_gap_and_length():
    seg = Segment(0, 20, "", words([(0, 1, " раз"), (1, 2, " два"), (4, 5, " три")]))
    assert [s.text for s in split_segment(seg)] == ["раз два", "три"]
    long = [(i * 0.5, i * 0.5 + 0.45, f" w{i}" + ("," if i == 9 else "")) for i in range(30)]
    out = split_segment(Segment(0, 15, "", words(long)), max_len=7)
    assert all(s.end - s.start <= 7.5 for s in out)
    assert out[0].text.endswith("w9,")   # разрыв по запятой


def test_japanese_no_spaces():
    seg = Segment(0, 3, "", words([(0, .5, "こんにちは"), (.5, 1.2, "。"), (1.5, 2.8, "元気？")]))
    assert [s.text for s in split_segment(seg, min_len=0.5)] == ["こんにちは。", "元気？"]


def test_filter_garbage():
    segs = [Segment(0, 1, " Субтитры сделал DimaTorzok"), Segment(1, 2, " ..."),
            Segment(2, 3, " Нормальная фраза"),
            Segment(3, 4, " шум", no_speech_prob=0.9, avg_logprob=-1.5)]
    out = segments_to_lines(segs, offset=10)
    assert [s.text for s in out] == ["Нормальная фраза"] and out[0].start == 12


class FakeModel:
    def transcribe(self, audio, **kw):
        assert audio.dtype == np.float32 and abs(len(audio) / 16000 - 4.0) < 0.01
        assert kw["word_timestamps"]
        seg = SimpleNamespace(start=0.5, end=2.0, text=" Тест один.", no_speech_prob=0.0,
                              avg_logprob=-0.2,
                              words=[SimpleNamespace(start=0.5, end=1.0, word=" Тест"),
                                     SimpleNamespace(start=1.0, end=2.0, word=" один.")])
        return iter([seg]), SimpleNamespace(language="ru")


def test_transcribe_range(tmp_path):
    p = tmp_path / "a.wav"
    sf.write(p, np.zeros((48000 * 10, 2), np.float32), 48000)
    prog = []
    lines, lang = transcribe(str(p), t_from=3.0, t_to=7.0, model=FakeModel(),
                             progress=lambda f, s: prog.append(f))
    assert lang == "ru"
    assert len(lines) == 1 and lines[0].start == 3.5 and lines[0].text == "Тест один."
    assert prog[-1] == 0.5
