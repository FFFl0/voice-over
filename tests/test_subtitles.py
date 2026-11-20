from voiceover.core.subtitles import (fmt_ts, load_subtitles, parse_ass, parse_srt,
                                      parse_ts, parse_vtt)

SRT = """1
00:00:01,000 --> 00:00:02,500
Наруто: Я стану Хокаге!

2
00:00:03,000 --> 00:00:04,000
<i>Саске: Ну-ну.</i>

3
00:00:05,000 --> 00:00:06,000
Наруто: Вот увидишь!

4
00:00:07,000 --> 00:00:08,000
Слушай: это просто фраза.
"""

ASS = r"""[Script Info]
Title: test

[V4+ Styles]
Format: Name, Fontname
Style: Default,Arial

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:01.00,0:00:02.50,Default,Гоку,0,0,0,,{\i1}Привет,\Nмир!{\i0}
Comment: 0,0:00:03.00,0:00:04.00,Default,Гоку,0,0,0,,комментарий
Dialogue: 0,0:00:05.00,0:00:06.00,Default,Вегета,0,0,0,,Кака-а-ро-о-от!
Dialogue: 0,0:00:07.00,0:00:08.00,Sign,,0,0,0,,{\p1}m 0 0 l 10 10{\p0}
"""

VTT = """WEBVTT

00:01.000 --> 00:02.000 align:start
<v Anna>Hello there</v>

00:00:03.000 --> 00:00:04.000
Plain line
"""


def test_ts():
    assert parse_ts("01:02:03,450") == 3723.45
    assert parse_ts("0:00:05.5") == 5.5
    assert parse_ts("00:07.25") == 7.25
    assert fmt_ts(3723.45) == "01:02:03,450"


def test_srt_speakers(tmp_path):
    p = tmp_path / "a.srt"
    p.write_text(SRT, encoding="cp1251")
    lines = load_subtitles(p)
    assert [s.speaker for s in lines] == ["Наруто", "", "Наруто", ""]
    # Саске встречается 1 раз — считаем частью текста; «Слушай:» тоже
    assert lines[1].text == "Саске: Ну-ну."
    assert lines[3].text.startswith("Слушай:")
    assert lines[0].text == "Я стану Хокаге!"


def test_srt_raw():
    lines = parse_srt(SRT, detect_speakers=False)
    assert len(lines) == 4 and lines[0].speaker == ""


def test_ass():
    lines = parse_ass(ASS)
    assert len(lines) == 2
    assert lines[0].speaker == "Гоку" and lines[0].text == "Привет, мир!"
    assert lines[1].start == 5.0


def test_ass_style_fallback():
    ass = ASS.replace(",Гоку,", ",,").replace(",Вегета,", ",,").replace(
        "Dialogue: 0,0:00:05.00,0:00:06.00,Default", "Dialogue: 0,0:00:05.00,0:00:06.00,Vegeta")
    lines = parse_ass(ass)
    assert [s.speaker for s in lines] == ["Default", "Vegeta"]


def test_vtt():
    lines = parse_vtt(VTT)
    assert lines[0].speaker == "Anna" and lines[0].text == "Hello there"
    assert lines[1].start == 3.0


def test_export_items_and_ass(tmp_path):
    from voiceover.core.models import Line, Project
    from voiceover.core.subtitles import build_subtitle_items, load_subtitles, write_ass, write_srt
    p = Project()
    a = p.add_character("Тенма", color="#ec9695")
    p.lines = [Line(1, 2, "Привет, {друг}!", a.id, orig_text="Xin chào"),
               Line(3, 4.5, "Без роли", None),
               Line(5, 6, "выкл.", a.id, enabled=False)]
    items = build_subtitle_items(p.lines, p.character, "both")
    assert items[0][2] == "Привет, {друг}!\nXin chào" and items[0][3] == "Тенма"
    assert len(items) == 2                                    # выключенная пропущена
    assert build_subtitle_items(p.lines, p.character, "orig")[0][2] == "Xin chào"
    ass = tmp_path / "out.ass"
    write_ass(ass, items, "Серия 1", color_by_speaker=True)
    txt = ass.read_text(encoding="utf-8-sig")
    assert "Dialogue: 0,0:00:01.00,0:00:02.00,Default,Тенма,0,0,0,,{\\c&H9596EC&}Привет, (друг)!\\NXin chào" in txt
    back = load_subtitles(ass)                               # читается обратно нашим же импортом
    assert back[0].speaker == "Тенма" and back[0].text.startswith("Привет, (друг)!")
    srt = tmp_path / "out.srt"
    write_srt(srt, [i[:4] for i in items], with_speaker=False)
    assert load_subtitles(srt, detect_speakers=False)[1].text == "Без роли"
