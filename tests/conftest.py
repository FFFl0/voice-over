import pytest


@pytest.fixture(autouse=True)
def _offline_dictionaries(monkeypatch):
    """Тесты окна не должны скачивать словари орфографии из интернета:
    уже лежащие в папке словари (как в test_spell) работают, загрузка — нет."""
    from voiceover.core import spell
    orig = spell.ensure_dictionary

    def offline(lang, folder=None):
        name = spell.LANGS[lang][1]
        d = folder or spell.dict_dir()
        if all((d / f"{name}{ext}").exists() for ext in (".aff", ".dic")):
            return orig(lang, folder)
        raise OSError("словари в тестах не скачиваются")
    monkeypatch.setattr(spell, "ensure_dictionary", offline)
