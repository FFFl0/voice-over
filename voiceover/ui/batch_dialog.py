"""Окно пакетной обработки серий."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout)

from ..core import ffmpeg
from ..core.batch import STEPS, run_batch
from ..core.models import PROJECT_EXT
from . import theme
from .workers import Task


class BatchDialog(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Пакетная обработка серий")
        self.setMinimumSize(620, 520)
        info = QLabel("Добавьте проекты серий — они будут обработаны по очереди. Экспорт "
                      "использует последние настройки окна экспорта (громкость, логотип, "
                      "заставка); видео сохраняется рядом с исходным как «имя.voiceover.mkv».")
        info.setWordWrap(True)
        self.list = QListWidget()
        self.list.setSelectionMode(QListWidget.ExtendedSelection)
        add = QPushButton(theme.icon("plus"), " Добавить проекты…")
        rem = QPushButton(theme.icon("trash"), " Убрать")
        add.clicked.connect(self._add)
        rem.clicked.connect(lambda: [self.list.takeItem(self.list.row(i))
                                     for i in self.list.selectedItems()])
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        self.steps = {}
        lay = QVBoxLayout(self)
        lay.addWidget(info)
        lay.addLayout(row)
        lay.addWidget(self.list, 1)
        for key, title in STEPS.items():
            cb = QCheckBox(title)
            cb.setChecked(key in ("extract", "export"))
            self.steps[key] = cb
            lay.addWidget(cb)
        self.burn = QCheckBox("Вшить субтитры (текст реплик)")
        self.burn.setEnabled(ffmpeg.has_filter("subtitles"))
        self.overwrite = QCheckBox("Переделывать уже готовое (заново отделять голоса, "
                                   "перезаписывать видео)")
        lay.addWidget(self.burn)
        lay.addWidget(self.overwrite)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Начать")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._start)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        p = win.project
        if p and p.path:
            self.list.addItem(p.path)

    def _add(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Проекты серий", "",
                                                f"Проекты (*{PROJECT_EXT})")
        have = {self.list.item(i).text() for i in range(self.list.count())}
        for x in sorted(paths):
            if x not in have:
                self.list.addItem(x)

    def paths(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def _start(self):
        paths = self.paths()
        steps = {k for k, cb in self.steps.items() if cb.isChecked()}
        if not paths or not steps:
            QMessageBox.information(self, "Пакетная обработка", "Добавьте проекты и выберите шаги.")
            return
        self.accept()
        run(self.win, paths, steps, self.burn.isChecked(), self.overwrite.isChecked())


def run(win, paths, steps, burn, overwrite):
    s = win.settings
    cur = win.project
    if cur and cur.path in paths:            # открытый проект: сохранить и потом перечитать
        if not win.save_project():
            return
    opts = ffmpeg.ExportOptions(loudness=s.export_loudness, logo=s.export_logo,
                                logo_pos=s.export_logo_pos, intro=s.export_intro)

    def work(t: Task):
        return run_batch(paths, steps, opts, s.demucs_model, s.demucs_device, burn, overwrite,
                         t.report, t.is_cancelled)

    def done(items):
        if cur and cur.path in paths and win.project is cur:
            win.dirty = False
            win.open_project(cur.path, ask=False)
        lines = []
        for it in items:
            name = Path(it.path).stem
            lines.append(f"✓ {name}: {', '.join(it.done) or 'нечего делать'}" if it.ok
                         else f"✗ {name}: {it.message}")
            lines += [f"    {w}" for w in it.warnings[:5]]
        ok = sum(1 for it in items if it.ok)
        win.show_report("Пакетная обработка", f"Готово серий: {ok} из {len(items)}.", lines,
                        "Результаты:")

    win.run_task(Task("Пакетная обработка", work), done)


def open_batch(win):
    if win.task:
        return
    BatchDialog(win).exec()

