"""Горячие клавиши: клавиши без модификаторов (работают, когда не идёт ввод текста) и
сочетания пунктов меню. Пользовательские назначения хранятся в настройках; ножная педаль
или кнопка на микрофоне обычно посылают нажатие клавиши — её и назначают нужному действию."""

from __future__ import annotations

# ключ → (название, клавиша по умолчанию)
SINGLE: dict[str, tuple[str, str]] = {
    "play": ("Пуск / пауза (во время записи — стоп)", "Space"),
    "record": ("Запись / стоп (на вкладке «Запись»)", "R"),
    "play_take": ("Прослушать запись выделенной реплики", "P"),
    "new_line": ("Новая реплика в позиции курсора", "N"),
    "delete": ("Удалить выделенные реплики (в таблице)", "Del"),
    "back": ("Перемотка назад на 2 с", "Left"),
    "fwd": ("Перемотка вперёд на 2 с", "Right"),
    "prev_line": ("Предыдущая реплика роли (запись)", "["),
    "next_line": ("Следующая реплика роли (запись)", "]"),
    "marker": ("Метка на таймлайне в позиции курсора", "M"),
    "approve": ("Одобрить запись выделенных реплик", "A"),
    "redo": ("Отправить выделенные реплики на переписку", "X"),
}


def action_key(text: str) -> str:
    """Устойчивый ключ пункта меню по его тексту."""
    return "menu:" + text.replace("&", "").replace("…", "").strip()


def merged(defaults: dict[str, str], overrides: dict[str, str]) -> dict[str, str]:
    out = dict(defaults)
    out.update({k: v for k, v in overrides.items() if k in defaults})
    return out


def conflicts(assign: dict[str, str]) -> list[tuple[str, str, str]]:
    """[(сочетание, ключ1, ключ2)] — одно сочетание назначено двум действиям."""
    seen: dict[str, str] = {}
    out = []
    for k, seq in assign.items():
        s = (seq or "").strip().lower()
        if not s:
            continue
        if s in seen:
            out.append((seq, seen[s], k))
        else:
            seen[s] = k
    return out
