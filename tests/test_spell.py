import os

import pytest

from voiceover.core.spell import ACCENT, SpellChecker, strip_accents, toggle_accent

pytest.importorskip("spylls")


@pytest.fixture
def checker(tmp_path):
    (tmp_path / "ru_RU.aff").write_text("SET UTF-8\nTRY оеиа\n", encoding="utf-8")
    words = ["я", "стану", "привет", "что", "то", "шёпотом", "замок", "кто", "нибудь"]
    (tmp_path / "ru_RU.dic").write_text(f"{len(words)}\n" + "\n".join(words) + "\n",
                                        encoding="utf-8")
    s = SpellChecker("ru", tmp_path)
    assert s.load(), s.error
    return s


def test_errors(checker):
    checker.set_extra(["Хокаге"])
    text = f"Я стану Хокаге! Превет, что-то шёпотом, за{ACCENT}мок, OK кто-нибудь 3D"
    assert [w for _, _, w in checker.errors(text)] == ["Превет"]
    assert "привет" in checker.suggest("превет")


def test_user_dictionary(checker, tmp_path):
    assert not checker.check("Конохи")
    checker.add_word("Конохи")
    assert checker.check("конохи")
    again = SpellChecker("ru", tmp_path)
    assert "конохи" in again.user_words


def test_toggle_accent():
    t, p = toggle_accent("замок", 2)                  # курсор после «за»
    assert t == "за" + ACCENT + "мок" and p == 3
    assert toggle_accent(t, 3) == ("замок", 2)       # повторно — снять
    assert toggle_accent("мкр", 2) == ("мкр", 2)      # нет гласной — ничего
    assert strip_accents(t) == "замок"


def test_prompt_html():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from voiceover.ui.record_panel import prompt_html
    h = prompt_html("(смеётся) <b>Ну</b> [шёпотом]")
    assert "&lt;b&gt;" in h and h.count("font-style:italic") == 2
