"""Take the pictures for the help (Phase 4.7, D-135).

Opens each screen of the real window on synthetic files and saves it, or the
part of it a topic talks about, as a PNG in ``assets/help/images``. Run it
again whenever a screen changes; ``tests/test_help.py`` fails if a topic
shows a picture that isn't there or a picture no topic shows.

    python tools\\make_help_screenshots.py            # all pictures
    python tools\\make_help_screenshots.py drop saved  # only these
    python tools\\make_help_screenshots.py --list

Everything shown is synthetic: the generated test corpus, the example config
``tools/help_screenshots.config.json`` and fabricated identifiers
(99-2026-9999xx). No real case material, ever (project rule).

The window is drawn off screen (``QT_QPA_PLATFORM=offscreen``) at 1200 x 780,
the size it opens at on most screens, so the title bar isn't in the pictures.
Native Windows dialogs (Save As, the file picker) and Explorer can't be
captured this way; the help describes them in words. Run it on Windows for
the shipped pictures, so the fonts are drawn as users see them.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pymupdf  # noqa: E402
from PySide6.QtCore import QPoint, QRect, QSize  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QLabel, QLineEdit, QScrollArea, QWidget  # noqa: E402

from stamper.gui.app import make_app  # noqa: E402
from stamper.gui.main_window import MainWindow  # noqa: E402
from stamper.gui.services import Services  # noqa: E402
from stamper.gui.stamp_dialog import StampDialog  # noqa: E402
from tools import make_corpus  # noqa: E402

OUT = REPO / "assets" / "help" / "images"
CONFIG = REPO / "tools" / "help_screenshots.config.json"
TODAY = date(2026, 10, 18)
SIZE = QSize(1200, 780)
WAIT_S = 20.0
PAD = 10
FAKE = "99-2026-999901"  # fabricated; matches the example rule


# ------------------------------------------------------------------ plumbing


class Scripted:
    """Answers for the window's questions, so no native dialog ever opens."""

    def __init__(self) -> None:
        self.save_paths: list[Path] = []
        self.passwords: list[str] = []
        self.confirms: list[bool] = []
        self.folders: list[Path] = []

    def services(self) -> Services:
        return Services(
            ask_open_file=lambda _p, _s: None,
            ask_config_file=lambda _p: None,
            ask_save_path=lambda _p, _s: self.save_paths.pop(0) if self.save_paths else None,
            ask_password=lambda *_a: self.passwords.pop(0) if self.passwords else None,
            open_file=lambda _p: None,
            show_in_folder=lambda _p: None,
            confirm=lambda *_a: self.confirms.pop(0) if self.confirms else True,
            ask_import_file=lambda _p: None,
            ask_export_path=lambda _p, _s: None,
            ask_color=lambda _p, _c: None,
            ask_open_files=lambda _p, _s: None,
            ask_folder=lambda _p, _s: self.folders.pop(0) if self.folders else None,
        )


