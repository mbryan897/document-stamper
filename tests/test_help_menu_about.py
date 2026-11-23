"""The drop screen's Help menu, About, What's new and Copy details (step 13).

Synthetic configs and files only (99-2026-999901). Nothing here opens a
real dialog, Explorer or a web address: FakeServices records what was asked.
"""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QPlainTextEdit, QPushButton
from pytestqt.qtbot import QtBot

from stamper.core.locations import Locations, resource_dir
from stamper.gui import about as about_module
from stamper.gui.about import AboutDialog, TextDialog, copy_details
from stamper.gui.app import make_app
from stamper.gui.help import HelpBrowser
from stamper.gui.main_window import MainWindow
from tests.gui_support import TODAY, FakeServices, close_window, copy_to, fake, open_dialog, type_into  # noqa: F401
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _window(qtbot: QtBot, fake: FakeServices, locs: Locations, **kw: object) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs,
                   sendto_shortcut=locs.user / "SendTo" / "Document Stamper.lnk", **kw)  # type: ignore[arg-type]
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    return w


@pytest.fixture
def no_web(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every QDesktopServices.openUrl call, recorded instead of made."""
    opened: list[str] = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(QUrl(url).toString()) or True)
    return opened


# ------------------------------------------------------------------ the menu


def test_help_menu_entries(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    try:
        menu = w.drop.help_menu_button.menu()
        assert menu is not None and w.drop.help_menu_button.text() == "Help"
        names = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert names == ["Help\tF1", "Keyboard shortcuts", "What's new", "Open settings folder",
                         "About Document Stamper"]
        assert w.drop.setup_button.menu() is not None  # built the same way as Organization setup…

        w.drop.help_action.trigger()
        assert w.help is not None and w.help.topic == "opening-files"
        w.drop.keys_action.trigger()
        assert w.help.topic == "keyboard"

        w.drop.whats_new_action.trigger()
        assert isinstance(w.about, TextDialog) and w.about.windowTitle() == "What's new"
        assert isinstance(w.about.view, HelpBrowser)  # the help viewer, not a second copy
        shown = w.about.view.toPlainText()
        assert shown.startswith("What's new in Document Stamper") and about_module.program_version() in shown and "1.0.0" in shown

        w.drop.about_action.trigger()
        assert isinstance(w.about, AboutDialog)

        w.drop.folder_action.trigger()
        assert fake.shown == [locs.app_config]
    finally:
        close_window(w)


def test_f1_still_opens_the_drop_screens_help(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    w = _window(qtbot, fake, locs)
    try:
        assert w.help_for_screen().topic == "opening-files"
        assert not hasattr(w.drop, "help_button")  # the drop screen's link became the menu
    finally:
        close_window(w)


def test_open_settings_folder_is_off_for_the_builtin_default(qtbot: QtBot, fake: FakeServices,
                                                             locs: Locations) -> None:
    w = _window(qtbot, fake, locs)
    try:
        assert w.source is not None and w.source.kind.value == "builtin"
        assert not w.drop.folder_action.isEnabled()
        w.open_settings_folder()
        assert fake.shown == []
    finally:
        close_window(w)


def test_open_settings_folder_is_off_with_no_settings_file(qtbot: QtBot, fake: FakeServices,
                                                           locs: Locations) -> None:
    locs.app_config.write_text('{"schema_version": 1}', encoding="utf-8")
    w = _window(qtbot, fake, locs)
    try:
        assert not w.drop.folder_action.isEnabled()
    finally:
        close_window(w)


def test_open_settings_folder_follows_the_file_in_use(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                      tmp_path: Path) -> None:
    w = _window(qtbot, fake, locs)
    try:
        chosen = copy_to(RECORDS_CONFIG, tmp_path, "chosen.json")
        fake.config_files = [chosen]
        w.choose_config()
        assert w.drop.folder_action.isEnabled()
        w.drop.folder_action.trigger()
        assert fake.shown == [chosen]
    finally:
        close_window(w)


# ------------------------------------------------------------------ About


def test_about_shows_the_package_version_and_runtime_versions(qtbot: QtBot) -> None:
    import platform

    import pymupdf
    import PySide6
    from PySide6.QtCore import qVersion

    make_app()
    d = AboutDialog(None)
    qtbot.addWidget(d)
    assert d.title.text() == f"Document Stamper {importlib.metadata.version('document-stamper')}"
    versions = d.versions.text()
    for part in (platform.python_version(), PySide6.__version__, qVersion(), pymupdf.VersionBind,
                 pymupdf.VersionFitz):
        assert part in versions
    text = d.text.toPlainText()
    assert text.startswith("About Document Stamper")
    assert f"Version {importlib.metadata.version('document-stamper')}" in text
    assert "@VERSION@" not in text


def test_no_version_number_is_typed_into_the_about_code() -> None:
    source = (REPO / "stamper" / "gui" / "about.py").read_text(encoding="utf-8")
    pins = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    for pin in pins:
        assert pin.split("==")[1] not in source, pin
    assert '"1.0.0"' not in source


def test_about_text_comes_from_the_help_topic(qtbot: QtBot) -> None:
    make_app()
    d = AboutDialog(None)
    qtbot.addWidget(d)
    md = (resource_dir() / "assets" / "help" / "about.md").read_text(encoding="utf-8")
    for heading in ("License", "Source code", "Built with", "Fonts", "Privacy", "No warranty"):
        assert f"## {heading}" in md
        assert heading in d.text.toPlainText()


def test_source_address_matches_the_repo_and_is_selectable_text(qtbot: QtBot, no_web: list[str]) -> None:
    repo = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["urls"]["Repository"]
    assert repo == "https://github.com/mbryan897/document-stamper"
    md = (resource_dir() / "assets" / "help" / "about.md").read_text(encoding="utf-8")
    assert f"`{repo}`" in md and "MBryan897" not in md
    make_app()
    d = AboutDialog(None)
    qtbot.addWidget(d)
    assert repo in d.text.toPlainText()
    assert d.text.isReadOnly() and d.text.textInteractionFlags()  # selectable, not editable
    assert not d.text.openExternalLinks() and not d.text.openLinks()
    labels = [b.text() for b in d.findChildren(QPushButton)]
    assert labels == ["License…", "Third-party notices…", "Copy details", "Close"]  # none opens an address
    for b in d.findChildren(QPushButton):
        if b.text() != "Close":
            b.click()
    d.text.anchorClicked.emit(QUrl(repo))
    assert no_web == []


def test_license_and_notices_load_from_resources(qtbot: QtBot) -> None:
    make_app()
    d = AboutDialog(None)
    qtbot.addWidget(d)
    lic = d.show_license()
    box = lic.view
    assert isinstance(box, QPlainTextEdit) and box.isReadOnly()
    assert box.toPlainText() == (resource_dir() / "LICENSE").read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in box.toPlainText()
    notices = d.show_notices().view
    assert isinstance(notices, QPlainTextEdit) and notices.isReadOnly()
    assert notices.toPlainText() == (resource_dir() / "THIRD-PARTY-NOTICES").read_text(encoding="utf-8")


def test_bundled_texts_are_read_from_resource_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("LICENSE", "THIRD-PARTY-NOTICES", "CHANGELOG.md"):
        (tmp_path / name).write_text(f"planted {name}", encoding="utf-8")
    monkeypatch.setattr(about_module, "resource_dir", lambda: tmp_path)
    for name in ("LICENSE", "THIRD-PARTY-NOTICES", "CHANGELOG.md"):
        assert about_module.read_bundled(name) == f"planted {name}"
    (tmp_path / "LICENSE").unlink()
    assert "isn't with this copy" in about_module.read_bundled("LICENSE")


def test_notices_cover_everything_about_lists() -> None:
    notices = (REPO / "THIRD-PARTY-NOTICES").read_text(encoding="utf-8")
    for name in ("PyMuPDF", "MuPDF", "Qt for Python", "PySide6", "pydantic", "pydantic-core",
                 "Python", "Atkinson Hyperlegible Mono", "Atkinson Hyperlegible Next"):
        assert name in notices, name
    for licence in ("GNU Affero General Public License", "GNU Lesser General Public License", "MIT License",
                    "PSF License", "SIL Open Font License"):
        assert licence in notices, licence


def test_privacy_paragraph_names_what_the_code_writes() -> None:
    md = (REPO / "assets" / "help" / "about.md").read_text(encoding="utf-8")
    privacy = md.split("## Privacy", 1)[1].split("\n## ", 1)[0]
    for thing in ("never connects", "stamped copy", "~stamper-", "pending", r"DocumentStamper\work", "~stamper-combine-",
                  "config.json.bak", "state.json", "Send to", "stamper-sample-", "_MEI", "Export",
                  "where the temporary file is", r"QtProject\OrganizationDefaults\FileDialog", "Passwords are never written"):
        assert thing in privacy, thing


# ------------------------------------------------------------------ Copy details


PLANTED_DOC = "Planted-QQ-99-2026-999901-sealed.pdf"
PLANTED_FOLDER = "PlantedCaseFolderQQ"
PLANTED_PROFILE = "PlantedProfileQQ"
PLANTED_VALUE = "99-2026-999901"


def test_copy_details_says_only_what_it_should(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                               corpus_dir: Path, tmp_path: Path) -> None:
    data = json.loads(RECORDS_CONFIG.read_text(encoding="utf-8"))
    data["profiles"][0]["name"] = PLANTED_PROFILE
    locs.app_config.write_text(json.dumps(data), encoding="utf-8")
    docs = tmp_path / PLANTED_FOLDER
    docs.mkdir()
    src = copy_to(corpus_dir / "letter_portrait.pdf", docs, PLANTED_DOC)
    w = _window(qtbot, fake, locs)
    try:
        d = open_dialog(qtbot, w, src)
        type_into(qtbot, d, "identifier", PLANTED_VALUE)
        about = w.show_about()
        text = about.copy()
        assert QGuiApplication.clipboard().text() == text
        for planted in (PLANTED_DOC, PLANTED_FOLDER, PLANTED_PROFILE, PLANTED_VALUE, "Case number",
                        "identifier", str(locs.state_file)):
            assert planted not in text, planted
        lines = text.splitlines()
        assert lines[0] == f"Document Stamper {importlib.metadata.version('document-stamper')}"
        assert [line.split(" ")[0] for line in lines[1:4]] == ["Python", "PySide6", "PyMuPDF"]
        assert lines[4].startswith("System: ")
        assert lines[5] == "Running: from source"
        assert lines[6:] == ["Settings file: next to the app", f"Settings file path: {locs.app_config}",
                             "Read-only: no"]
    finally:
        close_window(w)


def test_copy_details_for_the_builtin_and_for_none() -> None:
    from stamper.core.locations import SourceKind, describe

    text = copy_details(describe(DEFAULT_CONFIG, SourceKind.BUILTIN))
    assert "Settings file: the built-in default" in text and "Read-only: yes" in text
    assert copy_details(None).endswith("Settings file: none loaded\n")


def test_copy_details_shows_hidden_characters_in_the_path(tmp_path: Path) -> None:
    from stamper.core.locations import SourceKind, describe

    odd = tmp_path / "rec\u202eords.json"
    shutil.copyfile(RECORDS_CONFIG, odd)
    text = copy_details(describe(odd, SourceKind.OTHER))
    assert "\u202e" not in text and "⟨U+202E⟩" in text
    assert "Settings file: opened for this session" in text


def test_copy_details_never_reads_the_computer_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """platform.uname() reads the host name on Windows (socket.gethostname); Copy details must not use it."""
    import platform
    import types

    def refuse(*_a: object) -> None:
        raise AssertionError("the computer's name was read")

    monkeypatch.setattr(platform, "uname", refuse)
    monkeypatch.setattr(platform, "node", refuse)
    monkeypatch.setattr(sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(major=10, minor=0, build=26100), raising=False)
    assert "System: Windows 11 (version 10.0.26100)" in copy_details(None)
    monkeypatch.setattr(sys, "getwindowsversion",
                        lambda: types.SimpleNamespace(major=10, minor=0, build=19045), raising=False)
    assert "System: Windows 10 (version 10.0.19045)" in copy_details(None)


# ------------------------------------------------------------------ no network


_PROBE = r"""
import sys

# Anything that tries to reach a network goes through these audit events, whoever asks.
events = []
NET_EVENTS = ("socket.", "urllib.", "webbrowser.", "http.", "ftplib.", "smtplib.", "imaplib.", "poplib.")
sys.addaudithook(lambda name, _args: events.append(name) if name.startswith(NET_EVENTS) else None)

# Who imports the socket module: allowed only from the standard library (see the test).
importers = []


class Watch:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name == "socket":
            f = sys._getframe(1)
            while f is not None and str(f.f_globals.get("__name__", "")).startswith(("importlib", "_frozen")):
                f = f.f_back
            importers.append(str(f.f_globals.get("__name__", "?")) if f is not None else "?")
        return None


sys.meta_path.insert(0, Watch)

from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
app = make_app()
w = MainWindow()
w.show()
app.processEvents()
a = w.show_about(); a.copy(); a.show_license(); a.show_notices()
w.show_whats_new(); w.show_help("about")
app.processEvents()
w.close()
for m in sorted(sys.modules):
    print("MOD", m)
for m in importers:
    print("SOCKET_IMPORTED_BY", m)
for e in events:
    print("NET_EVENT", e)
"""

# Modules that exist to talk to a network. ``socket`` itself is handled apart: standard-library
# modules import it without connecting (on Windows one does at start-up), so it may be loaded only
# by the standard library, and the audit hook above proves nothing ever opens a socket.
NETWORK = ("ssl", "http", "urllib.request", "urllib3", "requests", "httpx", "ftplib",
           "smtplib", "imaplib", "poplib", "xmlrpc", "webbrowser", "asyncio", "PySide6.QtNetwork",
           "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets")


def _lines(out: str, tag: str) -> list[str]:
    return [line.split(" ", 1)[1] for line in out.splitlines() if line.startswith(tag + " ")]


def test_starting_the_app_loads_no_network_module_and_opens_no_socket(tmp_path: Path) -> None:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    run = subprocess.run([sys.executable, "-c", _PROBE], cwd=REPO, env=env, capture_output=True,
                         text=True, timeout=180, check=False)
    assert run.returncode == 0, run.stderr
    loaded = set(_lines(run.stdout, "MOD"))
    assert "stamper.gui.about" in loaded and "PySide6.QtWidgets" in loaded  # the probe really ran
    found = sorted(m for m in loaded if any(m == n or m.startswith(n + ".") for n in NETWORK))
    assert found == []
    assert _lines(run.stdout, "NET_EVENT") == []  # no socket made, no address looked up, no page opened
    for importer in _lines(run.stdout, "SOCKET_IMPORTED_BY"):
        top = importer.split(".")[0]
        assert top in sys.stdlib_module_names and top not in {n.split(".")[0] for n in NETWORK}, (
            f"socket imported by {importer}")


def test_the_probe_would_see_a_connection_attempt() -> None:
    """The audit hook really catches a socket being made (so an empty list means something)."""
    probe = "import sys\nev=[]\nsys.addaudithook(lambda n,a: ev.append(n) if n.startswith('socket.') else None)\n" \
            "import socket\ns=socket.socket(); s.close()\nprint(ev)"
    run = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, timeout=60, check=False)
    assert "socket.__new__" in run.stdout, run.stdout + run.stderr


def test_no_stamper_code_imports_a_network_module() -> None:
    found: list[str] = []
    for path in (REPO / "stamper").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            found += [f"{path.name}: {n}" for n in names
                      if any(n == m or n.startswith(m + ".") for m in (*NETWORK, "socket"))]
    assert found == []
