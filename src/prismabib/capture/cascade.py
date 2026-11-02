"""The Scopus level 2 and 3 cascade Layer 0 run (ADR 0041 Decision 6).

:func:`capture_scopus_cascade` asks :class:`~prismabib.sources.scopus.ScopusClient` for
``view=STANDARD`` Search by DOI (level 2, batched) and ``view=META`` Abstract Retrieval by
Scopus id (level 3, one per request) and writes
``raw/scopus-cascade/<run_id>/response-NNNN.jsonl``, one file per stored HTTP response,
plus ``progress.json`` while the run is open and ``manifest.json`` (the seal) at the end.

The bytes are caught as in :mod:`prismabib.capture.openalex`: an ``httpx`` response hook
persists each final response (200, 401, 403, and a META 404) before the client parses it.
A 429 or 5xx the client retries is not stored.

**Opt-in, budget, quota.** Every request in this run kind needs ``fallback_opt_in=True``;
without it the call raises before any request or directory, and :func:`_seal` refuses a
run that lacks it. ``budget_requests`` caps the HTTP responses received (checked before
each request, so a retry can overshoot by a few); a weekly-quota 429 stops the run. Both
seal with ``stop_reason`` ``budget`` or ``quota``; unattempted identifiers have no outcome.

**Not here (later PRs).** Resuming an unsealed run and the "already held" reuse rule: an
interrupted run leaves ``progress.json`` and is simply abandoned.

**The key.** A body containing the API key or institution token is never written
(:class:`~prismabib.capture.openalex.ApiKeyInBodyError`); the manifest holds a boolean.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import quote_plus

import httpx
import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    RUN_MANIFEST_FILENAME,
    SCOPUS_CASCADE_DIRNAME,
    atomic_write_bytes,
    guard_writable,
    new_run_id,
)
from prismabib.capture.manifest import (
    CascadeIdentifierOutcome,
    CascadeRequest,
    CascadeRunManifest,
)
from prismabib.capture.openalex import ApiKeyInBodyError, _encode_line
from prismabib.capture.writer import payload_sha256
from prismabib.config import Settings
from prismabib.errors import QuotaExceededError, ValidationError
from prismabib.project import Project
from prismabib.sources.openalex import normalise_doi
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import _EID_RE, ScopusClient, _as_list

logger = structlog.get_logger(__name__)

PROGRESS_FILENAME: Final = "progress.json"
ENDPOINT: Final = "https://api.elsevier.com/content"
_STORED_STATUSES: Final = frozenset({200, 401, 403, 404})
_Outcome = Literal["value", "absent", "not_found", "refused"]


class _Progress(BaseModel):
    """The ``progress.json`` sidecar; deleted on seal, never hashed."""

    fallback_opt_in: bool
    budget_requests: int
    started_at: datetime
    requests: list[CascadeRequest] = []
    #: Every HTTP response received, stored or not (the budget counts these).
    responses_seen: int = 0
    stop_reason: Literal["complete", "budget", "quota"] = "complete"


def _save(run_dir: Path, progress: _Progress) -> None:
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / PROGRESS_FILENAME, progress.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )


def _entry_ref(entry: Mapping[str, Any]) -> str | None:
    """The ``payload_ref`` id of a STANDARD entry: bare Scopus id, else ``doi:<doi>``."""
    ident = entry.get("dc:identifier")
    if isinstance(ident, str) and ident.startswith("SCOPUS_ID:"):
        return ident.removeprefix("SCOPUS_ID:")
    doi = entry.get("prism:doi")
    return f"doi:{normalise_doi(doi)}" if isinstance(doi, str) else None


def _count(affiliation: Any) -> int:
    return sum(isinstance(a, Mapping) for a in _as_list(affiliation))


def _outcome(
    identifier: str, status: int, outcome: _Outcome, n: int = 0
) -> CascadeIdentifierOutcome:
    return CascadeIdentifierOutcome(
        identifier=identifier, outcome=outcome, http_status=status, affiliations=n
    )


def _outcomes(
    view: str, identifiers: list[str], status: int, body: bytes, run_name: str
) -> list[CascadeIdentifierOutcome]:
    """Per-identifier outcomes of one stored response (ADR 0041 Decision 6).

    Raises:
        ValidationError: If two entries of one STANDARD response carry the same id.
    """
    if status in (401, 403):
        return [_outcome(i, status, "refused") for i in identifiers]
    if status == 404:
        return [_outcome(i, status, "not_found") for i in identifiers]
    try:
        parsed = json.loads(body)
    except ValueError:
        return []  # malformed: the client raises next; Layer 1 records it (ADR 0012)
    if not isinstance(parsed, dict):
        return []
    if view == "META":
        record = parsed.get("abstracts-retrieval-response")
        if not isinstance(record, Mapping):
            return []
        n = _count(record.get("affiliation"))
        return [_outcome(i, status, "value" if n else "absent", n) for i in identifiers]
    results = parsed.get("search-results")
    entries = (
        [e for e in _as_list(results.get("entry")) if isinstance(e, Mapping)]
        if isinstance(results, Mapping)
        else []
    )
    refs = [r for e in entries if (r := _entry_ref(e)) is not None]
    if len(refs) != len(set(refs)):
        dup = next(r for r in refs if refs.count(r) > 1)
        raise ValidationError(f"Two entries of {run_name} carry the same id {dup!r}; not written.")
    by_doi = {
        normalise_doi(e["prism:doi"]): e for e in entries if isinstance(e.get("prism:doi"), str)
    }
    out = []
    for i in identifiers:
        entry = by_doi.get(i)
        if entry is None:
            out.append(_outcome(i, status, "not_found"))
            continue
        n = _count(entry.get("affiliation"))
        out.append(_outcome(i, status, "value" if n else "absent", n))
    return out


class _Recorder:
    """The ``httpx`` response hook that persists each response before the client parses it."""

    def __init__(self, run_dir: Path, progress: _Progress, *, secrets: list[bytes]) -> None:
        self._run_dir = run_dir
        self.progress = progress
        self._secrets = secrets
        #: What the current client call asked for; set by the loop before each call.
        self.view: Literal["STANDARD", "META"] = "STANDARD"
        self.identifiers: list[str] = []

    def __call__(self, response: httpx.Response) -> None:
        progress = self.progress
        progress.responses_seen += 1
        status = response.status_code
        # A STANDARD 404 names no record (a proxy or gateway fault); the client retries it.
        if status not in _STORED_STATUSES or (status == 404 and self.view == "STANDARD"):
            return
        response.read()
        body = response.content
        index = len(progress.requests)
        name = f"response-{index:04d}.jsonl"
        if any(secret in body for secret in self._secrets):
            raise ApiKeyInBodyError(
                f"The response for {name} contains the Scopus key or token; it was not "
                "written and the run stays unsealed (ADR 0041 Decision 6)."
            )
        outcomes = _outcomes(self.view, self.identifiers, status, body, name)
        line, encoding, raw_sha256 = _encode_line(body)
        guard_writable(self._run_dir)
        atomic_write_bytes(self._run_dir / name, line)
        remaining = response.headers.get("X-RateLimit-Remaining", "")
        progress.requests.append(
            CascadeRequest(
                index=index,
                file=name,
                sha256=sha256(line).hexdigest(),
                encoding=encoding,
                raw_sha256=raw_sha256,
                view=self.view,
                identifiers=self.identifiers,
                http_status=status,
                retrieved_at=datetime.now(UTC),
                quota_remaining=int(remaining) if remaining.isdigit() else None,
                outcomes=outcomes,
            )
        )
        _save(self._run_dir, progress)


def _seal(
    run_dir: Path, progress: _Progress, *, api_key_used: bool, criteria_version: str
) -> CascadeRunManifest:
    """Write ``manifest.json`` and drop the sidecar; refuses a run without opt-in.

    Raises:
        ValidationError: If ``fallback_opt_in`` is false (ADR 0041 Decision 6).
    """
    if not progress.fallback_opt_in:
        raise ValidationError(
            f"Refusing to seal {run_dir.name}: a cascade run needs fallback_opt_in "
            "(ADR 0041 Decision 6)."
        )
    files = [r.file for r in progress.requests]
    manifest = CascadeRunManifest(
        run_id=run_dir.name,
        started_at=progress.started_at,
        finished_at=datetime.now(UTC),
        endpoint=ENDPOINT,
        client_version=_CLIENT_VERSION,
        criteria_version=criteria_version,
        fallback_opt_in=progress.fallback_opt_in,
        api_key_used=api_key_used,
        budget_requests=progress.budget_requests,
        requests_made=progress.responses_seen,
        stop_reason=progress.stop_reason,
        requests=progress.requests,
        payload_files=files,
        payload_sha256=payload_sha256(run_dir, files),
    )
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / RUN_MANIFEST_FILENAME, manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )
    (run_dir / PROGRESS_FILENAME).unlink(missing_ok=True)
    return manifest


def capture_scopus_cascade(
    project: Project,
    *,
    dois: Iterable[str] = (),
    scopus_ids: Iterable[str] = (),
    fallback_opt_in: bool,
    budget_requests: int,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
) -> CascadeRunManifest | None:
    """Run one cascade acquisition and seal it under ``raw/scopus-cascade/``.

    Args:
        project: The project; everything is written under ``project.raw_dir``.
        dois: DOIs for level 2 (``STANDARD``, batched); normalised, de-duplicated.
        scopus_ids: Scopus ids for level 3 (``META``, one request each); a
            ``scopus:`` prefix or ``2-s2.0-`` is accepted and stripped.
        fallback_opt_in: Must be true; recorded in the manifest.
        budget_requests: Cap on HTTP responses received (positive); approximate, since
            retried 429s inside one unit can overshoot it by up to ``MAX_ATTEMPTS - 1``.
        settings: Environment settings (the API key); defaults to ``Settings()``.
        transport: An ``httpx`` transport, for tests.
        rate_limiter: An injectable limiter, for tests.

    Returns:
        The sealed run's manifest, or ``None`` when there is nothing to ask for.

    Raises:
        ValidationError: Without opt-in, with a non-positive budget or a bad DOI or Scopus
            id (no request, no directory); on duplicate ids in one response (the run stays unsealed).
        ApiKeyInBodyError: If a response body contains the key or token.
    """
    if not fallback_opt_in:
        raise ValidationError("A cascade run needs fallback_opt_in (ADR 0041 Decision 6).")
    if budget_requests < 1:
        raise ValidationError("budget_requests must be at least 1 (ADR 0041 Decision 6).")
    doi_plan = list(dict.fromkeys(normalise_doi(d) for d in dois))
    id_plan = list(
        dict.fromkeys(i.removeprefix("scopus:").removeprefix("2-s2.0-") for i in scopus_ids)
    )
    # Reject what the client would reject mid-run, before any request or directory.
    if bad := next((d for d in doi_plan if '"' in d), None):
        raise ValidationError(f"DOI {bad!r} contains a double quote and cannot be queried.")
    if bad := next((i for i in id_plan if not _EID_RE.fullmatch(f"2-s2.0-{i}")), None):
        raise ValidationError(
            f"Scopus id {bad!r} is not numeric (expected digits or 2-s2.0-<digits>)."
        )
    if not doi_plan and not id_plan:
        logger.info("scopus_cascade.capture.nothing_to_request")
        return None

    resolved = settings if settings is not None else Settings()
    tokens = [resolved.scopus_api_key, resolved.scopus_insttoken]
    values = [t.get_secret_value() for t in tokens if t is not None]
    forms = {f for v in values for f in (v, quote_plus(v, safe=""), json.dumps(v)[1:-1])}
    secrets = [s.encode("utf-8") for s in forms if s]

    run_dir = project.raw_dir / SCOPUS_CASCADE_DIRNAME / new_run_id()
    run_dir.mkdir(parents=True)
    progress = _Progress(
        fallback_opt_in=fallback_opt_in,
        budget_requests=budget_requests,
        started_at=datetime.now(UTC),
    )
    _save(run_dir, progress)
    logger.info("scopus_cascade.capture.run_started", run_id=run_dir.name)

    recorder = _Recorder(run_dir, progress, secrets=secrets)
    size = ScopusClient.PAGE_SIZE
    units: list[tuple[Literal["STANDARD", "META"], list[str]]] = [
        ("STANDARD", doi_plan[i : i + size]) for i in range(0, len(doi_plan), size)
    ] + [("META", [i]) for i in id_plan]
    http = httpx.Client(transport=transport, timeout=30.0, event_hooks={"response": [recorder]})
    try:
        with ScopusClient(resolved, http_client=http, rate_limiter=rate_limiter) as client:
            for view, identifiers in units:
                if progress.responses_seen >= budget_requests:
                    progress.stop_reason = "budget"
                    break
                recorder.view, recorder.identifiers = view, identifiers
                try:
                    if view == "STANDARD":
                        client.search_standard_by_dois(identifiers, fallback_opt_in=True)
                    else:
                        client.abstract_meta(f"2-s2.0-{identifiers[0]}", fallback_opt_in=True)
                except QuotaExceededError:
                    progress.stop_reason = "quota"
                    break
    finally:
        http.close()

    manifest = _seal(
        run_dir, progress, api_key_used=bool(values), criteria_version=project.criteria.version
    )
    logger.info(
        "scopus_cascade.capture.run_sealed",
        run_id=run_dir.name,
        responses=len(manifest.requests),
        stop_reason=manifest.stop_reason,
    )
    return manifest


__all__ = ["PROGRESS_FILENAME", "capture_scopus_cascade"]