def pump(seconds: float = 0.05) -> None:
    """Let Qt and the engine thread run for a moment."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.01)


def wait(cond: Callable[[], bool], what: str) -> None:
    """Process events until ``cond`` is true."""
    end = time.monotonic() + WAIT_S
    while not cond():
        if time.monotonic() > end:
            raise TimeoutError(f"Timed out waiting for {what}.")
        QApplication.processEvents()
        time.sleep(0.01)
    pump(0.15)  # one more round, so the last repaint is in


def ready(window: MainWindow) -> StampDialog:
    wait(lambda: window.dialog is not None and window.dialog.ready(), "the stamp dialog")
    assert window.dialog is not None
    return window.dialog


def _scroller(w: QWidget) -> QScrollArea | None:
    p = w.parentWidget()
    while p is not None:
        if isinstance(p, QScrollArea):
            return p
        p = p.parentWidget()
    return None


def area(window: QWidget, widgets: Sequence[QWidget], pad: int = PAD) -> QPixmap:
    """The part of the window covering ``widgets``, with a margin.

    Inside a scrolling panel, the first widget is scrolled to the top and the
    picture stops at the panel's visible edge, so nothing outside it (the
    buttons under a panel) gets in.
    """
    scroller = _scroller(widgets[0])
    clip = window.rect()
    if scroller is not None:
        body = scroller.widget()
        top = min(w.mapTo(body, QPoint(0, 0)).y() for w in widgets)
        scroller.verticalScrollBar().setValue(max(0, top - pad))
        pump(0.15)
        view = scroller.viewport()
        clip = QRect(view.mapTo(window, QPoint(0, 0)), view.rect().size())
    rect = QRect()
    for w in widgets:
        top_left = w.mapTo(window, QPoint(0, 0))
        rect = rect.united(QRect(top_left, w.rect().size()))  # rect(): some widgets have a "size" attribute
    rect = rect.adjusted(-pad, -pad, pad, pad).intersected(clip)
    # Cut from a picture of the whole window: grab(rect) draws a little too large at 150%.
    whole = full(window)
    r = whole.devicePixelRatio()
    part = whole.copy(QRect(round(rect.x() * r), round(rect.y() * r), round(rect.width() * r), round(rect.height() * r)))
    part.setDevicePixelRatio(r)
    return part


def label(parent: QWidget, starts: str) -> QLabel:
    """The first label inside ``parent`` whose text starts with ``starts``."""
    for lab in parent.findChildren(QLabel):
        if lab.text().startswith(starts) and lab.isVisible():
            return lab
    raise LookupError(starts)


_PATHS: dict[str, str] = {}  # temp folders -> the Windows-looking paths shown instead


def neutral_paths() -> None:
    """Show tidy example paths instead of this run's temp folders in every label.

    The pictures would otherwise carry the machine's temp folder (and, on
    Windows, the user name in it).
    """
    for w in QApplication.allWidgets():
        if isinstance(w, QLabel):
            text = w.text()
            new = text
            for real, shown in _PATHS.items():
                while real in new:
                    i = new.index(real)
                    end = i + len(real)
                    while end < len(new) and (new[end].isalnum() or new[end] in "/\\._-"):
                        end += 1
                    new = new[:i] + shown + new[i + len(real):end].replace("/", "\\") + new[end:]
            if new != text:
                w.setText(new)


def full(window: QWidget) -> QPixmap:
    """The whole window, example paths shown."""
    neutral_paths()
    pump(0.05)
    return window.grab()


def save(pix: QPixmap, name: str) -> Path:
    """Write a picture as a compact PNG, with its scale recorded for the help window."""
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    image = pix.toImage().convertToFormat(QImage.Format.Format_RGB32)
    image.setText("DPR", f"{pix.devicePixelRatio():g}")
    if not image.save(str(path), "PNG", 0):
        raise OSError(f"Couldn't write {path}")
    return path


# ------------------------------------------------------------------ files


def signed_copy(src: Path, dst: Path) -> Path:
    """A synthetic file whose first form field is a filled signature field (detection only)."""
    with pymupdf.open(src) as d:
        w = next(iter(d[0].widgets()))
        sig = d.get_new_xref()
        d.update_object(sig, "<</Type/Sig/Filter/Adobe.PPKLite/SubFilter/adbe.pkcs7.detached"
                             "/Contents<00>/ByteRange[0 0 0 0]>>")
        d.xref_set_key(w.xref, "FT", "/Sig")
        d.xref_set_key(w.xref, "V", f"{sig} 0 R")
        d.xref_set_key(d.pdf_catalog(), "AcroForm/SigFlags", "3")
        d.save(dst)
    return dst


class Files:
    """Synthetic files with names that suit the pictures, in a temp folder."""

    def __init__(self, folder: Path) -> None:
        corpus = make_corpus.DEFAULT_OUT
        if not make_corpus.is_complete(corpus):
            make_corpus.generate(corpus)
        self.folder = folder
        self.out = folder / "stamped"
        self.out.mkdir()

        def copy(src: str, name: str) -> Path:
            dst = folder / name
            shutil.copyfile(corpus / src, dst)
            return dst

        self.single = copy("provider_return_99-2026-999901.pdf", f"{FAKE}_return.pdf")
        self.form = copy("acroform_filled.pdf", f"{FAKE}_form.pdf")
        self.part1 = copy("letter_portrait.pdf", f"{FAKE}_report_part1.pdf")
        self.part2 = copy("mixed_sizes.pdf", f"{FAKE}_report_part2.pdf")
        self.part3 = copy("letter_landscape.pdf", f"{FAKE}_attachment.pdf")
        self.locked = copy("encrypted_user_password.pdf", "locked_return.pdf")
        self.restricted = copy("owner_restrictions.pdf", "restricted_order.pdf")
        self.signed = signed_copy(corpus / "acroform_filled.pdf", folder / "signed_report.pdf")
        self.notes = folder / "notes.txt"
        self.notes.write_text("not a PDF\n", encoding="utf-8")


# ------------------------------------------------------------------ the pictures


class Shots:
    """One method per picture (or group of pictures from one screen)."""

    def __init__(self, files: Files) -> None:
        self.files = files
        self.answers = Scripted()
        self.window = MainWindow(services=self.answers.services(), today=TODAY)  # config.json next to the app
        self.window.setMinimumSize(QSize(100, 100))
        self.window.resize(SIZE)
        self.window.show()
        pump(0.3)

    def close(self) -> None:
        self.window.close()

    def reset(self, size: QSize = SIZE) -> None:
        self.window.show_drop()
        self.window.resize(size)
        pump(0.2)

    # -- drop screen

    def drop(self) -> None:
        self.reset()
        save(full(self.window), "drop")

    # -- single file

    def stamp(self) -> None:
        """The stamp dialog, its parts, and the saved screen."""
        w = self.window
        self.reset()
        w.open_file(self.files.single)
        d = ready(w)
        combo = d.editor("doc_type")
        combo.setCurrentIndex(combo.findData("Report"))  # type: ignore[attr-defined]
        d = ready(w)
        save(full(w), "stamp-dialog")
        rows = d._rows
        save(area(w, [rows["identifier"].label, rows["identifier"].message, rows["doc_type"].editor]),
             "stamp-fields")
        editor = d.editor("identifier")
        editor.setText("99-2026-99990")  # type: ignore[attr-defined]
        d._field_edited("identifier")
        d = ready(w)
        save(area(w, [rows["identifier"].label, rows["identifier"].editor, rows["identifier"].message]),
             "stamp-field-invalid")
        save(area(w, [d.status, d.save_button, d.help_button]), "stamp-blocked")
        editor.setText(FAKE)  # type: ignore[attr-defined]
        d._field_edited("identifier")
        ready(w)

        save(area(w, [label(d.form, "Placement"), d.direction_buttons["along-right"], d.page_numbers_combo,
                      label(d.form, "Keeps the stamp readable")]), "stamp-placement")
        save(area(w, [label(d.form, "File name"), d.name_label, *d._ticks.values()]), "stamp-filename")

        target = self.files.out / d.suggested_name()
        target.unlink(missing_ok=True)
        self.answers.save_paths.append(target)
        d.save_button.click()
        wait(lambda: w.stack.currentWidget() is w.saved and w.saved.thumbnail.pixmap() is not None
             and not w.saved.thumbnail.pixmap().isNull(), "the saved screen")
        save(full(w), "saved")

    def notes(self) -> None:
        """"Before you save" on a file with a form field under the stamp."""
        w = self.window
        self.reset()
        w.open_file(self.files.form)
        d = ready(w)
        combo = d.editor("doc_type")
        combo.setCurrentIndex(combo.findData("Report"))  # type: ignore[attr-defined]
        d = ready(w)
        d.scroll.verticalScrollBar().setValue(d.scroll.verticalScrollBar().maximum())
        pump(0.2)
        save(area(w, [d.notes_frame]), "stamp-before-you-save")

    def protected(self) -> None:
        """Signed and restricted notices, and the password prompt."""
        w = self.window
        self.reset()
        w.open_file(self.files.signed)
        d = ready(w)
        frame = d.signature_tick.parentWidget() if d.signature_tick else None
        if frame is not None:
            save(area(w, [frame], pad=6), "notice-signed")
        self.reset()
        w.open_file(self.files.restricted)
        d = ready(w)
        frame = d.restriction_tick.parentWidget() if d.restriction_tick else None
        if frame is not None:
            save(area(w, [frame], pad=6), "notice-restricted")
        self.reset()
        prompt = QInputDialog(w)
        prompt.setWindowTitle("Password needed")
        prompt.setLabelText(f"Enter the password for {self.files.locked.name}:")
        prompt.setTextEchoMode(QLineEdit.EchoMode.Password)
        prompt.setTextValue("password")
        prompt.show()
        pump(0.2)
        save(prompt.grab(), "password-prompt")
        prompt.close()

    # -- several files

    def several(self) -> None:
        """Files screen, Before stamping, combine dialog."""
        w = self.window
        self.reset()
        w.open_several([self.files.part1, self.files.part2, self.files.part3, self.files.notes])
        wait(lambda: w.stack.currentWidget() is w.files_screen, "the files screen")
        save(full(w), "files")

        self.reset()
        w.open_several([self.files.part1, self.files.locked, self.files.signed, self.files.restricted])
        wait(lambda: w.stack.currentWidget() is w.files_screen, "the files screen")
        w.choose_mode("combine")
        wait(lambda: w.prepare is not None, "the Before stamping screen")
        save(full(w), "before-stamping")

        self.reset()
        w.open_several([self.files.part1, self.files.part2, self.files.part3])
        wait(lambda: w.stack.currentWidget() is w.files_screen, "the files screen")
        w.choose_mode("combine")
        d = ready(w)
        combo = d.editor("doc_type")
        combo.setCurrentIndex(combo.findData("Report"))  # type: ignore[attr-defined]
        ready(w)
        save(full(w), "combine-dialog")

    def batch(self) -> None:
        """Batch dialog and its results, one copy skipped because it already exists."""
        w = self.window
        self.reset()
        w.open_several([self.files.part1, self.files.part2, self.files.part3])
        wait(lambda: w.stack.currentWidget() is w.files_screen, "the files screen")
        w.choose_mode("batch")
        d = ready(w)
        combo = d.editor("doc_type")
        combo.setCurrentIndex(combo.findData("Report"))  # type: ignore[attr-defined]
        d = ready(w)
        save(full(w), "batch-dialog")
        for f in self.files.out.glob("*"):
            f.unlink()
        d._set_folder(self.files.out)  # type: ignore[attr-defined]
        taken = self.files.out / d.name_for(self.files.part3)  # type: ignore[attr-defined]
        taken.write_bytes(b"%PDF-1.7\n% placeholder: an existing copy\n")
        self.answers.confirms.append(True)
        d.save_button.click()
        wait(lambda: w.stack.currentWidget() is w.batch_saved, "the batch results")
        save(full(w), "batch-results")

    # -- profile settings

    def settings(self) -> None:
        w = self.window
        self.reset()
        s = w.open_settings()
        assert s is not None
        wait(lambda: s.preview_ready(), "the settings preview")
        save(full(w), "settings-profiles")
        editor = s.profiles_page.stamp_editors[0]
        save(area(w, [editor], pad=4), "settings-stamp")
        panel = editor.color.panel
        panel.adjustSize()
        panel.ensurePolished()
        save(panel.grab(), "settings-color")

        s.show_section(1)
        page = s.fields_page
        page.select_key("identifier")
        pump(0.2)
        assert page.try_value is not None
        page.try_value.setText("99-2026-999901")
        if page.try_file is not None:
            page.try_file.setText(f"{FAKE}_return.pdf")
        page.refresh_live()
        pump(0.2)
        save(full(w), "settings-fields")
        rule = page.format_editors[0]
        save(area(w, [rule]), "settings-rule")
        page.help.setVisible(True)
        pump(0.2)
        save(area(w, [page.help]), "settings-rule-help")
        save(area(w, [label(page.detail, "Try it"), page.try_value, page.try_value_result]
                  + ([page.try_file, page.try_file_result] if page.try_file is not None else [])),
             "settings-try-it")

        s.show_section(2)
        pump(0.2)
        save(full(w), "settings-lists")
        s.show_section(3)
        pump(0.2)
        save(full(w), "settings-general")
        s.closed.emit()
        pump(0.2)

    # -- the help window itself

    def help(self) -> None:
        w = self.window
        self.reset()
        h = w.show_help("opening-files")
        h.resize(QSize(1100, 720))
        h.search.setText("signature")
        h.run_search()
        h.open_result(0)
        pump(0.4)
        save(full(h), "help-window")
        h.search.clear()
        h.run_search()
        h.close()


SHOTS: dict[str, tuple[str, ...]] = {
    "drop": ("drop",),
    "stamp": ("stamp-dialog", "stamp-fields", "stamp-placement", "stamp-filename",
              "stamp-field-invalid", "stamp-blocked", "saved"),
    "notes": ("stamp-before-you-save",),
    "protected": ("notice-signed", "notice-restricted", "password-prompt"),
    "several": ("files", "before-stamping", "combine-dialog"),
    "batch": ("batch-dialog", "batch-results"),
    "settings": ("settings-profiles", "settings-stamp", "settings-color", "settings-fields", "settings-rule",
                 "settings-rule-help", "settings-try-it", "settings-lists", "settings-general"),
    "help": ("help-window",),
}


def main(argv: list[str] | None = None) -> int:
    """Take the pictures named (groups from ``--list``), or all of them."""
    parser = argparse.ArgumentParser(description="Take the help pictures from the real window.")
    parser.add_argument("groups", nargs="*", help="which groups to take (default: all)")
    parser.add_argument("--list", action="store_true", help="list the groups and their pictures")
    parser.add_argument("--scale", type=float, default=1.5,
                        help="draw at this scale, like Windows display scaling (default 1.5, for 150%%)")
    args = parser.parse_args(argv)
    if args.list:
        for g, names in SHOTS.items():
            print(f"{g}: {', '.join(names)}")
        return 0
    groups = args.groups or list(SHOTS)
    unknown = [g for g in groups if g not in SHOTS]
    if unknown:
        parser.error(f"unknown group {unknown[0]}; see --list")

    # Keep the user's own config, state and work folders out of it, on Windows too.
    with tempfile.TemporaryDirectory(prefix="stamper-help-shots-", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp)
        for name in ("app", "user", "local"):
            (root / name).mkdir()
        os.environ["STAMPER_APP_DIR"] = str(root / "app")
        os.environ["STAMPER_USER_DIR"] = str(root / "user")
        os.environ["STAMPER_LOCAL_DIR"] = str(root / "local")
        shutil.copyfile(CONFIG, root / "app" / "config.json")
        work = root / "files"
        work.mkdir()
        for real, shown in ((root / "app", "C:\\DocumentStamper"), (work, "D:\\Records\\Incoming")):
            _PATHS[str(real)] = shown
            _PATHS[str(real.resolve())] = shown
        os.environ.setdefault("QT_SCALE_FACTOR", f"{args.scale:g}")
        make_app()
        shots = Shots(Files(work))
        try:
            for g in groups:
                getattr(shots, g)()
                print(f"{g}: {', '.join(SHOTS[g])}")
        finally:
            shots.close()
            pump(0.3)
    total = sum(p.stat().st_size for p in OUT.glob("*.png"))
    print(f"{len(list(OUT.glob('*.png')))} pictures in {OUT}, {total / 1024:.0f} KB in all.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
