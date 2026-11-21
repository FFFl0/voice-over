"""Работа с актёрами на удалёнке: пакет для актёра, отправка и загрузка записей."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel, QListWidget,
                               QListWidgetItem, QMessageBox, QVBoxLayout)

from ..core import package
from .characters_panel import color_icon


def choose_characters(win, title: str, text: str, preselect: set[str] | None = None) -> list[str]:
    p = win.project
    dlg = QDialog(win)
    dlg.setWindowTitle(title)
    lay = QVBoxLayout(dlg)
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lay.addWidget(lbl)
    lst = QListWidget()
    for c in p.characters:
        n = sum(1 for ln in p.lines if ln.character_id == c.id and ln.enabled)
        it = QListWidgetItem(color_icon(c.color),
                             f"{c.name} — {n} реплик" + (f" ({c.actor})" if c.actor else ""))
        it.setData(Qt.UserRole, c.id)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Checked if preselect and c.id in preselect else Qt.Unchecked)
        lst.addItem(it)
    lay.addWidget(lst)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    dlg.resize(420, 420)
    if dlg.exec() != QDialog.Accepted:
        return []
    return [lst.item(i).data(Qt.UserRole) for i in range(lst.count())
            if lst.item(i).checkState() == Qt.Checked]


def _base_dir(win) -> str:
    p = win.project
    return str(Path(p.path).parent) if p and p.path else ""


def export_package(win) -> None:
    p = win.project
    if not p or not p.characters:
        QMessageBox.information(win, "Пакет для актёра", "В проекте нет персонажей.")
        return
    cur = win.current_line()
    pre = {cur.character_id} if cur and cur.character_id else set()
    cids = choose_characters(win, "Пакет для актёра",
                             "Отметьте роли, которые записывает актёр. В пакет войдёт проект "
                             "со всеми репликами (для контекста) и сценарий его ролей. Видео не "
                             "кладётся — у актёра должна быть своя копия серии.", pre)
    if not cids:
        return
    names = ", ".join(c.name for c in p.characters if c.id in cids)
    stem = Path(p.path).stem if p.path else "проект"
    path, _ = QFileDialog.getSaveFileName(win, "Сохранить пакет",
                                          str(Path(_base_dir(win)) / f"{stem} — {names}.zip"),
                                          "Пакет (*.zip)")
    if not path:
        return
    try:
        n = package.export_actor_package(p, cids, path)
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(win, "Пакет для актёра", f"Не удалось сохранить пакет:\n{e}")
        return
    QMessageBox.information(win, "Пакет для актёра",
                            f"Пакет сохранён ({n} реплик):\n{path}\n\nОтправьте его актёру. "
                            "Записи, которые он пришлёт, загрузите через «Файл → Загрузить "
                            "записи актёров».")


def open_package(win) -> None:
    if win.task or not win.maybe_save():
        return
    path, _ = QFileDialog.getOpenFileName(win, "Открыть пакет актёра", "", "Пакет (*.zip)")
    if not path:
        return
    dest = Path(path).with_suffix("")
    try:
        vop = package.open_actor_package(path, str(dest))
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(win, "Пакет актёра", f"Не удалось открыть пакет:\n{e}")
        return
    win.dirty = False
    win.open_project(vop, ask=False)
    p = win.project
    if p and p.package:
        cids = p.package.get("characters") or []
        if cids:
            i = win.f_char.findData(cids[0])
            if i >= 0:
                win.f_char.setCurrentIndex(i)
        names = ", ".join(c.name for c in p.characters if c.id in cids)
        QMessageBox.information(
            win, "Пакет актёра",
            f"Ваши роли: {names}.\nПроект распакован в папку:\n{dest}\n\nЗапишите реплики на "
            "вкладке «Запись», затем «Файл → Отправить записи…».")


def send_recordings(win) -> None:
    p = win.project
    if not p:
        return
    cids = p.package.get("characters") if p.package else None
    if not cids:
        cids = choose_characters(win, "Отправить записи", "Записи каких ролей отправить?")
        if not cids:
            return
    win.save_project()
    names = ", ".join(c.name for c in p.characters if c.id in cids)
    stem = p.package.get("source") or (Path(p.path).stem if p.path else "проект")
    path, _ = QFileDialog.getSaveFileName(win, "Сохранить записи",
                                          str(Path(_base_dir(win)) / f"{stem} — записи {names}.zip"),
                                          "Записи (*.zip)")
    if not path:
        return
    try:
        n = package.export_recordings(p, path, cids)
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(win, "Отправить записи", f"Не удалось сохранить:\n{e}")
        return
    total = sum(1 for ln in p.lines if ln.character_id in cids and ln.enabled)
    QMessageBox.information(win, "Отправить записи",
                            f"Сохранено записей: {n} из {total}.\n{path}\n\nОтправьте файл режиссёру.")


def import_recordings(win) -> None:
    p = win.project
    if not p:
        return
    if not p.path:
        QMessageBox.information(win, "Записи актёров", "Сначала сохраните проект — записи "
                                                     "хранятся в его папке.")
        return
    paths, _ = QFileDialog.getOpenFileNames(win, "Загрузить записи актёров", _base_dir(win),
                                            "Записи (*.zip)")
    if not paths:
        return
    total, unknown, moved, errors = 0, 0, [], []
    for path in paths:
        try:
            rep = package.import_recordings(p, path)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{Path(path).name}: {e}")
            continue
        total += rep.imported
        unknown += rep.unknown
        moved += rep.moved
    if total:
        win.chars.reload()
        win.refresh_all()
        win.mark_dirty("Загрузка записей актёров")
    msg = [f"Загружено записей: {total}."]
    if unknown:
        msg.append(f"Не найдено реплик для {unknown} записей (их удалили или это другой проект).")
    if moved:
        msg.append(f"У {len(moved)} реплик тайминг с тех пор изменился — синхрон сохранён по "
                   "звуку фильма, проверьте их.")
    msg += errors
    (QMessageBox.warning if errors else QMessageBox.information)(
        win, "Записи актёров", "\n\n".join(msg))
