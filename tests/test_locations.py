"""Config lookup, safe saving and last-used values (D-080, D-081, D-087)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from stamper.core import locations as loc
from stamper.core.config import ConfigError, load_config
from stamper.core.locations import AppState, Locations, SourceKind
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def locs(tmp_path: Path) -> Locations:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    return Locations(app=app, user=user, builtin=DEFAULT_CONFIG)


def _make_read_only(path: Path) -> None:
    path.chmod(stat.S_IREAD)


# ------------------------------------------------------------ folders


def test_source_layout_folders() -> None:
    assert loc.resource_dir() == REPO
    assert loc.builtin_config_path() == DEFAULT_CONFIG
    assert (loc.resource_dir() / "assets" / "fonts").is_dir()


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STAMPER_APP_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("STAMPER_USER_DIR", str(tmp_path / "u"))
    assert loc.app_dir() == tmp_path / "a"
    assert loc.user_dir() == tmp_path / "u"


def test_user_dir_uses_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("STAMPER_USER_DIR")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert loc.user_dir() == tmp_path / "DocumentStamper"


def test_app_dir_without_override_is_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STAMPER_APP_DIR")
    assert loc.app_dir() == REPO


def test_frozen_folders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = tmp_path / "DocumentStamper" / "DocumentStamper.exe"
    internal = exe.parent / "_internal"
    monkeypatch.delenv("STAMPER_APP_DIR")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    assert loc.is_frozen()
    assert loc.app_dir() == exe.parent.resolve()
    assert loc.resource_dir() == internal
    assert loc.builtin_config_path() == internal / "config" / "default.json"


# ------------------------------------------------------------ lookup order


def test_nothing_found_uses_the_builtin_read_only(locs: Locations) -> None:
    src = loc.find_config(locs)
    assert src == loc.ConfigSource(DEFAULT_CONFIG, SourceKind.BUILTIN, False)


def test_user_folder_comes_before_the_builtin(locs: Locations) -> None:
    locs.user.mkdir()
    shutil.copyfile(RECORDS_CONFIG, locs.user_config)
    src = loc.find_config(locs)
    assert (src.path, src.kind, src.writable) == (locs.user_config, SourceKind.USER, True)


def test_app_folder_comes_first(locs: Locations) -> None:
    locs.user.mkdir()
    shutil.copyfile(RECORDS_CONFIG, locs.user_config)
    shutil.copyfile(DEFAULT_CONFIG, locs.app_config)
    src = loc.find_config(locs)
    assert (src.path, src.kind) == (locs.app_config, SourceKind.APP)


def test_an_invalid_file_is_still_chosen_and_reported(locs: Locations) -> None:
    # No silent fall-back to another config: the user must see why theirs failed.
    locs.app_config.write_text('{"schema_version": 1}', encoding="utf-8")
    assert loc.find_config(locs).path == locs.app_config
    with pytest.raises(ConfigError, match="config.json"):
        loc.load_located(locs)


def test_load_located(locs: Locations) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    cfg, src = loc.load_located(locs)
    assert src.kind is SourceKind.APP
    assert cfg.default_profile.name == load_config(RECORDS_CONFIG).default_profile.name


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_read_only_file_is_not_writable(locs: Locations) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    _make_read_only(locs.app_config)
    src = loc.find_config(locs)
    assert src.kind is SourceKind.APP and not src.writable
    assert loc.save_target(src, locs) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only attribute")
def test_read_only_attribute_is_not_writable_on_windows(locs: Locations) -> None:
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    _make_read_only(locs.app_config)
    try:
        assert not loc.find_config(locs).writable
    finally:
        locs.app_config.chmod(stat.S_IWRITE | stat.S_IREAD)


# ------------------------------------------------------------ save target


def test_saving_the_builtin_goes_next_to_the_app(locs: Locations) -> None:
    target = loc.save_target(loc.find_config(locs), locs)
    assert target == loc.ConfigSource(locs.app_config, SourceKind.APP, True)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root")
def test_saving_the_builtin_falls_back_to_the_user_folder(locs: Locations) -> None:
    locs.app.chmod(stat.S_IREAD | stat.S_IEXEC)
    try:
        target = loc.save_target(loc.find_config(locs), locs)
        assert target == loc.ConfigSource(locs.user_config, SourceKind.USER, True)
        assert locs.user.is_dir()
    finally:
        locs.app.chmod(stat.S_IRWXU)


def test_saving_the_builtin_with_no_app_folder_uses_the_user_folder(tmp_path: Path) -> None:
    locs = Locations(app=tmp_path / "missing", user=tmp_path / "user", builtin=DEFAULT_CONFIG)
    target = loc.save_target(loc.find_config(locs), locs)
    assert target is not None and target.kind is SourceKind.USER


def test_a_writable_file_saves_to_itself(locs: Locations, tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.json"
    shutil.copyfile(RECORDS_CONFIG, other)
    src = loc.describe(other, SourceKind.OTHER)
    assert src.writable and loc.save_target(src, locs) == src


# ------------------------------------------------------------ save_config


def test_save_writes_and_round_trips(tmp_path: Path) -> None:
    cfg = load_config(RECORDS_CONFIG)
    out = tmp_path / "config.json"
    assert loc.save_config(cfg, out) == out
    assert load_config(out) == cfg
    assert not loc.backup_path(out).exists()  # nothing to back up the first time
    assert [p.name for p in tmp_path.iterdir()] == ["config.json"]  # no temp file left


def test_save_keeps_one_backup(tmp_path: Path) -> None:
    out = tmp_path / "config.json"
    shutil.copyfile(DEFAULT_CONFIG, out)
    before = out.read_bytes()
    loc.save_config(load_config(RECORDS_CONFIG), out)
    assert loc.backup_path(out).read_bytes() == before
    loc.save_config(load_config(DEFAULT_CONFIG), out)
    assert load_config(loc.backup_path(out)) == load_config(RECORDS_CONFIG)


def test_save_refuses_a_file_changed_since_it_was_loaded(tmp_path: Path) -> None:
    out = tmp_path / "config.json"
    shutil.copyfile(DEFAULT_CONFIG, out)
    loaded = loc.describe(out, SourceKind.APP).digest
    loc.save_config(load_config(RECORDS_CONFIG), out, expect=loaded)  # unchanged: saves
    theirs = out.read_bytes()
    with pytest.raises(loc.ConfigChanged, match="changed by someone else"):
        loc.save_config(load_config(DEFAULT_CONFIG), out, expect=loaded)  # changed since: refused
    assert out.read_bytes() == theirs
    with pytest.raises(loc.ConfigChanged):
        loc.save_config(load_config(DEFAULT_CONFIG), tmp_path / "new.json", expect=loaded)
    loc.save_config(load_config(DEFAULT_CONFIG), tmp_path / "new.json", expect=None)  # no file expected


def test_save_without_backup(tmp_path: Path) -> None:
    out = tmp_path / "export.json"
    shutil.copyfile(DEFAULT_CONFIG, out)
    loc.save_config(load_config(RECORDS_CONFIG), out, backup=False)
    assert not loc.backup_path(out).exists()


def test_save_writes_lf_utf8_without_bom(tmp_path: Path) -> None:
    out = tmp_path / "config.json"
    loc.save_config(load_config(RECORDS_CONFIG), out)
    raw = out.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf") and b"\r\n" not in raw


def test_failed_write_leaves_the_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "config.json"
    shutil.copyfile(DEFAULT_CONFIG, out)
    before = out.read_bytes()

    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(loc.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        loc.save_config(load_config(RECORDS_CONFIG), out)
    assert out.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.json", "config.json.bak"]


# ------------------------------------------------------------ state


def test_state_round_trip(locs: Locations) -> None:
    assert loc.load_state(locs) == AppState()
    assert loc.save_state(AppState("Records submission", r"D:\Returns"), locs)
    assert loc.load_state(locs) == AppState("Records submission", r"D:\Returns")


def test_state_never_holds_field_values() -> None:
    # last_config is a file path (D-121), never a field value.
    assert set(json.loads(AppState("P", "F").to_json())) == {"last_profile", "last_folder", "last_config"}


def test_damaged_state_is_ignored(locs: Locations) -> None:
    locs.user.mkdir()
    for text in ("{not json", "[1, 2]", '{"last_profile": 5}'):
        locs.state_file.write_text(text, encoding="utf-8")
        assert loc.load_state(locs).last_profile is None


def test_unknown_state_keys_are_kept(locs: Locations) -> None:
    locs.user.mkdir()
    locs.state_file.write_text('{"last_profile": "A", "future": [1]}', encoding="utf-8")
    state = loc.load_state(locs)
    state.last_folder = "F"
    loc.save_state(state, locs)
    assert json.loads(locs.state_file.read_text(encoding="utf-8"))["future"] == [1]


def test_state_save_failure_is_quiet(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    locs = Locations(app=tmp_path, user=blocker / "sub", builtin=DEFAULT_CONFIG)
    assert loc.save_state(AppState("P"), locs) is False


def test_state_goes_to_the_user_folder_not_the_app(locs: Locations) -> None:
    loc.save_state(AppState("P"), locs)
    assert locs.state_file.parent == locs.user
    assert list(locs.app.iterdir()) == []


# ------------------------------------------------------------ CLI uses the lookup


def _cli(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "stamper", *args], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=60)


def test_cli_uses_config_next_to_the_app(corpus_dir: Path, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    shutil.copyfile(RECORDS_CONFIG, app / "config.json")
    env = {**os.environ, "STAMPER_APP_DIR": str(app), "STAMPER_USER_DIR": str(tmp_path / "u")}
    # The Records fixture's doc types differ from the default's: "Report" isn't one of them.
    r = _cli(str(corpus_dir / "letter_portrait.pdf"), "--set", "identifier=99-2026-999901",
             "--set", "doc_type=Report", "--dry-run", env=env)
    assert r.returncode == 4, r.stdout + r.stderr
    r = _cli(str(corpus_dir / "letter_portrait.pdf"), "--set", "identifier=99-2026-999901",
             "--set", "doc_type=Search warrant return", "--dry-run", env=env)
    assert r.returncode == 0, r.stdout + r.stderr


def test_cli_config_option_still_overrides(corpus_dir: Path, tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    shutil.copyfile(RECORDS_CONFIG, app / "config.json")
    env = {**os.environ, "STAMPER_APP_DIR": str(app), "STAMPER_USER_DIR": str(tmp_path / "u")}
    r = _cli(str(corpus_dir / "letter_portrait.pdf"), "--config", str(DEFAULT_CONFIG),
             "--set", "identifier=X1", "--set", "doc_type=Report", "--dry-run", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
