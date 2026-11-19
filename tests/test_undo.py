from voiceover.core.models import Line, MixMode, Project
from voiceover.core.undo import UndoHistory


def test_undo_redo():
    p = Project()
    h = UndoHistory()
    h.reset(p)
    assert not h.commit(p)          # без изменений — шага нет
    a = p.add_character("A")
    p.lines.append(Line(0, 1, "раз", a.id))
    assert h.commit(p, "Импорт")
    mix = p.mix
    p.lines[0].text = "два"
    p.mix.mode = MixMode.DUBBING
    assert h.commit(p, "Правка")
    assert h.undo_label() == "Правка"
    assert h.undo(p) == "Правка"
    assert p.lines[0].text == "раз" and p.mix.mode == MixMode.VOICEOVER
    assert p.mix is mix              # объект настроек тот же (на него ссылается интерфейс)
    assert h.undo(p) == "Импорт"
    assert p.lines == [] and p.characters == []
    assert h.undo(p) is None
    assert h.redo(p) == "Импорт"
    assert h.redo(p) == "Правка"
    assert p.lines[0].text == "два"
    # новое изменение очищает повтор
    h.undo(p)
    p.lines[0].text = "три"
    h.commit(p)
    assert not h.can_redo()


def test_limit():
    p = Project()
    h = UndoHistory(limit=3)
    h.reset(p)
    for i in range(10):
        p.lines.append(Line(i, i + 1, str(i)))
        h.commit(p)
    n = 0
    while h.undo(p):
        n += 1
    assert n == 3 and len(p.lines) == 7
