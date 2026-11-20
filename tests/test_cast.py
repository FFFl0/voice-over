from voiceover.core import cast
from voiceover.core.models import Project


def test_save_and_apply(monkeypatch, tmp_path):
    monkeypatch.setattr(cast, "library_path", lambda: tmp_path / "cast_library.json")
    ep1 = Project()
    a = ep1.add_character("Тенма", actor="Саша", volume_db=-3, color="#123456")
    b = ep1.add_character("Аки", actor="Маша")
    assert cast.save_cast("Inazuma", [a, b]) == 2
    ep2 = Project()
    ep2.add_character("тенма")                   # другое написание — тот же персонаж
    upd, add = cast.apply_cast(ep2, "Inazuma", add_missing=True)
    assert (upd, add) == (1, 1) and ep2.cast_name == "Inazuma"
    t = ep2.character_by_name("Тенма")
    assert t.actor == "Саша" and t.volume_db == -3 and t.color == "#123456"
    assert t.name == "тенма"                     # имя и id персонажа в проекте не меняются
    k = ep2.character_by_name("Аки")
    assert k.actor == "Маша"


def test_merge_and_only(monkeypatch, tmp_path):
    monkeypatch.setattr(cast, "library_path", lambda: tmp_path / "lib.json")
    p = Project()
    a = p.add_character("A", actor="v1")
    cast.save_cast("S", [a])
    b = p.add_character("B", actor="v2")
    a.actor = "v3"
    cast.save_cast("S", [a, b])                  # дополнение: A обновлён, B добавлен
    assert {c["name"]: c["actor"] for c in cast.get_cast("S")} == {"A": "v3", "B": "v2"}
    q = Project()
    new = q.add_character("B")
    q.add_character("A", actor="свой")
    upd, add = cast.apply_cast(q, "S", only=[new])
    assert upd == 1 and add == 0 and q.character_by_name("A").actor == "свой"
    assert cast.list_casts() == ["S"]
    cast.delete_cast("S")
    assert cast.list_casts() == []


def test_broken_library(monkeypatch, tmp_path):
    f = tmp_path / "lib.json"
    f.write_text("{не json")
    monkeypatch.setattr(cast, "library_path", lambda: f)
    assert cast.list_casts() == []


def test_project_keeps_cast_name(tmp_path):
    p = Project(cast_name="Inazuma")
    p.save(str(tmp_path / "p.vop"))
    assert Project.load(str(tmp_path / "p.vop")).cast_name == "Inazuma"
