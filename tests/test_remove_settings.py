"""Remove my settings from this computer… (step 13).

Only exact known paths are removed: state.json, the pending journal when it's
empty, the Send to shortcut when it opens this copy, then the user folder,
the combine work folder and the local folder if each is empty. Never a
settings file or its backups. Every folder is a temp folder (conftest); the
Send to shortcut is a plain file standing in for one, with its status
supplied, since shortcuts can only be made on Windows. Synthetic data only.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from pytestqt.qtbot import QtBot

from stamper import sendto
from stamper.core import pending
from stamper.core.locations import AppState, Locations, local_dir, save_state, work_dir
from stamper.core.traces import carry_out, plan_removal
from stamper.gui.app import make_app
from stamper.gui.main_window import MainWindow
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
    return locs.user.parent / "SendTo" / sendto.SHORTCUT_NAME


def _plant(locs: Locations) -> dict[str, Path]:
    """state.json, a user config with both kinds of backup, an empty journal and work folder, and a stranger."""
    save_state(AppState(last_profile="Records", last_folder=str(locs.user)), locs)
    shutil.copyfile(RECORDS_CONFIG, locs.user_config)
    bak = locs.user / "config.json.bak"
    dated = locs.user / "config.json.20261101-101010.bak"
    for b in (bak, dated):
        shutil.copyfile(RECORDS_CONFIG, b)
    pending.journal_dir().mkdir(parents=True, exist_ok=True)
    work_dir().mkdir(parents=True, exist_ok=True)
    other = locs.user / "notes-someone-left.txt"
    other.write_text("not ours", encoding="utf-8")
    return {"bak": bak, "dated": dated, "other": other}


def _window(qtbot: QtBot, fake: FakeServices, locs: Locations) -> MainWindow:
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs, sendto_shortcut=_shortcut(locs))
    qtbot.addWidget(w)
    w.show()
    return w


def _status(monkeypatch: pytest.MonkeyPatch, state: sendto.SendToState, shortcut: Path) -> None:
    monkeypatch.setattr(ProfileSettings, "sendto_status",
                        lambda self: sendto.SendToStatus(state, shortcut))


# ------------------------------------------------------------------ the plan and its carrying out


def test_plan_lists_exact_paths_and_keeps_settings_files(locs: Locations) -> None:
    planted = _plant(locs)
    shortcut = _shortcut(locs)
    shortcut.parent.mkdir()
    shortcut.write_bytes(b"lnk")
    r = plan_removal(locs, settings_files=(locs.user_config, locs.app_config), shortcut=shortcut,
                     shortcut_is_ours=True)
    assert r.files == (locs.state_file, shortcut)
    assert r.folders == (pending.journal_dir(), work_dir(), local_dir(), locs.user)
    assert r.kept == ((locs.user_config, "settings files are never removed, nor their backups"),)
    out = carry_out(r)
    assert out.removed == [locs.state_file, shortcut, pending.journal_dir(), work_dir(), local_dir()]
    assert (locs.user, "other files are in it") in out.left
    for kept in (locs.user_config, planted["bak"], planted["dated"], planted["other"]):
        assert kept.is_file(), kept
    assert not local_dir().exists()


def test_a_journal_with_entries_and_a_running_combine_are_left(locs: Locations, tmp_path: Path) -> None:
    _plant(locs)
    tmp = tmp_path / "~stamper-x.tmp"
    tmp.write_bytes(b"")
    entry = pending.record(tmp)
    assert entry is not None and entry.is_file()
    (work_dir() / "~stamper-combine-x").mkdir()
    r = plan_removal(locs, combine_running=True)
    assert pending.journal_dir() not in r.folders and work_dir() not in r.folders
    whys = dict(r.kept)
    assert "weren't finished" in whys[pending.journal_dir()]
    assert whys[work_dir()] == "files are being combined"
    carry_out(r)
    assert entry.is_file() and (work_dir() / "~stamper-combine-x").is_dir()


def test_a_work_folder_with_something_in_it_is_left_even_when_no_combine_runs(locs: Locations) -> None:
    _plant(locs)
    (work_dir() / "~stamper-combine-other").mkdir()
    out = carry_out(plan_removal(locs))
    assert (work_dir(), "other files are in it") in out.left
    assert (work_dir() / "~stamper-combine-other").is_dir()


def test_a_shortcut_to_another_copy_is_kept(locs: Locations) -> None:
    shortcut = _shortcut(locs)
    shortcut.parent.mkdir()
    shortcut.write_bytes(b"lnk")
    r = plan_removal(locs, shortcut=shortcut, shortcut_is_ours=False)
    assert shortcut not in r.files
    assert (shortcut, "it opens another copy of Document Stamper") in r.kept
    carry_out(r)
    assert shortcut.is_file()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_links_are_never_followed_or_removed(locs: Locations, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    local_dir().parent.mkdir(parents=True, exist_ok=True)
    if local_dir().exists():
        shutil.rmtree(local_dir())
    local_dir().symlink_to(target, target_is_directory=True)
    state_target = tmp_path / "real-state.json"
    state_target.write_text("{}", encoding="utf-8")
    locs.state_file.symlink_to(state_target)
    out = carry_out(plan_removal(locs))
    assert locs.state_file.is_symlink() and state_target.is_file()
    assert local_dir().is_symlink() and (target / "keep.txt").is_file()
    assert local_dir() not in out.removed


def test_nothing_is_found_by_searching(locs: Locations) -> None:
    """Files with similar names beside the known ones stay: no wildcard."""
    _plant(locs)
    look_alikes = [locs.user / "state.json.bak", locs.user / "state.json.old", locs.user / "~stamper-1.tmp",
                   pending.journal_dir().parent / "pending2"]
    for p in look_alikes:
        p.write_text("x", encoding="utf-8")
    carry_out(plan_removal(locs))
    for p in look_alikes:
        assert p.exists(), p


# ------------------------------------------------------------------ the window


def test_confirm_lists_what_goes_and_what_stays(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    planted = _plant(locs)
    elsewhere = _shortcut(locs)
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"lnk to another copy")
    _status(monkeypatch, sendto.SendToState.ELSEWHERE, elsewhere)
    w = _window(qtbot, fake, locs)
    try:
        s = w.open_settings()
        assert s is not None
        s.show_section(3)
        assert s.general_page.forget_button.text() == "Remove my settings from this computer…"
        fake.confirms = [False]
        s.general_page.forget_button.click()
        title, text = fake.questions[-1]
        assert title == "Remove my settings from this computer?"
        assert f"• {locs.state_file}\n" in text
        assert f"• {locs.user} (the folder, if nothing else is left in it)" in text
        assert f"• {elsewhere}: it opens another copy of Document Stamper" in text
        assert f"• {locs.user_config}: settings files are never removed, nor their backups" in text
        assert "Settings files (config.json) and their backups are never removed." in text
        assert locs.state_file.is_file()  # declined: nothing removed

        fake.confirms = [True]
        s.general_page.forget_button.click()
        assert not locs.state_file.exists()
        for kept in (locs.user_config, planted["bak"], planted["dated"], planted["other"], elsewhere):
            assert kept.is_file(), kept
        said = s.status.text()
        assert said.startswith("Removed ")
        assert f"Left {elsewhere}: it opens another copy of Document Stamper." in said
        assert f"Left {locs.user_config}: settings files are never removed, nor their backups." in said
        assert f"Left {locs.user}: other files are in it." in said
    finally:
        close_window(w)


def test_shortcut_to_this_copy_is_removed(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    ours = _shortcut(locs)
    ours.parent.mkdir()
    ours.write_bytes(b"lnk to this copy")
    _status(monkeypatch, sendto.SendToState.CURRENT, ours)
    w = _window(qtbot, fake, locs)
    try:
        s = w.open_settings()
        assert s is not None
        fake.confirms = [True]
        s.remove_my_settings()
        assert f"• {ours}\n" in fake.questions[-1][1]
        assert not ours.exists()
    finally:
        close_window(w)


def test_the_window_forgets_state_too(qtbot: QtBot, fake: FakeServices, locs: Locations,
                                      corpus_dir: Path, tmp_path: Path) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    w = _window(qtbot, fake, locs)
    try:
        w.state.last_folder = str(tmp_path)
        w.state.last_save_folder = str(tmp_path)
        save_state(w.state, locs)
        s = w.open_settings()
        assert s is not None
        fake.confirms = [True]
        s.remove_my_settings()
        assert not locs.state_file.exists()
        assert w.state == AppState() and w._last_folder is None
        w._use_profile(w.profile.name)  # the next thing remembered starts from nothing
        assert str(tmp_path) not in locs.state_file.read_text(encoding="utf-8")
    finally:
        close_window(w)


def test_nothing_to_remove_says_so(qtbot: QtBot, fake: FakeServices, locs: Locations, tmp_path: Path) -> None:
    w = _window(qtbot, fake, locs)
    try:
        shutil.rmtree(locs.user)
        shutil.rmtree(local_dir())
        s = w.open_settings()
        assert s is not None
        assert s.remove_my_settings() is None
        assert fake.questions == []
        assert s.status.text().startswith("Nothing of Document Stamper's is stored on this computer to remove.")
    finally:
        close_window(w)


def test_hidden_characters_in_paths_are_shown(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> None:
    odd_user = tmp_path / "us‮er"
    odd_user.mkdir()
    (tmp_path / "app").mkdir()
    locs = Locations(app=tmp_path / "app", user=odd_user, builtin=DEFAULT_CONFIG)
    save_state(AppState(last_profile="Standard"), locs)
    w = _window(qtbot, fake, locs)
    try:
        s = w.open_settings()
        assert s is not None
        fake.confirms = [False]
        s.remove_my_settings()
        text = fake.questions[-1][1]
        assert "‮" not in text and "⟨U+202E⟩" in text
    finally:
        close_window(w)
