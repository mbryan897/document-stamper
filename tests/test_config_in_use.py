"""Which config is in use (D-121): change since last start-up noted, size limit, no overrides when packaged."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper.core import locations as loc
from stamper.core.config import MAX_CONFIG_BYTES, ConfigError, load_config
from stamper.core.locations import AppState, Locations, load_state, save_state
from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
from tests.gui_support import FakeServices, fake  # noqa: F401 - fixtures
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _start(qtbot: QtBot, fake: FakeServices, locs: Locations, **kw: object) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), locs=locs, **kw)  # type: ignore[arg-type]
    qtbot.addWidget(w)
    return w


def _note(w: MainWindow) -> str:
    return w.drop.message.text() if not w.drop.message_frame.isHidden() else ""


def test_a_different_config_from_last_time_is_noted(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    first = _start(qtbot, fake, locs)
    assert _note(first) == ""
    assert load_state(locs).last_config == str(DEFAULT_CONFIG)

    shutil.copyfile(RECORDS_CONFIG, locs.app_config)  # someone puts a config next to the app
    second = _start(qtbot, fake, locs)
    note = _note(second)
    assert note.startswith("This is a different settings file from last time.")
    assert str(locs.app_config) in note and "next to the app" in note and str(DEFAULT_CONFIG) in note
    assert second.drop.message_frame.objectName() == "warning"

    third = _start(qtbot, fake, locs)
    assert _note(third) == ""  # same as last time: nothing to say


def test_a_session_config_is_not_remembered(qtbot: QtBot, fake: FakeServices, locs: Locations) -> None:
    _start(qtbot, fake, locs)
    w = _start(qtbot, fake, locs, config_path=RECORDS_CONFIG)
    assert _note(w) == ""
    assert load_state(locs).last_config == str(DEFAULT_CONFIG)


def test_state_keeps_last_config_and_unknown_keys(locs: Locations) -> None:
    save_state(AppState(last_config="E:\\config.json", extra={"future": 1}), locs)
    state = load_state(locs)
    assert state.last_config == "E:\\config.json" and state.extra == {"future": 1}


def test_config_over_a_megabyte_is_refused(tmp_path: Path) -> None:
    big = tmp_path / "config.json"
    big.write_text("{" + " " * MAX_CONFIG_BYTES + "}", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(big)
    assert "a config is never larger than 1 MB" in e.value.problems[0]


def test_packaged_app_ignores_folder_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    planted = tmp_path / "planted"
    for var in ("STAMPER_APP_DIR", "STAMPER_USER_DIR", "STAMPER_LOCAL_DIR"):
        monkeypatch.setenv(var, str(planted))
    assert loc.app_dir() == planted  # from source, the overrides work (tests rely on them)

    exe = tmp_path / "dist" / "DocumentStamper.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert loc.app_dir() == exe.parent.resolve()
    assert loc.user_dir() == tmp_path / "Roaming" / "DocumentStamper"
    assert loc.local_dir() == tmp_path / "Local" / "DocumentStamper"
