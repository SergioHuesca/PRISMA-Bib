"""OpenAlex works search as a primary source, in Layer 0 (ADR 0042 Decision 4).

:func:`capture_openalex_search` pages ``GET /works?search=...`` by cursor and writes
``raw/openalex-search/<run_id>/response-NNNN.jsonl`` (one HTTP response per file), a
``progress.json`` sidecar while the run is open, and ``manifest.json`` (the seal) only when all four
seal rules hold:

1. the last response has no ``next_cursor``;
2. ``total_count`` (``meta.count`` of the first page) is known and equals the works captured;
3. every page was a 200 whose ``meta.count`` equals ``total_count``;
4. the run was never resumed with a changed count (rule 3 catches it on the resumed page).

**Discard and restart (D-4).** A run that cannot satisfy rules 2 to 4, or whose stored cursor the
server rejects, gets ``"discarded": true`` in ``progress.json`` and never a ``manifest.json``. It
stays on disk as an audit trail, is never resumed, and the next call starts a new run. A rule
that is already broken is detected when the page arrives, so no further quota is spent on a run
that can never seal. Cursors were measured valid for at least 2 hours on
2026-11-03 and can be re-used, so no expiry clock is kept; the rejected-cursor rule is the safety
net. A budget stop (``budget`` pages) or a spent daily budget leaves the run
unsealed and resumable. Nothing is estimated: a missing count means no seal.

**The key** is required before any request (Q-D) and travels in the ``Authorization`` header only.
A body that contains it is never written.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import quote, quote_plus

import httpx
import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    OPENALEX_SEARCH_DIRNAME,
    RUN_MANIFEST_FILENAME,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.capture.manifest import OpenAlexSearchRequest, OpenAlexSearchRunManifest
from prismabib.capture.openalex import (
    PROGRESS_FILENAME,
    ApiKeyInBodyError,
    _encode_line,  # pyright: ignore[reportPrivateUsage]  # one encoding rule (ADR 0031 Decision 2)
)
from prismabib.config import Settings, require_openalex_key
from prismabib.errors import UpstreamError, ValidationError
from prismabib.project import Project
from prismabib.source_mode import require_capability
from prismabib.sources.openalex import (
    INVALID_CURSOR_MESSAGE,
    PAGES_PER_DAY,
    SEARCH_PAGE_PRICE_USD,
    SEARCH_PAGE_SIZE,
    SEARCH_SELECT,
    DailyBudgetExhaustedError,
    OpenAlexClient,
    SearchPageResponse,
)
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

FIRST_CURSOR: Final = "*"
DISCARD_AND_RESTART: Final = "discard and restart"

Status = Literal["sealed", "already_sealed", "budget_stop", "daily_budget", "discarded"]


@dataclass(frozen=True)
class OpenAlexSearchResult:
    """What one call did.

    Attributes:
        status: ``sealed`` (this call completed and sealed the run), ``already_sealed`` (a sealed
            run for the query exists; no run, no request), ``budget_stop`` (the page budget ran
            out; resumable), ``daily_budget`` (OpenAlex's daily budget is spent; resumable after
            midnight UTC) or ``discarded`` (the run can never seal: discard and restart).
        run_id: The run touched, or the sealed run that was reused.
        pages_fetched: Pages requested and stored by this call.
        spent_usd: Cost of those pages: ``meta.cost_usd`` when present, else list price.
        manifest: The manifest of a sealed or reused run, else ``None``.
        message: A one-line reason; for ``discarded`` it contains "discard and restart".
        resets_in_seconds: For ``daily_budget``, the server's ``retry-after`` (seconds until the
            budget resets at 00:00 UTC), else ``None``.
        reason: For ``daily_budget``, the server's message (truncated), else empty.
    """

    status: Status
    run_id: str
    pages_fetched: int
    spent_usd: float
    manifest: OpenAlexSearchRunManifest | None = None
    message: str = ""
    resets_in_seconds: float | None = None
    reason: str = ""


class _Progress(BaseModel):
    """The ``progress.json`` sidecar: the plan, the cursor to send next, and the discard marker."""

    query: str
    query_key: str
    select: str
    per_page: int
    started_at: datetime
    requests: list[OpenAlexSearchRequest] = []
    next_cursor: str | None = FIRST_CURSOR
    total_count: int | None = None
    spent_usd: float = 0.0
    discarded: bool = False
    discard_reason: str | None = None
    #: Set by a daily-budget stop so the operator sees when it resets; cleared by the next page.
    stop_reason: str | None = None
    resets_in_seconds: float | None = None

    def same_plan(self, other: _Progress) -> bool:
        """Whether ``other`` asks for exactly this run (ADR 0042 Decision 4, Resume)."""
        return (self.query_key, self.select, self.per_page) == (
            other.query_key,
            other.select,
            other.per_page,
        )


def query_key(query: str) -> str:
    """The reuse key: the query with its whitespace collapsed and stripped."""
    return " ".join(query.split())


def cost_bound_usd(budget: int) -> float:
    """The most a ``budget``-page run costs at list price (for a dry run).

    OpenAlex meters 10 credits per search page against 10,000 credits a day, so a day holds
    :data:`~prismabib.sources.openalex.PAGES_PER_DAY` (1,000) pages at $0.001, whatever ``per_page``.
    """
    return budget * SEARCH_PAGE_PRICE_USD


def _save(run_dir: Path, progress: _Progress) -> None:
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / PROGRESS_FILENAME, progress.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )


def _read_progress(run_dir: Path) -> _Progress | None:
    try:
        return _Progress.model_validate_json(
            (run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def _run_dirs(search_dir: Path) -> list[Path]:
    return sorted(p for p in search_dir.iterdir() if p.is_dir()) if search_dir.is_dir() else []


def _sealed_for(search_dir: Path, key: str) -> OpenAlexSearchRunManifest | None:
    """The earliest sealed run of this query, if any.

    Run ids minted in the same second order randomly (the suffix is random), so no logic may
    depend on which of two same-second runs comes first. A manifest that cannot be read is
    skipped with a log line and never treated as a seal.
    """
    for run_dir in _run_dirs(search_dir):
        if not is_sealed(run_dir):
            continue
        try:
            manifest = OpenAlexSearchRunManifest.model_validate_json(
                (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
        except (OSError, ValueError):  # pydantic's ValidationError is a ValueError
            logger.warning("openalex_search.unreadable_manifest_skipped", run_dir=str(run_dir))
            continue
        if manifest.query_key == key:
            return manifest
    return None


def _find_resumable(search_dir: Path, plan: _Progress) -> Path | None:
    """The latest unsealed, not-discarded run whose plan equals ``plan``.

    "Latest" is by run id; same-second ids order randomly, so nothing may depend on it.
    """
    found = [
        d
        for d in _run_dirs(search_dir)
        if not is_sealed(d)
        and (state := _read_progress(d)) is not None
        and not state.discarded
        and state.same_plan(plan)
    ]
    return found[-1] if found else None


def _torn_page(run_dir: Path, requests: list[OpenAlexSearchRequest]) -> str | None:
    """The first recorded page whose file is missing or does not match its recorded digest."""
    for request in requests:
        try:
            data = (run_dir / request.file).read_bytes()
        except OSError:
            return request.file
        if hashlib.sha256(data).hexdigest() != request.sha256:
            return request.file
    return None


def _discard(run_dir: Path, progress: _Progress, reason: str) -> None:
    progress.discarded = True
    progress.discard_reason = reason
    _save(run_dir, progress)
    logger.warning("openalex_search.run_discarded", run_id=run_dir.name, reason=reason)


def _is_invalid_cursor(page: SearchPageResponse, cursor: str) -> bool:
    """A rejected resume cursor: HTTP 400 with the exact message (live probe, 2026-11-03)."""
    return (
        page.status == 400
        and cursor != FIRST_CURSOR
        and INVALID_CURSOR_MESSAGE in page.body.decode("utf-8", errors="replace")
    )


def _carries_key(body: bytes, secrets: list[bytes]) -> bool:
    """Whether ``body`` holds the key in any stored form, raw or after JSON decoding."""
    if any(s in body for s in secrets):
        return True
    try:
        decoded = json.dumps(json.loads(body), ensure_ascii=False).encode("utf-8")
    except ValueError:
        return False
    return any(s in decoded for s in secrets)


def _parse_page(body: bytes) -> tuple[int | None, int | None, str | None, float | None]:
    """``(result_count, meta.count, meta.next_cursor, meta.cost_usd)`` of a 200 body."""
    try:
        parsed: Any = json.loads(body)
    except ValueError:
        return None, None, None, None
    if not isinstance(parsed, dict):
        return None, None, None, None
    results = parsed.get("results")
    meta = parsed.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    count = meta.get("count")
    cursor = meta.get("next_cursor")
    cost = meta.get("cost_usd")
    return (
        len(results) if isinstance(results, list) else None,
        count if isinstance(count, int) and not isinstance(count, bool) else None,
        cursor if isinstance(cursor, str) and cursor else None,
        float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
    )


def _check_seal_rules(progress: _Progress) -> str | None:
    """Why the run cannot seal (rules 2 and 3), or ``None`` when it can."""
    total = progress.total_count
    if total is None:
        return "OpenAlex gave no meta.count on the first page, so the run cannot be sealed"
    pages = progress.requests
    if any(r.http_status != 200 for r in pages):
        return "a page was not a 200"
    if any(r.meta_count != total for r in pages):
        return f"meta.count changed from {total} during the run"
    captured = sum(r.result_count or 0 for r in pages)
    if captured != total:
        return f"{captured} works captured but meta.count says {total}"
    return None


def _seal(
    run_dir: Path, progress: _Progress, *, criteria_version: str
) -> OpenAlexSearchRunManifest:
    manifest = OpenAlexSearchRunManifest(
        run_id=run_dir.name,
        started_at=progress.started_at,
        finished_at=datetime.now(UTC),
        endpoint=OpenAlexClient.ENDPOINT,
        client_version=_CLIENT_VERSION,
        criteria_version=criteria_version,
        query=progress.query,
        query_key=progress.query_key,
        select=progress.select,
        per_page=progress.per_page,
        total_count=progress.total_count,
        works_captured=sum(r.result_count or 0 for r in progress.requests),
        complete=True,
        api_key_used=True,
        requests=progress.requests,
    )
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / RUN_MANIFEST_FILENAME, manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )
    (run_dir / PROGRESS_FILENAME).unlink(missing_ok=True)
    return manifest


def capture_openalex_search(
    project: Project,
    *,
    query: str,
    budget: int,
    refresh: bool = False,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> OpenAlexSearchResult:
    """Run, resume or reuse one OpenAlex search under ``raw/openalex-search/``.

    Args:
        project: The project; everything is written under ``project.raw_dir``.
        query: The already-built OpenAlex ``search`` string, sent unchanged.
        budget: The most pages (HTTP requests) this call may make; at least 1.
        refresh: Start a new run even if a sealed run of the same query exists.
        settings: Environment settings (the API key); defaults to ``Settings()``.
        transport: An ``httpx`` transport, for tests.
        rate_limiter: An injectable limiter, for tests.
        sleep: How retries wait; replaced in tests.

    Returns:
        An :class:`OpenAlexSearchResult`.

    Raises:
        ConfigError: If the sources mode does not allow OpenAlex search, or the key is not set.
        ValidationError: If ``query`` is blank or ``budget`` is below 1.
        ApiKeyInBodyError: If a response body contains the key; it is not written.
        AuthError: On HTTP 401 or 403; the run stays resumable.
        UpstreamError: On an unexpected status for the first page, or a 5xx past the retries.
        RateLimitError: On a per-second 429 that outlasts the retries; the run stays resumable.
    """
    require_capability(project, "openalex_search")
    resolved = settings if settings is not None else Settings()
    key = require_openalex_key(resolved)
    if not query.strip():
        raise ValidationError("the OpenAlex search query is blank.")
    if budget < 1:
        raise ValidationError("--budget must be at least 1 page.")

    search_dir = project.raw_dir / OPENALEX_SEARCH_DIRNAME
    plan = _Progress(
        query=query,
        query_key=query_key(query),
        select=SEARCH_SELECT,
        per_page=SEARCH_PAGE_SIZE,
        started_at=datetime.now(UTC),
    )
    sealed = None if refresh else _sealed_for(search_dir, plan.query_key)
    if sealed is not None:
        return OpenAlexSearchResult(
            "already_sealed", sealed.run_id, 0, 0.0, sealed, "a sealed run of this query exists"
        )

    resumed = _find_resumable(search_dir, plan)
    if resumed is not None:
        run_dir = resumed
        progress = _read_progress(run_dir) or plan
        # A file written but not recorded (a crash between the two) is asked for again.
        for orphan in run_dir.glob("response-*.jsonl"):
            if int(orphan.stem.removeprefix("response-")) >= len(progress.requests):
                orphan.unlink()
        logger.info(
            "openalex_search.run_resumed", run_id=run_dir.name, pages=len(progress.requests)
        )
    else:
        run_dir = search_dir / new_run_id()
        run_dir.mkdir(parents=True)
        progress = plan
        _save(run_dir, progress)
        logger.info("openalex_search.run_started", run_id=run_dir.name)

    secret = key.get_secret_value()
    # Raw, percent-encoded (both spaces as + and as %20) and JSON-escaped: a body may echo the key
    # in any of them; _carries_key also checks the body after JSON decoding (\uXXXX escapes).
    forms = {
        secret,
        quote_plus(secret, safe=""),
        quote(secret, safe=""),
        json.dumps(secret)[1:-1],
    }
    secrets = [f.encode("utf-8") for f in forms if f]
    fetched = 0
    spent = 0.0

    def result(
        status: Status,
        message: str,
        manifest: OpenAlexSearchRunManifest | None = None,
        *,
        resets_in_seconds: float | None = None,
        reason: str = "",
    ) -> OpenAlexSearchResult:
        return OpenAlexSearchResult(
            status, run_dir.name, fetched, spent, manifest, message, resets_in_seconds, reason
        )

    if resumed is not None and (torn := _torn_page(run_dir, progress.requests)) is not None:
        reason = f"page file {torn} does not match its recorded digest"
        _discard(run_dir, progress, reason)
        return result("discarded", f"{reason}: {DISCARD_AND_RESTART}")

    http = httpx.Client(transport=transport, timeout=30.0)
    try:
        with OpenAlexClient(
            resolved, http_client=http, rate_limiter=rate_limiter, cache=None, sleep=sleep
        ) as client:
            while progress.next_cursor is not None and fetched < budget:
                cursor = progress.next_cursor
                index = len(progress.requests)
                try:
                    page = client.fetch_search_page(plan.query, cursor, select=progress.select)
                except DailyBudgetExhaustedError as exc:
                    progress.stop_reason = exc.reason or "HTTP 429"
                    progress.resets_in_seconds = exc.resets_in_seconds
                    _save(run_dir, progress)
                    return result(
                        "daily_budget",
                        f"OpenAlex's daily budget ({PAGES_PER_DAY} search pages) is spent; "
                        "resume after 00:00 UTC",
                        resets_in_seconds=exc.resets_in_seconds,
                        reason=progress.stop_reason,
                    )
                progress.stop_reason = progress.resets_in_seconds = None
                if page.status != 200 and not _is_invalid_cursor(page, cursor):
                    # Nothing stored: the run stays as it was and can be resumed.
                    raise UpstreamError(f"OpenAlex answered HTTP {page.status} to {cursor!r}.")
                name = f"response-{index:04d}.jsonl"
                if _carries_key(page.body, secrets):
                    raise ApiKeyInBodyError(
                        f"The response for {name} contains the OpenAlex API key; it was not "
                        "written and the run stays unsealed (ADR 0031 Decision 6)."
                    )
                line, encoding, raw_sha256 = _encode_line(page.body)
                guard_writable(run_dir)
                atomic_write_bytes(run_dir / name, line)
                count_in_page, meta_count, next_cursor, cost = _parse_page(page.body)
                progress.requests.append(
                    OpenAlexSearchRequest(
                        index=index,
                        file=name,
                        cursor_sent=cursor,
                        sha256=hashlib.sha256(line).hexdigest(),
                        encoding=encoding,
                        raw_sha256=raw_sha256,
                        http_status=page.status,
                        retrieved_at=datetime.now(UTC),
                        result_count=count_in_page,
                        meta_count=meta_count,
                        next_cursor=next_cursor,
                    )
                )
                fetched += 1
                spent += cost if cost is not None else SEARCH_PAGE_PRICE_USD
                progress.spent_usd += cost if cost is not None else SEARCH_PAGE_PRICE_USD
                if page.status != 200:
                    reason = "OpenAlex rejected the stored cursor (HTTP 400, Invalid cursor value)"
                    _discard(run_dir, progress, reason)
                    return result("discarded", f"{reason}: {DISCARD_AND_RESTART}")
                if index == 0:
                    progress.total_count = meta_count
                progress.next_cursor = next_cursor
                broken = (
                    None
                    if progress.total_count is not None and meta_count == progress.total_count
                    else _check_seal_rules(progress)
                )
                if broken is not None:
                    _discard(run_dir, progress, broken)
                    return result("discarded", f"{broken}: {DISCARD_AND_RESTART}")
                _save(run_dir, progress)
    finally:
        http.close()

    if progress.next_cursor is not None:
        return result("budget_stop", f"page budget of {budget} used; the next call resumes")
    broken = _check_seal_rules(progress)
    if broken is None and (torn := _torn_page(run_dir, progress.requests)) is not None:
        broken = f"page file {torn} does not match its recorded digest"
    if broken is not None:
        _discard(run_dir, progress, broken)
        return result("discarded", f"{broken}: {DISCARD_AND_RESTART}")
    manifest = _seal(run_dir, progress, criteria_version=project.criteria.version)
    logger.info("openalex_search.run_sealed", run_id=run_dir.name, works=manifest.works_captured)
    return result("sealed", "complete", manifest)


__all__ = [
    "DISCARD_AND_RESTART",
    "FIRST_CURSOR",
    "OpenAlexSearchResult",
    "capture_openalex_search",
    "cost_bound_usd",
    "query_key",
]
