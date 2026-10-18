"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

# GUI tests never open a visible window, on Windows or anywhere else.
if not os.environ.get("QT_QPA_PLATFORM"):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest

from tools import make_corpus


@pytest.fixture(autouse=True)
def _isolated_folders(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the app, user and local folders at empty temp folders (D-080, D-124).

    A developer's own ``config.json`` next to the repo or in ``%APPDATA%``
    must never change what a test sees, and tests must never write there.
    Subprocesses inherit the variables.
    """
    root = tmp_path_factory.mktemp("folders")
    (root / "app").mkdir()
    (root / "user").mkdir()
    (root / "local").mkdir()
    monkeypatch.setenv("STAMPER_APP_DIR", str(root / "app"))
    monkeypatch.setenv("STAMPER_USER_DIR", str(root / "user"))
    monkeypatch.setenv("STAMPER_LOCAL_DIR", str(root / "local"))


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    """Return the generated corpus folder, building any missing files first."""
    out = make_corpus.DEFAULT_OUT
    if not make_corpus.is_complete(out):
        make_corpus.generate(out)
    return out
