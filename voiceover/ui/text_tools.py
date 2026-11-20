"""Инструменты текста в главном окне: перевод реплик, возврат оригинала, субтитры с видео (OCR).

Логика вынесена из main_window.py, чтобы окно только подключало действия.
Все изменения проекта проходят через mark_dirty — их можно отменить (Ctrl+Z).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PySide6.QtWidgets import QDialog, QMessageBox

from ..core.models import Line
from .workers import Task

if TYPE_CHECKING:
    from .main_window import MainWindow


def make_translator(settings, src: str, dst: str, on_wait, task: Task):
    """Переводчик по настройкам: локальная модель, DeepL или Google."""
    from ..core import translate as tr
    eng = settings.translate_engine or ("local" if tr.nllb_available()[0] else "google")
    if eng == "local":
        return tr.NllbLocal(src, dst, settings.nllb_size,
                            progress=lambda text: task.report(0.0, text))
    if eng == "deepl":
        return tr.DeepLApi(settings.deepl_key, src, dst, on_wait=on_wait)
    return tr.google_translator(src, dst, on_wait=on_wait, cancel=task.is_cancelled)


def translate_lines(win: "MainWindow", lines: Optional[list[Line]] = None) -> None:
    """Перевести реплики. lines=None — спросить в диалоге (выделенные или все)."""
    from ..core.translate import PartialTranslation, translate_texts, translator_available
    from .dialogs import TranslateDialog

    p = win.project
    if not p:
        return
    ok, why = translator_available()
    if not ok:
        QMessageBox.warning(win, "Перевод", why)
        return
    selected = win.selected_lines()
    skip_done = True
    if lines is None:
        dlg = TranslateDialog(win.settings, len(selected), len(p.lines), win)
        if dlg.exec() != QDialog.Accepted:
            return
        lines = selected if dlg.scope_sel.isChecked() else list(p.lines)
        skip_done = dlg.skip_done.isChecked()
    total_asked = len(lines)
    if skip_done:
        lines = [ln for ln in lines if not ln.orig_text]
    # уже переведённые (если не пропускаем) переводятся заново — с оригинала
    lines = [ln for ln in lines if (ln.orig_text or ln.text).strip()]
    if not lines:
        win.statusBar().showMessage(
            "Все выбранные реплики уже переведены" if total_asked else "Нет реплик для перевода", 6000)
        return
    src, dst = win.settings.translate_source, win.settings.translate_target
    sources = [ln.orig_text or ln.text for ln in lines]
    ids = [ln.id for ln in lines]
    state = {"frac": 0.0, "applied": 0, "error": ""}

    def work(t: Task):
        def prog(f, text):
            state["frac"] = f
            t.report(f, text)

        def on_wait(sec):
            t.report(state["frac"], f"Переводчик просит подождать: пауза {sec:.0f} с, затем перевод продолжится")

        tr = make_translator(win.settings, src, dst, on_wait, t)
        try:
            result = translate_texts(sources, tr, prog, t.is_cancelled)
            t.emit((result, [True] * len(result), ""))
        except PartialTranslation as e:
            # готовые реплики применяются и при ошибке, и при отмене
            t.emit((e.result, e.done, "" if t.is_cancelled() else str(e)))
        return True

    def apply(msg):
        result, done_mask, err = msg
        by_id = {ln.id: ln for ln in p.lines}
        changed = 0
        for lid, src_text, new, ok_ in zip(ids, sources, result, done_mask):
            ln = by_id.get(lid)
            if ln is None or not ok_ or not new:
                continue
            if not ln.orig_text:
                ln.orig_text = src_text
                ln.orig_lang = src
            if ln.text != new:
                ln.text = new
                changed += 1
        state["applied"] = changed
        state["error"] = err
        win.refresh_all()
        win._on_table_selection()
        if changed:
            win.mark_dirty("Перевод реплик")
            win.save_project()
        left = len(ids) - sum(1 for m in done_mask if m)
        if err:
            QMessageBox.warning(
                win, "Перевод",
                f"Переведено реплик: {changed} из {len(ids)}.\n\n{err}\n\n"
                f"Осталось: {left}. Запустите перевод ещё раз — уже переведённые будут пропущены.")
        elif left:
            win.statusBar().showMessage(f"Перевод остановлен: готово {changed}, осталось {left}", 8000)
        else:
            win.statusBar().showMessage(
                f"Переведено реплик: {changed}. Оригинал сохранён; Ctrl+Z — отменить перевод.", 8000)

    win.run_task(Task(f"Перевод ({len(lines)} реплик)", work), None, apply)


def restore_original(win: "MainWindow") -> None:
    """Вернуть текст оригинала у выделенных переведённых реплик."""
    lines = [ln for ln in win.selected_lines() if ln.orig_text]
    if not lines:
        win.statusBar().showMessage("У выделенных реплик нет сохранённого оригинала", 5000)
        return
    for ln in lines:
        ln.text, ln.orig_text, ln.orig_lang = ln.orig_text, "", ""
    win.refresh_all()
    win._on_table_selection()
    win.mark_dirty("Возврат оригинала")
    win.statusBar().showMessage(f"Возвращён оригинал у реплик: {len(lines)}", 5000)


def run_ocr(win: "MainWindow") -> None:
    """Распознать вшитые субтитры в кадре и добавить их как реплики."""
    from ..core.ocr import extract_subtitles
    from .dialogs import OcrDialog

    p = win.project
    if not p or not p.video_path:
        return
    dlg = OcrDialog(win.settings, p.video_path, win.player.position(), p.duration,
                    bool(p.lines), win)
    if dlg.exec() != QDialog.Accepted:
        return
    settings = dlg.settings()
    t_from, t_to = dlg.time_range()
    replace = dlg.replace.isChecked()
    video = p.video_path

    def work(t: Task):
        return extract_subtitles(video, settings, None, t_from, t_to, t.report, t.is_cancelled)

    def done(subs):
        end = t_to if t_to is not None else float("inf")
        if replace:
            p.lines = [ln for ln in p.lines if ln.end <= t_from or ln.start >= end]
        for sl in subs:
            p.lines.append(Line(sl.start, sl.end, sl.text, None))
        p.sort_lines()
        # Язык найденного текста — источник для будущего перевода
        lang = {"zh": "zh-CN"}.get(settings.language, settings.language)
        win.settings.translate_source = lang
        win.settings.save()
        win.chars.reload()
        win.refresh_all()
        win.mark_dirty("Субтитры с видео")
        win.save_project()
        win.tabs.setCurrentIndex(0)
        if not subs:
            QMessageBox.information(
                win, "Субтитры с видео",
                "Текст в кадре не найден. Проверьте рамку области и вид субтитров "
                "(светлые/тёмные) и попробуйте снова на пробном отрезке.")
            return
        target = win.settings.translate_target
        if lang != target and QMessageBox.question(
                win, "Субтитры с видео",
                f"Найдено реплик: {len(subs)}.\n\nПеревести их сейчас на язык озвучки?"
        ) == QMessageBox.Yes:
            new_lines = [ln for ln in p.lines
                         if any(abs(ln.start - sl.start) < 1e-6 and ln.text == sl.text for sl in subs)]
            translate_lines(win, new_lines)
        else:
            win.statusBar().showMessage(f"Найдено реплик: {len(subs)}. Назначьте им роли.", 8000)

    win.run_task(Task("Субтитры с видео", work), done)
