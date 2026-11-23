"""The copy's ModDate carries the UTC offset in force when it was stamped, not always the summer one."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pymupdf
import pytest

from stamper.handlers.pdf import PdfHandler, pdf_now
from tests.stamping import default_job


@pytest.mark.parametrize(("when", "expected"), [
    (datetime(2026, 11, 22, 22, 2, 23, tzinfo=timezone(timedelta(hours=-5))), "D:20261122220223-05'00'"),  # winter
    (datetime(2026, 7, 4, 9, 5, 0, tzinfo=timezone(timedelta(hours=-4))), "D:20260704090500-04'00'"),  # summer
    (datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))), "D:20260115120000+05'30'"),
    (datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=-3, minutes=-30))), "D:20260115120000-03'30'"),
    (datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc), "D:20260115120000Z"),
])
def test_pdf_now_writes_the_offset_of_that_moment(when: datetime, expected: str) -> None:
    assert pdf_now(when) == expected


def test_pdf_now_without_a_time_is_now_in_local_time() -> None:
    before = datetime.now().astimezone().replace(microsecond=0)
    stamp = pdf_now()
    after = datetime.now().astimezone()
    assert pdf_now(before) <= stamp <= pdf_now(after) or before.utcoffset() != after.utcoffset()


@pytest.fixture
def eastern() -> Iterator[None]:
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    yield
    if old is None:
        del os.environ["TZ"]
    else:
        os.environ["TZ"] = old
    time.tzset()


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="the time zone can't be set for one test on Windows")
def test_a_copy_stamped_now_has_the_offset_now_in_force(eastern: None, tmp_path: Path) -> None:
    """In a zone with daylight saving, whichever half of the year the test runs in."""
    doc = pymupdf.open()
    doc.new_page()
    src = tmp_path / "one.pdf"
    doc.save(src)
    doc.close()
    PdfHandler().apply(src, default_job(), tmp_path / "out.pdf")
    with pymupdf.open(tmp_path / "out.pdf") as out:
        mod = out.metadata["modDate"]
    offset = datetime.now().astimezone().utcoffset()
    assert offset in (timedelta(hours=-5), timedelta(hours=-4))
    assert mod.endswith("-05'00'" if offset == timedelta(hours=-5) else "-04'00'"), mod
