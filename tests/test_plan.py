"""Handler.plan: the whole-document check before saving (D-074)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from stamper.handlers.base import Cancelled, StampDoesNotFit, StampJob, UnsupportedDocument
from stamper.handlers.pdf import PROPERTIES_NOTE_PREFIX, PdfHandler
from tests.stamping import (
    default_job,
    make_xfa_pdf,
    password_for,
    records_config,
    sha256,
)
from tools import make_corpus

H = PdfHandler()
CONFIRMATION_NOTES = ("digitally signed", "owner restrictions")


@pytest.mark.parametrize("name", list(make_corpus.CORPUS))
def test_plan_matches_apply(name: str, corpus_dir: Path, tmp_path: Path) -> None:
    src = corpus_dir / name
    pw = password_for(name)
    before = sha256(src)
    plan = H.plan(src, default_job(), password=pw)
    assert sha256(src) == before
    result = H.apply(src, default_job(), tmp_path / name, password=pw, accept_restrictions=True)
    assert plan.page_count == result.pages_stamped
    assert plan.placements == result.placements
    applied = tuple(w for w in result.warnings if not any(n in w for n in CONFIRMATION_NOTES))
    # The properties note names values, and the copy's "Last changed" is only known once saved (UX-046).
    assert [w for w in plan.warnings if not w.startswith(PROPERTIES_NOTE_PREFIX)] == \
        [w for w in applied if not w.startswith(PROPERTIES_NOTE_PREFIX)]
    assert sum(w.startswith(PROPERTIES_NOTE_PREFIX) for w in plan.warnings) == 1
    assert sum(w.startswith(PROPERTIES_NOTE_PREFIX) for w in applied) == 1
    assert plan.metadata_written == result.metadata_written
    assert plan.metadata_replaced == result.metadata_replaced


def test_plan_reports_form_field_overlap(corpus_dir: Path) -> None:
    plan = H.plan(corpus_dir / "acroform_filled.pdf", default_job())
    assert any("overlaps a form field" in w for w in plan.warnings)


def test_plan_finds_a_page_that_doesnt_fit(corpus_dir: Path) -> None:
    cfg = records_config()
    job = StampJob.from_profile(cfg, cfg.default_profile,
                                {"identifier": "99-2026-999901", "doc_type": "Search warrant return"})
    with pytest.raises(StampDoesNotFit) as e:
        H.plan(corpus_dir / "mixed_sizes.pdf", job)
    assert e.value.page == 4  # the receipt, not the page being previewed
    assert "Page 4" in str(e.value)


def test_plan_refuses_xfa(corpus_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(UnsupportedDocument, match="XFA"):
        H.plan(make_xfa_pdf(corpus_dir, tmp_path / "x.pdf"), default_job())


def test_plan_can_be_cancelled(corpus_dir: Path) -> None:
    calls = []

    def cancel() -> bool:
        calls.append(1)
        return len(calls) > 3

    with pytest.raises(Cancelled):
        H.plan(corpus_dir / "large_520_pages.pdf", default_job(), cancel=cancel)
    assert len(calls) == 4


def test_plan_is_quick_on_520_pages(corpus_dir: Path) -> None:
    start = time.perf_counter()
    plan = H.plan(corpus_dir / "large_520_pages.pdf", default_job())
    elapsed = time.perf_counter() - start
    assert plan.page_count == make_corpus.LARGE_PAGE_COUNT
    assert elapsed < 2.0, f"plan took {elapsed:.2f} s"
