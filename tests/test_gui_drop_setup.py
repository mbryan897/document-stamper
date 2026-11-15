"""The drop screen's "Organization setup…" menu (UX-009) and Send to link (UX-011).

Synthetic configs only. Every Send to shortcut path points into a temp
folder; ``sendto.create`` is replaced, so no real shortcut is written.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from stamper import sendto
from stamper.core.filenames import visible
from stamper.core.locations import AppState, Locations, SourceKind, save_state
from stamper.gui.app import make_app
from stamper.gui.main_window import MINIMUM_SIZE, SESSION_SHOWN, MainWindow
from stamper.gui.profiles import ProfileSettings
from tests.gui_support import TODAY, FakeServices, close_window, fake  # noqa: F401
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _shortcut(locs: Locations) -> Path:
    return locs.user / "SendTo" / sendto.SHORTCUT_NAME


def _window(qtbot: QtBot, fake: FakeServices, locs: Locations) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs, sendto_shortcut=_shortcut(locs))
    qtbot.addWidget(w)
    w.resize(1280, 860)
    w.show()
    return w


@pytest.fixture
def records_window(qtbot: QtBot, fake: FakeServices, locs: Locations) -> Iterator[MainWindow]:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    yield w
    close_window(w)


# ------------------------------------------------------------ Organization setup… (UX-009)


def test_one_setup_link_replaces_the_two(records_window: MainWindow) -> None:
    d = records_window.drop
    assert d.setup_button.text() == "Organization setup…" and d.setup_button.objectName() == "link"
    assert [a.text() for a in d.setup_button.menu().actions()] == [
        "Edit profiles and fields…", "Use a different settings file…"]
    texts = [b.text() for b in d.findChildren(QPushButton)]
    assert "Profile settings…" not in texts and "Open another config…" not in texts


def test_edit_entry_opens_profile_settings(records_window: MainWindow) -> None:
    records_window.drop.settings_action.trigger()
    assert isinstance(records_window.stack.currentWidget(), ProfileSettings)


def test_settings_file_entry_loads_a_file_for_this_session(records_window: MainWindow, fake: FakeServices,
                                                          tmp_path: Path) -> None:
    other = tmp_path / "other.json"
    shutil.copyfile(DEFAULT_CONFIG, other)
    fake.config_files = [other]
    records_window.drop.file_action.trigger()
    assert records_window.config_path == other
    assert records_window.source is not None and records_window.source.kind is SourceKind.OTHER


def _drop_text(w: MainWindow) -> list[str]:
    """Every string the drop screen can show, hidden ones included."""
    d = w.drop
    out = [x.text() for kind in (QLabel, QPushButton) for x in d.findChildren(kind)]
    out += [a.text() for a in d.setup_button.menu().actions()]
    return out


def _without(texts: list[str], *paths: Path) -> str:
    """The texts with the file names and paths taken out (they're the user's, not ours)."""
    joined = "\n".join(texts)
    for p in paths:
        joined = joined.replace(visible(str(p)), "<path>").replace(p.name, "<name>")
    return joined


def test_no_drop_screen_wording_says_config(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                           corpus_dir: Path) -> None:
    before = locs.user / "earlier.json"
    save_state(AppState(last_config=str(before)), locs)
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    try:
        assert "different settings file from last time" in w.drop.message.text()
        assert w.drop.config_label.text().startswith("Settings file: ")
        assert "config" not in _without(_drop_text(w), locs.app_config, before).lower()
    finally:
        close_window(w)

    locs.app_config.write_text('{"schema_version": 1}', encoding="utf-8")
    w = _window(qtbot, fake, locs)
    try:
        assert w.drop.config_label.text().startswith("No settings file loaded.")
        assert not w.drop.settings_action.isEnabled()
        shown = _drop_text(w)
        w.open_file(corpus_dir / "letter_portrait.pdf")
        shown.append(w.drop.message.text())
        w.open_several([corpus_dir / "letter_portrait.pdf", corpus_dir / "letter_portrait.pdf"])
        shown.append(w.drop.message.text())
        assert "settings file that works" in shown[-1]
        assert "config" not in _without(shown, locs.app_config).lower()
    finally:
        close_window(w)


def test_settings_file_path_shows_hidden_characters(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    odd = tmp_path / "rec‮ords.json"
    shutil.copyfile(RECORDS_CONFIG, odd)
    make_app()
    w = MainWindow(config_path=odd, services=fake.services(), today=TODAY,
                   sendto_shortcut=tmp_path / "SendTo" / sendto.SHORTCUT_NAME)
    qtbot.addWidget(w)
    try:
        assert "⟨U+202E⟩" in w.drop.config_label.text() and "‮" not in w.drop.config_label.text()
    finally:
        close_window(w)


# ------------------------------------------------------------ the Send to link (UX-011)


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MainWindow, "sendto_available", lambda _self: True)


def _status(monkeypatch: pytest.MonkeyPatch, state: sendto.SendToState) -> None:
    monkeypatch.setattr(sendto, "status", lambda lnk=None, want=None: sendto.SendToStatus(state, lnk))


@pytest.mark.usefixtures("on_windows")
def test_link_shows_while_the_shortcut_is_missing_and_adds_it(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Path | None] = []

    def create(lnk: Path | None = None, what: object = None) -> Path:
        made.append(lnk)
        return lnk  # type: ignore[return-value]

    monkeypatch.setattr(sendto, "create", create)
    w = _window(qtbot, fake, locs)
    try:
        link = w.drop.sendto_link
        assert not link.isHidden() and link.text() == "Add Document Stamper to Explorer's Send to menu"
        link.click()
        assert made == [_shortcut(locs)]  # the window's shortcut path, never the real Send to folder
        assert link.isHidden()
        assert w.drop.message.text() == "Added to the Send to menu."
    finally:
        close_window(w)


@pytest.mark.usefixtures("on_windows")
def test_link_stays_with_the_reason_when_adding_fails(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    why = "Windows couldn't save the shortcut x‮.lnk (error 0x80070005)."

    def refuse(lnk: Path | None = None, what: object = None) -> Path:
        raise sendto.SendToError(why)

    monkeypatch.setattr(sendto, "create", refuse)
    w = _window(qtbot, fake, locs)
    try:
        w.drop.sendto_link.click()
        assert not w.drop.sendto_link.isHidden()
        assert w.drop.message.text() == visible(why)
        assert w.drop.message_frame.objectName() == "warning"
    finally:
        close_window(w)


@pytest.mark.usefixtures("on_windows")
@pytest.mark.parametrize(("state", "offered"), [
    (sendto.SendToState.MISSING, True),
    (sendto.SendToState.ELSEWHERE, True),
    (sendto.SendToState.CURRENT, False),
])
def test_link_follows_the_shortcut(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                   monkeypatch: pytest.MonkeyPatch, state: sendto.SendToState,
                                   offered: bool) -> None:
    _status(monkeypatch, state)
    w = _window(qtbot, fake, locs)
    try:
        assert w.drop.sendto_link.isHidden() is not offered
    finally:
        close_window(w)


def test_no_link_off_windows(qtbot: QtBot, fake: FakeServices, locs: Locations,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MainWindow, "sendto_available", lambda _self: False)
    _status(monkeypatch, sendto.SendToState.MISSING)
    w = _window(qtbot, fake, locs)
    try:
        assert w.drop.sendto_link.isHidden()
    finally:
        close_window(w)


@pytest.mark.usefixtures("on_windows")
def test_drop_screen_with_the_link_fits_at_minimum(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _status(monkeypatch, sendto.SendToState.MISSING)
    w = _window(qtbot, fake, locs)
    try:
        w.resize(MINIMUM_SIZE)
        qtbot.waitUntil(lambda: w.size() == MINIMUM_SIZE)
        for b in (w.drop.choose_button, w.drop.setup_button, w.drop.sendto_link):
            top = b.mapTo(w, QPoint(0, 0)).y()
            assert b.isVisible() and top >= 0 and top + b.height() <= w.height()
        widget: QWidget = w.drop
        assert widget.height() >= widget.minimumSizeHint().height()
    finally:
        close_window(w)


@pytest.mark.usefixtures("on_windows")
def test_drop_screen_with_everything_showing_fits_at_minimum(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                             monkeypatch: pytest.MonkeyPatch,
                                                             tmp_path: Path) -> None:
    """The Send to link, the partial-copy notice and a full session list at once (UX-035, D-093)."""
    _status(monkeypatch, sendto.SendToState.MISSING)
    w = _window(qtbot, fake, locs)
    try:
        for i in range(SESSION_SHOWN):
            w._add_to_session(f"99-2026-99990{i}_return_with_a_long_synthetic_name.pdf", tmp_path / f"{i}.pdf")
        w.show_drop()
        w.drop.show_leftovers("A partial copy was left behind when a save stopped: synthetic.pdf", 1)
        w.resize(MINIMUM_SIZE)
        qtbot.waitUntil(lambda: w.size() == MINIMUM_SIZE)
        assert len(w.drop.session_buttons) == SESSION_SHOWN
        d = w.drop
        for b in (d.choose_button, d.setup_button, d.sendto_link, d.leftover_delete, d.leftover_keep,
                  d.session_buttons[0]):
            top = b.mapTo(w, QPoint(0, 0)).y()
            assert b.isVisible() and top >= 0 and top + b.height() <= w.height(), b.text()
        widget: QWidget = w.drop
        assert widget.height() >= widget.minimumSizeHint().height()
    finally:
        close_window(w)


def test_muted_link_text_reaches_4_5_to_1() -> None:
    from stamper.gui import theme
    from tests.test_gui_keyboard import _contrast, _rule

    color = theme.COLORS["muted"]
    assert f'color: {color}' in _rule("QPushButton#mutedLink")
    assert _contrast(color, theme.COLORS["ground"]) >= 4.5
