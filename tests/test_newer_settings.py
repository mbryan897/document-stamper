"""A settings file from a newer version of the app (step 13).

A whole-number ``schema_version`` above ours gives one sentence instead of
the list of validation problems, wherever a settings file is loaded: at
start-up, "Use a different settings file…", Restore a previous version and
import. An older or non-numeric version keeps today's message. Synthetic
configs only.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.core import config as config_module
from stamper.core.config import SCHEMA_VERSION, ConfigError, load_config
from stamper.core.locations import Locations
from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
from tests.gui_support import TODAY, FakeServices, close_window, fake  # noqa: F401
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

SENTENCE = ("This settings file was made by a newer version of Document Stamper. "
            "Update the program, or use a different settings file.")


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _versioned(path: Path, version: object) -> Path:
    """A copy of the Records fixture with another schema_version (and a later key it doesn't know)."""
    data = json.loads(RECORDS_CONFIG.read_text(encoding="utf-8"))
    data["schema_version"] = version
    data["something_new"] = {"added": "in a later version"}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _window(qtbot: QtBot, fake: FakeServices, locs: Locations) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs,
                   sendto_shortcut=locs.user / "SendTo" / "Document Stamper.lnk")
    qtbot.addWidget(w)
    w.show()
    return w


def test_the_sentence_is_the_agreed_wording() -> None:
    assert getattr(config_module, "NEWER_VERSION", None) == SENTENCE


@pytest.mark.parametrize("version", [SCHEMA_VERSION + 1, 99])
def test_loader_says_newer_in_one_sentence(tmp_path: Path, version: int) -> None:
    with pytest.raises(ConfigError) as e:
        load_config(_versioned(tmp_path / "config.json", version))
    assert getattr(e.value, "newer", False)
    assert e.value.problems == [SENTENCE]


@pytest.mark.parametrize("version", [0, -3, "x", "2", 1.5, None])
def test_older_or_odd_versions_keep_todays_message(tmp_path: Path, version: object) -> None:
    with pytest.raises(ConfigError) as e:
        load_config(_versioned(tmp_path / "config.json", version))
    assert not getattr(e.value, "newer", False)
    assert SENTENCE not in e.value.problems
    assert any(p.startswith("schema_version") for p in e.value.problems)


def test_start_up_shows_the_sentence(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    _versioned(locs.app_config, 2)
    w = _window(qtbot, fake, locs)
    try:
        assert w.drop.message.text() == SENTENCE
        assert "•" not in w.drop.message.text()
        assert w.drop.config_label.text().startswith("No settings file loaded.")
    finally:
        close_window(w)


@pytest.mark.parametrize("version", [0, "x"])
def test_start_up_keeps_the_list_for_older_or_odd(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                  version: object) -> None:
    _versioned(locs.app_config, version)
    w = _window(qtbot, fake, locs)
    try:
        text = w.drop.message.text()
        assert text.startswith("config.json can't be used:\n• ")
        assert "schema_version" in text and SENTENCE not in text
    finally:
        close_window(w)


def test_use_a_different_settings_file_shows_the_sentence(qtbot: QtBot, fake: FakeServices,
                                                          locs: Locations, tmp_path: Path) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    try:
        fake.config_files = [_versioned(tmp_path / "newer.json", 2)]
        w.choose_config()
        assert w.drop.message.text() == SENTENCE
        assert w.config_path == locs.app_config  # the working file stays in use
    finally:
        close_window(w)


def test_import_and_restore_show_the_sentence(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                              tmp_path: Path) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    try:
        s = w.open_settings()
        assert s is not None
        fake.import_files = [_versioned(tmp_path / "newer.json", 2)]
        s.import_config()
        assert s.status.text() == SENTENCE
        fake.import_files = [_versioned(locs.app / "config.json.bak", 7)]
        s.restore_backup()
        assert s.status.text() == SENTENCE
        assert fake.questions == [] and not s.draft.dirty
        fake.import_files = [_versioned(tmp_path / "older.json", 0)]
        s.import_config()
        assert s.status.text().startswith("older.json can't be imported:\n• schema_version")
    finally:
        close_window(w)
