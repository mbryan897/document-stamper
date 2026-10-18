"""Batch stamping: one job, several files, one stamped copy each (D-095, D-097).

``check_batch`` runs the whole-document check (``Handler.plan``) on every
file and looks for output-name problems before anything is saved (D-107),
so the window and the command line can show every problem up front.
``run_batch`` then saves each file that passed. One file failing never stops
the others; an existing output file is skipped and reported, never replaced
unless the caller explicitly allows overwriting (the window never does,
D-075). Each copy keeps its original's protection (D-050).

Nothing here imports Qt; progress and cancellation are plain callables.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .base import (
    ApplyResult,
    CancelCheck,
    Cancelled,
    Handler,
    PlanResult,
    StampError,
    StampJob,
)
from .pdf import same_file

__all__ = [
    "BatchCheck",
    "BatchItem",
    "BatchOutcome",
    "BatchProgress",
    "Status",
    "check_batch",
    "run_batch",
]

BatchProgress = Callable[[int, int, int], None]
"""Called as ``progress(done_pages, total_pages, file_index)``; ``file_index`` is 0-based."""


class Status:
    """Outcome names, as the saved screen and the command line show them."""

    OK = "ok"
    SAVED = "saved"
    SKIPPED = "skipped"  # the output name is taken: nothing was written
    FAILED = "failed"  # the file can't be stamped as set up
    CANCELLED = "cancelled"  # cancelled while this file was being stamped
    NOT_STARTED = "not started"  # cancelled before this file's turn


@dataclass(frozen=True, slots=True)
class BatchItem:
    """One file in a batch: where it is, its password, and where its copy goes."""

    path: Path
    out_path: Path
    password: str | None = None


@dataclass(frozen=True, slots=True)
class BatchCheck:
    """The up-front check for one file: ``status`` is ok, skipped or failed."""

    item: BatchItem
    status: str
    message: str = ""
    plan: PlanResult | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        """True when the file can be saved."""
        return self.status == Status.OK


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """What happened to one file: saved, skipped, failed, cancelled or not started."""

    item: BatchItem
    status: str
    message: str = ""
    result: ApplyResult | None = None
    error: Exception | None = None


def _output_problem(item: BatchItem, items: Sequence[BatchItem], earlier: Sequence[BatchItem],
                    overwrite: bool) -> tuple[str, str]:
    """``(status, message)`` for an output-name problem, or ``("", "")``."""
    out = item.out_path
    for other in items:
        if same_file(other.path, out):
            return Status.FAILED, (f"Its copy would be saved over {other.path.name}, one of the originals. "
                                   "Choose another folder.")
    for prior in earlier:
        if same_file(prior.out_path, out):
            return Status.SKIPPED, (f"{prior.path.name} in this batch is saved to the same name, "
                                    f"{out.name}.")
    if not out.parent.is_dir():
        return Status.FAILED, f"The folder {out.parent} doesn't exist."
    if out.exists() and not overwrite:
        return Status.SKIPPED, f"{out.name} already exists, so it wasn't replaced."
    return "", ""


def check_batch(
    handler: Handler,
    items: Sequence[BatchItem],
    job: StampJob,
    *,
    overwrite: bool = False,
    progress: Callable[[int, int], None] | None = None,
    cancel: CancelCheck | None = None,
) -> list[BatchCheck]:
    """Check every file as ``apply`` would, without writing anything.

    Output problems (an existing file, two files saving to one name, a copy
    that would land on an original) come first; then ``plan`` runs on each
    file. Raises Cancelled when ``cancel`` returns True. ``progress(done,
    total)`` counts files.
    """
    checks: list[BatchCheck] = []
    for i, item in enumerate(items):
        if cancel is not None and cancel():
            raise Cancelled("The check was cancelled. Nothing was saved.")
        status, message = _output_problem(item, items, items[:i], overwrite)
        if status:
            checks.append(BatchCheck(item, status, message))
        else:
            try:
                plan = handler.plan(item.path, job, password=item.password, cancel=cancel)
            except Cancelled:
                raise
            except StampError as e:
                checks.append(BatchCheck(item, Status.FAILED, str(e), error=e))
            except Exception as e:  # reported per file, never stops the batch
                checks.append(BatchCheck(item, Status.FAILED, f"Something went wrong ({type(e).__name__}: {e}).",
                                         error=e))
            else:
                checks.append(BatchCheck(item, Status.OK, plan=plan))
        if progress is not None:
            progress(i + 1, len(items))
    return checks


def run_batch(
    handler: Handler,
    checks: Sequence[BatchCheck],
    job: StampJob,
    *,
    accept_signature_loss: bool = False,
    accept_restrictions: bool = False,
    remove_encryption: bool = False,
    overwrite: bool = False,
    progress: BatchProgress | None = None,
    cancel: CancelCheck | None = None,
) -> list[BatchOutcome]:
    """Save every file whose check passed; report every file.

    Files that failed or were skipped in ``checks`` are reported as such
    without being opened again. A file that fails while saving (a disk error,
    a file changed since the check) is reported and the batch goes on.
    Cancelling stops between pages: copies already saved stay, the current
    file is reported as cancelled and the rest as not started.
    """
    total = sum(c.plan.page_count for c in checks if c.ok and c.plan is not None)
    done_before = 0
    outcomes: list[BatchOutcome] = []
    stopped = False
    for index, check in enumerate(checks):
        item = check.item
        if not check.ok:
            outcomes.append(BatchOutcome(item, check.status, check.message, error=check.error))
            continue
        if stopped or (cancel is not None and cancel()):
            stopped = True
            outcomes.append(BatchOutcome(item, Status.NOT_STARTED, "Cancelled before this file was stamped."))
            continue
        pages = check.plan.page_count if check.plan is not None else 0

        def page_progress(done: int, _total: int, base: int = done_before, idx: int = index) -> None:
            if progress is not None:
                progress(base + done, total, idx)

        try:
            result = handler.apply(
                item.path, job, item.out_path,
                password=item.password,
                accept_signature_loss=accept_signature_loss,
                accept_restrictions=accept_restrictions,
                remove_encryption=remove_encryption,
                overwrite=overwrite,
                progress=page_progress,
                cancel=cancel,
            )
        except Cancelled:
            stopped = True
            outcomes.append(BatchOutcome(item, Status.CANCELLED, "Cancelled while stamping; nothing was saved for this file."))
        except StampError as e:
            outcomes.append(BatchOutcome(item, Status.FAILED, str(e), error=e))
        except Exception as e:
            outcomes.append(BatchOutcome(item, Status.FAILED, f"Something went wrong ({type(e).__name__}: {e}).",
                                         error=e))
        else:
            outcomes.append(BatchOutcome(item, Status.SAVED, "", result=result))
        done_before += pages
    return outcomes
