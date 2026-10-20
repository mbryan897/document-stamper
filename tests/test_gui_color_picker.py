"""The stamp color button in Profile settings (D-094)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtGui import QColor
from pytestqt.qtbot import QtBot

from stamper.core.config import load_config
from stamper.core.locations import Locations
from stamper.gui.app import make_app
from stamper.gui.color_picker import (
    MIN_CONTRAST,
    STAMP_COLORS,
    color_name,
    contrast_on_white,
    light_color_note,
)
from stamper.gui.main_window import MainWindow
from stamper.gui.profiles import ProfileSettings
from tests.gui_support import TODAY, FakeServices, close_window, fake  # noqa: F401
from tests.stamping import DEFAULT_CONFIG, RECORDS_CONFIG

# ------------------------------------------------------------ the palette


def test_palette_is_twenty_valid_distinct_colors() -> None:
    hexes = [h for _, h in STAMP_COLORS]
    assert len(STAMP_COLORS) == 20
    assert len(set(hexes)) == 20 and len({n for n, _ in STAMP_COLORS}) == 20
    assert all(QColor.isValidColorName(h) and len(h) == 7 for h in hexes)
    assert STAMP_COLORS[0] == ("Stamp blue", "#2B3A8F")  # the shipped default comes first


def test_names_and_codes() -> None:
    assert color_name("#2b3a8f") == "Stamp blue"
    assert color_name("#123456") == "#123456"


def test_contrast() -> None:
    assert contrast_on_white("#000000") == pytest.approx(21.0)
    assert contrast_on_white("#FFFFFF") == pytest.approx(1.0)


@pytest.mark.parametrize("name", ["Yellow", "Orange", "Light green", "Light blue", "Green"])
def test_light_colors_get_a_note(name: str) -> None:
    hex_color = dict(STAMP_COLORS)[name]
    assert contrast_on_white(hex_color) < MIN_CONTRAST
    assert light_color_note(hex_color).startswith(f"{name} is light")


@pytest.mark.parametrize("name", ["Stamp blue", "Black", "Dark red", "Red", "Blue", "Purple"])
def test_dark_colors_get_no_note(name: str) -> None:
    assert light_color_note(dict(STAMP_COLORS)[name]) == ""


# ------------------------------------------------------------ in Profile settings


@pytest.fixture
def settings(qtbot: QtBot, fake: FakeServices, tmp_path: Path) -> Iterator[ProfileSettings]:
    app, user = tmp_path / "app", tmp_path / "user"
    app.mkdir()
    user.mkdir()
    locs = Locations(app=app, user=user, builtin=DEFAULT_CONFIG)
    shutil.copyfile(RECORDS_CONFIG, locs.app_config)
    make_app()
    w = MainWindow(services=fake.services(), today=TODAY, locs=locs,
                   sendto_shortcut=user / "SendTo" / "Document Stamper.lnk")
    qtbot.addWidget(w)
    w.resize(1280, 860)
    w.show()
    s = w.open_settings()
    assert s is not None
    yield s
    close_window(w)


def test_label_is_spelled_color(settings: ProfileSettings) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    assert ed.color.accessibleName() == "Stamp 1 color"
    texts = [lab.text() for lab in ed.findChildren(type(ed.example))]
    assert "Color" in texts and "Colour" not in texts


def test_button_shows_the_profiles_color(settings: ProfileSettings) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    assert ed.color.color() == "#2B3A8F"
    assert ed.color.text().startswith("Stamp blue")
    assert ed.color_note.isHidden()


def test_picking_a_swatch_changes_the_draft_and_saves(qtbot: QtBot, settings: ProfileSettings) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    ed.color.panel.swatch("Dark red").click()
    assert ed.color.color() == "#C00000" and ed.color.text().startswith("Dark red")
    assert settings.draft.profiles[0]["stamps"][0]["color"] == "#C00000"
    qtbot.waitUntil(settings.can_save)
    settings.save()
    saved = load_config(settings.source.path)
    assert saved.profiles[0].stamps[0].color.upper() == "#C00000"
    assert json.loads(settings.source.path.read_text(encoding="utf-8"))["profiles"][0]["stamps"][0]["color"] \
        == "#C00000"


def test_light_swatch_shows_a_note_but_still_saves(qtbot: QtBot, settings: ProfileSettings) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    ed.color.panel.swatch("Yellow").click()
    assert not ed.color_note.isHidden() and "Yellow is light" in ed.color_note.text()
    qtbot.waitUntil(settings.can_save)
    ed.color.panel.swatch("Black").click()
    assert ed.color_note.isHidden()


def test_more_colors_uses_the_color_wheel(settings: ProfileSettings, fake: FakeServices) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    fake.colors.append("#3366AA")
    ed.color.panel.more_button.click()
    assert fake.color_prompts == ["#2B3A8F"]  # the wheel opens on the current color
    assert ed.color.color() == "#3366AA" and ed.color.text().startswith("#3366AA")
    assert settings.draft.profiles[0]["stamps"][0]["color"] == "#3366AA"


def test_cancelling_the_color_wheel_changes_nothing(settings: ProfileSettings, fake: FakeServices) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    ed.color.panel.more_button.click()  # nothing queued: the user cancelled
    assert ed.color.color() == "#2B3A8F"
    assert not settings.draft.dirty


def test_same_color_is_not_an_edit(settings: ProfileSettings) -> None:
    ed = settings.profiles_page.stamp_editors[0]
    ed.color.panel.swatch("Stamp blue").click()
    assert not settings.draft.dirty
