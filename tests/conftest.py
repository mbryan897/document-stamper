"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import make_corpus


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    """Return the generated corpus folder, building any missing files first."""
    out = make_corpus.DEFAULT_OUT
    if not make_corpus.is_complete(out):
        make_corpus.generate(out)
    return out
