"""The progress event a capture reports to its caller (CLI display, background worker)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CaptureProgress:
    """One progress report from :func:`~prismabib.capture.writer.capture_search` or the OpenAlex search.

    Attributes:
        event: ``started`` (new run), ``resumed`` (an unsealed run continues), ``page`` (one
            result page written) or ``sealed`` (the run finished).
        run_id: The run directory name.
        pages_done: Pages held by the run, including those written before a resume.
        records_seen: Records on the pages written by this call (not those held before a resume).
        total_results: The source's total, once a page has reported it; else ``None``.
    """

    event: Literal["started", "resumed", "page", "sealed"]
    run_id: str
    pages_done: int
    records_seen: int
    total_results: int | None


__all__ = ["CaptureProgress"]
