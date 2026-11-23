"""The settings switch that turns folder memory off (F-03).

``settings.remember_folders`` defaults to true; a config without it remembers
folders as before. Off: state.json keeps no folder, folders already in it go,
unknown keys and the last profile stay, and the dialogs start where they did
before folders were remembered. The key is written only when it's off, so a
file left at the default still loads in versions that don't know it.

Synthetic configs and files only; a distinctive made-up folder name is planted
and searched for.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.core.config import ConfigError, load_config, parse_config
from stamper.core.locations import Locations, load_state
from stamper.gui.app import make_app
from stamper.gui.config_draft import diff_summary
from stamper.gui.main_window import MainWindow
from stamper.gui.multi import BatchDialog, CombineDialog
from tests.gui_support import (  # noqa: F401 - fixtures
    TODAY,
    WAIT_MS,
    FakeServices,
    choose,
    close_window,
    copy_to,
    default_window,
    fake,
    open_dialog,
    type_into,
    wait_ready,
)
from tests.stamping import DEFAULT_CONFIG
from tests.test_gui_multi import open_several, wait_dialog

PLANT = "Zq-Planted-Folder-4417"
CASE = "99-2026-999901"
FOLDER_KEYS = {"last_folder", "last_save_folder", "batch_folder", "batch_in_one_folder"}


def _config(tmp_path: Path, value: object) -> Path:
    data = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    data["settings"]["remember_folders"] = value
    path = tmp_path / "off.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _state(w: MainWindow) -> str:
    return w.locs.state_file.read_text(encoding="utf-8")


def _fill(qtbot: QtBot, d) -> None:  # noqa: ANN001
    choose(d, "doc_type", "Report")
    type_into(qtbot, d, "identifier", CASE)
    wait_ready(qtbot, d)
    assert d.save_button.isEnabled(), d.blockers()


def _seed_state(**values: object) -> Path:
    """state.json as an earlier run left it, before any window starts."""
    from stamper.core.locations import locations

    locs = locations()
    locs.user.mkdir(parents=True, exist_ok=True)
    locs.state_file.write_text(json.dumps(values), encoding="utf-8")
    return locs.state_file


@pytest.fixture
def off_window(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> Iterator[MainWindow]:
    make_app()
    w = MainWindow(config_path=_config(tmp_path, False), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.resize(1200, 780)
    w.show()
    yield w
    close_window(w)


# ------------------------------------------------------------ the config key


def test_without_the_key_folders_are_remembered() -> None:
    config = load_config(DEFAULT_CONFIG)
    assert "remember_folders" not in DEFAULT_CONFIG.read_text(encoding="utf-8")
    assert config.settings.remember_folders is True


def test_key_is_written_only_when_off(tmp_path: Path) -> None:
    on = load_config(_config(tmp_path, True))
    assert "remember_folders" not in on.to_json()  # loads in versions that refuse unknown keys
    off = load_config(_config(tmp_path, False))
    assert json.loads(off.to_json())["settings"]["remember_folders"] is False
    assert parse_config(json.loads(off.to_json())).settings.remember_folders is False


@pytest.mark.parametrize("value", ["sometimes", 2, [True], None])
def test_an_invalid_value_is_reported_with_its_location(tmp_path: Path, value: object) -> None:
    with pytest.raises(ConfigError) as e:
        load_config(_config(tmp_path, value))
    assert len(e.value.problems) == 1
    assert e.value.problems[0].startswith("settings.remember_folders: ")
    assert "unknown key" not in e.value.problems[0]  # a known key with a bad value


# ------------------------------------------------------------ on (the default): as before


def test_default_config_remembers_the_folder(qtbot: QtBot, default_window: MainWindow, corpus_dir: Path,
                                             tmp_path: Path) -> None:
    src = copy_to(corpus_dir / "letter_portrait.pdf", tmp_path)
    open_dialog(qtbot, default_window, src)
    assert json.loads(_state(default_window))["last_folder"] == str(tmp_path)
    assert default_window._last_folder == tmp_path


# ------------------------------------------------------------ off


def test_off_keeps_no_folder_after_open_save_combine_and_batch(
        qtbot: QtBot, fake: FakeServices, corpus_dir: Path, tmp_path: Path) -> None:
    _seed_state(last_profile="Standard", future=[1])
    make_app()
    w = MainWindow(config_path=_config(tmp_path, False), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.show()
    try:
        src_dir = tmp_path / PLANT / "in"
        src_dir.mkdir(parents=True)

        def check() -> None:
            text = _state(w)
            assert PLANT not in text and str(tmp_path) not in text
            data = json.loads(text)
            assert data["last_profile"] == "Standard" and data["future"] == [1]
            assert not FOLDER_KEYS & set(data)

        # Open and save one file.
        src = copy_to(corpus_dir / "letter_portrait.pdf", src_dir)
        d = open_dialog(qtbot, w, src)
        check()
        _fill(qtbot, d)
        fake.save_paths.append(tmp_path / PLANT / "single.pdf")
        d.save_button.click()
        qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)
        assert (tmp_path / PLANT / "single.pdf").is_file()
        check()

        # Combine and save.
        w.show_drop()
        files = [copy_to(corpus_dir / n, src_dir, f"c_{n}") for n in ("letter_portrait.pdf", "rotate_90.pdf")]
        open_several(qtbot, w, files)
        check()
        w.files_screen.combine_button.click()
        c = wait_dialog(qtbot, w, CombineDialog)
        _fill(qtbot, c)
        fake.save_paths.append(tmp_path / PLANT / "combined.pdf")
        c.save_button.click()
        qtbot.waitUntil(lambda: w.stack.currentWidget() is w.saved, timeout=WAIT_MS)
        assert (tmp_path / PLANT / "combined.pdf").is_file()
        check()

        # A batch saved "In one folder".
        out = tmp_path / PLANT / "batch out"
        out.mkdir()
        w.show_drop()
        open_several(qtbot, w, files)
        w.files_screen.batch_button.click()
        b = wait_dialog(qtbot, w, BatchDialog)
        assert isinstance(b, BatchDialog)
        fake.folders.append(out)
        b.folder_radio.click()
        assert b.out_folder == out
        _fill(qtbot, b)
        b.save_button.click()
        qtbot.waitUntil(lambda: w.stack.currentWidget() is w.batch_saved, timeout=WAIT_MS)
        assert len(list(out.iterdir())) == 2
        check()
    finally:
        close_window(w)


def test_off_removes_folders_already_there_and_keeps_unknown_keys(
        qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    kept = tmp_path / PLANT
    kept.mkdir()
    state_file = _seed_state(last_profile="Standard", last_folder=str(kept), last_save_folder=str(kept),
                             batch_folder=str(kept), batch_in_one_folder=True, future={"x": 1})
    make_app()
    w = MainWindow(config_path=_config(tmp_path, False), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    try:
        text = state_file.read_text(encoding="utf-8")
        assert PLANT not in text
        data = json.loads(text)
        assert not FOLDER_KEYS & set(data)
        assert data["future"] == {"x": 1} and data["last_profile"] == "Standard"
        assert load_state(w.locs).extra == {"future": {"x": 1}}
    finally:
        close_window(w)


def test_off_dialogs_ignore_remembered_folders(qtbot: QtBot, fake: FakeServices, corpus_dir: Path,
                                              tmp_path: Path) -> None:
    kept = tmp_path / PLANT
    kept.mkdir()
    _seed_state(last_folder=str(kept), last_save_folder=str(kept), batch_folder=str(kept),
                batch_in_one_folder=True)
    src_dir = tmp_path / "originals"
    src_dir.mkdir()
    make_app()
    w = MainWindow(config_path=_config(tmp_path, False), services=fake.services(), today=TODAY)
    qtbot.addWidget(w)
    w.show()
    try:
        assert w._last_folder is None  # Open starts at the system default
        src = copy_to(corpus_dir / "letter_portrait.pdf", src_dir)
        d = open_dialog(qtbot, w, src)
        _fill(qtbot, d)
        d.save_button.click()  # answered with no path: only the starting folder is checked
        assert fake.save_suggestions[-1].parent == src_dir  # the original's folder

        w.show_drop()
        files = [copy_to(corpus_dir / n, src_dir, f"c_{n}") for n in ("letter_portrait.pdf", "rotate_90.pdf")]
        open_several(qtbot, w, files)
        w.files_screen.batch_button.click()
        b = wait_dialog(qtbot, w, BatchDialog)
        assert isinstance(b, BatchDialog)
        assert b.next_to_radio.isChecked() and b.out_folder is None and b.folder_label.text() == ""
    finally:
        close_window(w)


# ------------------------------------------------------------ Profile settings › General


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def test_tick_box_round_trips_through_save_changes(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    locs.app_config.write_text(DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs,
                   sendto_shortcut=locs.user / "SendTo" / "Document Stamper.lnk")
    qtbot.addWidget(w)
    w.show()
    try:
        s = w.open_settings()
        assert s is not None
        s.show_section(3)
        box = s.general_page.remember_folders
        assert box.text() == "Remember the folders files are opened from and saved to"
        assert box.isChecked()
        box.click()
        fake.confirms.append(True)
        s.save()
        assert "Remember folders turned off" in fake.questions[-1][1]
        assert load_config(locs.app_config).settings.remember_folders is False
        assert w.state.remember_folders is False  # the window follows the saved file

        s = w.open_settings()
        assert s is not None
        s.show_section(3)
        box = s.general_page.remember_folders
        assert not box.isChecked()
        box.click()
        fake.confirms.append(True)
        s.save()
        assert "Remember folders turned on" in fake.questions[-1][1]
        assert "remember_folders" not in locs.app_config.read_text(encoding="utf-8")
        assert w.state.remember_folders is True
    finally:
        close_window(w)


def test_summary_line_only_when_it_changes() -> None:
    base = json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    off = json.loads(json.dumps(base))
    off["settings"]["remember_folders"] = False
    assert diff_summary(base, off) == ["Remember folders turned off"]
    assert diff_summary(off, base) == ["Remember folders turned on"]
    explicit = json.loads(json.dumps(base))
    explicit["settings"]["remember_folders"] = True
    assert "Remember folders" not in " ".join(diff_summary(base, explicit))
