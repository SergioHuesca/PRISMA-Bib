"""What the size-check screen decides, without widgets (GUI spec #312, ticket #328)."""

from __future__ import annotations

from datetime import datetime, tzinfo
from typing import Final, Literal

from prismabib.sizecheck import NOT_REPORTED, OpenAlexSizeCheck, ScopusSizeCheck

WARN_ABOVE: Final = 2_000
BLOCK_ABOVE: Final = 10_000

Verdict = Literal["ok", "warn", "block"]

WARN_TEXT: Final = (
    "This search matches a lot of Records. Screening them all takes a long time: "
    "narrow the query, or confirm below that you want to continue."
)
BLOCK_TEXT: Final = (
    "This search matches too many Records to continue. A very large set would slow the "
    "Screener: narrow the query (more specific terms, a shorter year range) and check again."
)


def verdict(count: int) -> Verdict:
    """2,000 and fewer pass, up to 10,000 warn and need a confirm, above 10,000 stop."""
    return "block" if count > BLOCK_ABOVE else "warn" if count > WARN_ABOVE else "ok"


def local_reset(moment: datetime, tz: tzinfo | None = None) -> str:
    """A UTC instant as local time (``tz`` for tests, else this machine's zone)."""
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z").strip()


def _allowance(value: int | str, what: str) -> str:
    return f"{what}: not reported by the service." if value == NOT_REPORTED else f"{what}: {value}"


def scopus_lines(result: ScopusSizeCheck) -> list[str]:
    """The Scopus numbers, plus a sentence before any capture when the quota would run out."""
    lines = [
        f"Records: {result.record_count:,}",
        f"Quota cost: {result.quota_cost:,} requests",
        _allowance(result.weekly_remaining, "Remaining weekly allowance"),
    ]
    left = result.weekly_remaining
    if isinstance(left, int) and result.quota_cost > left:
        lines.append(
            f"**The weekly quota would be exceeded:** this capture needs {result.quota_cost:,} "
            f"requests and {left:,} remain. Narrow the query or wait for the reset."
        )
    return lines


def openalex_lines(result: OpenAlexSizeCheck, tz: tzinfo | None = None) -> list[str]:
    """The OpenAlex numbers, with the reset shown in local time."""
    reset = result.resets_at
    return [
        f"Records: {result.record_count:,}",
        f"Cost bound: ${result.cost_bound_usd:,.2f}",
        f"Page budget: {result.pages:,} pages",
        _allowance(result.daily_remaining, "Remaining daily allowance"),
        "Allowance resets: not reported by the service."
        if isinstance(reset, str)
        else f"Allowance resets: {local_reset(reset, tz)}",
    ]
