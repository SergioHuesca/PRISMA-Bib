"""The OpenAlex Layer 0 run: persist every response, then seal (ADR 0031).

:func:`capture_openalex` asks :class:`~prismabib.sources.openalex.OpenAlexClient`
for a set of DOIs and ``(title, year)`` pairs and writes
``raw/openalex/<run_id>/response-NNNN.jsonl``, one file per HTTP response, plus
``progress.json`` while the run is open and ``manifest.json`` (the seal) at the end.

**How the bytes are caught.** The client returns parsed works, never bytes. This
module therefore hands the client an ``httpx.Client`` with a *response event hook*
(:class:`_Recorder`): the hook sees every final response (200, 401, 403, 404; a
429/5xx that the client retries is not a stored response), persists it, and records
its manifest entry, all before the client parses anything. A 401/403 outside an
opt-in run is written first and then the client raises, as ADR 0031 Decision 5
requires. The client's return values are never used.

**Sealing and resumption.** As in :mod:`prismabib.capture.writer`, ``manifest.json``
is the seal. An unsealed run resumes only when its plan (identifiers in order,
``fallback_opt_in`` and ``refresh``) is identical and it holds no refusal.
Resumption is by *unit* (a DOI batch, a single DOI, or one title search), so a
finished unit is never requested again. A unit interrupted part-way (a batch 404
whose single lookups did not all finish) is asked again and its orphaned files are
rewritten; ``progress.json`` never named them.

**The key.** The manifest holds ``api_key_used`` and ``params`` without
``api_key``. A body that contains the key (raw or percent-encoded) is never
written: the run stays unsealed and the error names the file only.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import quote_plus

import httpx
import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    OPENALEX_DIRNAME,
    RUN_MANIFEST_FILENAME,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.capture.manifest import (
    OpenAlexEntitlementContext,
    OpenAlexOutcome,
    OpenAlexRequest,
    OpenAlexRunManifest,
    OpenAlexTitle,
)
from prismabib.capture.writer import _payload_sha256
from prismabib.config import Settings
from prismabib.errors import ValidationError
from prismabib.project import Project
from prismabib.sources.openalex import (
    OpenAlexClient,
    OpenAlexWork,
    field_outcomes,
    normalise_doi,
    normalise_title,
)
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

PROGRESS_FILENAME: Final = "progress.json"
#: Responses that are data. A 429/5xx is retried by the client and is not stored.
_STORED_STATUSES: Final = frozenset({200, 401, 403, 404})
_WORKS_PATH: Final = "/works"

#: One request unit: a DOI chunk (a batch, or a single DOI that cannot be batched)
#: or one title search.
_Unit = list[str] | OpenAlexTitle


class ApiKeyInBodyError(ValidationError):
    """A response body contains the API key; it was not written (ADR 0031 Decision 6)."""


class _Refusal(BaseModel):
    index: int
    status: int


class _Progress(BaseModel):
    """The ``progress.json`` resumption sidecar; deleted on seal, never hashed."""

    dois: list[str]
    titles: list[OpenAlexTitle]
    fallback_opt_in: bool
    refresh: bool
    started_at: datetime
    requests: list[OpenAlexRequest] = []
    #: Units fully answered, and how many of ``requests`` belong to them.
    units_done: int = 0
    requests_in_done_units: int = 0
    refusal: _Refusal | None = None

    def same_plan(self, other: _Progress) -> bool:
        """Whether ``other`` asks for exactly this run (ADR 0031 Decision 1)."""
        return (
            self.dois == other.dois
            and self.titles == other.titles
            and self.fallback_opt_in == other.fallback_opt_in
            and self.refresh == other.refresh
        )


def _save(run_dir: Path, progress: _Progress) -> None:
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / PROGRESS_FILENAME, progress.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )


def _encode_line(body: bytes) -> tuple[bytes, Literal["verbatim", "compact"], str | None]:
    """The stored line for ``body`` (ADR 0031 Decision 2): verbatim, else compact."""
    if b"\n" not in body and b"\r" not in body:
        return body + b"\n", "verbatim", None
    try:
        parsed: Any = json.loads(body)
    except ValueError:
        # Not JSON (e.g. an HTML refusal page): keep it as one JSON string so the
        # file is still one valid JSONL line; raw_sha256 digests the original.
        parsed = body.decode("utf-8", errors="replace")
    line = json.dumps(parsed, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return line + b"\n", "compact", hashlib.sha256(body).hexdigest()


def _parse_work(raw: dict[str, Any]) -> OpenAlexWork:
    # Mirrors the client's private `_parse_work`; kept local so this module
    # depends only on the client's public interface.
    year = raw.get("publication_year")
    doi = raw.get("doi")
    return OpenAlexWork(
        openalex_id=raw.get("id"),
        doi=normalise_doi(doi) if isinstance(doi, str) else None,
        title=raw.get("title"),
        publication_year=year if isinstance(year, int) else None,
        abstract_inverted_index=raw.get("abstract_inverted_index") or None,
        authorships=raw.get("authorships") or (),
        topics=raw.get("topics") or (),
    )


def _found(identifier: str | OpenAlexTitle, status: int, raw: dict[str, Any]) -> OpenAlexOutcome:
    absent = [n for n, o in field_outcomes(_parse_work(raw)).items() if o.outcome == "absent"]
    oid = raw.get("id")
    return OpenAlexOutcome(
        identifier=identifier,
        outcome="value",
        http_status=status,
        openalex_id=oid if isinstance(oid, str) else None,
        absent_fields=absent,
    )


def _missing(identifier: str | OpenAlexTitle, status: int) -> OpenAlexOutcome:
    return OpenAlexOutcome(identifier=identifier, outcome="not_found", http_status=status)


def _outcomes(
    lookup: str,
    identifiers: list[str] | list[OpenAlexTitle],
    status: int,
    body: bytes,
) -> tuple[list[OpenAlexOutcome], int | None]:
    """The per-identifier outcomes and the ``results`` count of one response."""
    ids: list[str | OpenAlexTitle] = list(identifiers)
    if status in (401, 403):
        return [
            OpenAlexOutcome(identifier=i, outcome="refused", http_status=status) for i in ids
        ], None
    if status == 404:
        # A batch 404 says nothing about any one DOI; the singles that follow do.
        return ([] if lookup == "doi_batch" else [_missing(i, status) for i in ids]), None
    try:
        parsed = json.loads(body)
    except ValueError:
        return [], None  # malformed: the client raises next; Layer 1 records it (ADR 0012)
    if lookup == "doi_single":
        if not isinstance(parsed, dict):
            return [], None
        work = _parse_work(parsed)
        hit = work.doi == ids[0]
        return [_found(ids[0], status, parsed) if hit else _missing(ids[0], status)], None
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        return [], None
    works = [r for r in results if isinstance(r, dict)]
    out: list[OpenAlexOutcome] = []
    if lookup == "doi_batch":
        by_doi = {w["doi"]: w for w in works if isinstance(w.get("doi"), str)}
        by_doi = {normalise_doi(k): v for k, v in by_doi.items()}
        for i in ids:
            hit_work = by_doi.get(str(i))
            out.append(_found(i, status, hit_work) if hit_work else _missing(i, status))
        return out, len(results)
    for i in ids:
        assert isinstance(i, OpenAlexTitle)
        wanted = normalise_title(i.title)
        match = next(
            (
                w
                for w in works
                if isinstance(w.get("title"), str)
                and w.get("publication_year") == i.year
                and normalise_title(w["title"]) == wanted
            ),
            None,
        )
        out.append(_found(i, status, match) if match else _missing(i, status))
    return out, len(results)


class _Recorder:
    """The ``httpx`` response hook that persists each response before parsing."""

    def __init__(
        self, run_dir: Path, progress: _Progress, *, secrets: list[bytes], api_key_used: bool
    ) -> None:
        self._run_dir = run_dir
        self.progress = progress
        self._secrets = secrets
        self._api_key_used = api_key_used

    def __call__(self, response: httpx.Response) -> None:
        status = response.status_code
        if status not in _STORED_STATUSES:
            return
        response.read()
        body = response.content
        progress = self.progress
        index = len(progress.requests)
        name = f"response-{index:04d}.jsonl"
        if any(secret in body for secret in self._secrets):
            raise ApiKeyInBodyError(
                f"The response for {name} contains the OpenAlex API key; it was not "
                "written and the run stays unsealed (ADR 0031 Decision 6)."
            )
        line, encoding, raw_sha256 = _encode_line(body)
        guard_writable(self._run_dir)
        atomic_write_bytes(self._run_dir / name, line)

        url = response.request.url
        params = {k: v for k, v in url.params.items() if k != "api_key"}
        identifiers: list[str] | list[OpenAlexTitle]
        if url.path.rstrip("/") != _WORKS_PATH:
            lookup: Literal["doi_batch", "doi_single", "title_search"] = "doi_single"
            identifiers = [normalise_doi(url.path.removeprefix(f"{_WORKS_PATH}/doi:"))]
        elif "search" in params:
            lookup = "title_search"
            year = int(params.get("filter", "").removeprefix("publication_year:"))
            identifiers = [OpenAlexTitle(title=params["search"], year=year)]
        else:
            lookup = "doi_batch"
            identifiers = params.get("filter", "").removeprefix("doi:").split("|")
        outcomes, result_count = _outcomes(lookup, identifiers, status, body)
        refusal = status if status in (401, 403) else None
        progress.requests.append(
            OpenAlexRequest(
                index=index,
                file=name,
                sha256=hashlib.sha256(line).hexdigest(),
                encoding=encoding,
                raw_sha256=raw_sha256,
                lookup=lookup,
                identifiers=identifiers,
                params=params,
                api_view=params.get("select", ""),
                entitlement_context=OpenAlexEntitlementContext(
                    api_key_used=self._api_key_used,
                    fallback_opt_in=progress.fallback_opt_in,
                    refusal_status=refusal,  # type: ignore[arg-type]
                ),
                http_status=status,
                retrieved_at=datetime.now(UTC),
                result_count=result_count,
                outcomes=outcomes,
            )
        )
        if refusal is not None and not progress.fallback_opt_in:
            progress.refusal = _Refusal(index=index, status=refusal)
        _save(self._run_dir, progress)


def _units(dois: list[str], titles: list[OpenAlexTitle]) -> list[_Unit]:
    """Batches of DOIs, then un-batchable DOIs singly, then one unit per title."""
    singles = [d for d in dois if "|" in d or "," in d]
    batchable = [d for d in dois if d not in singles]
    size = OpenAlexClient.BATCH_SIZE
    units: list[_Unit] = [batchable[i : i + size] for i in range(0, len(batchable), size)]
    units += [[d] for d in singles]
    units += list(titles)
    return units


def _held(openalex_dir: Path) -> set[str | tuple[str, int]]:
    """Identifiers a sealed run already holds as ``value`` or ``not_found`` (Decision 7)."""
    held: set[str | tuple[str, int]] = set()
    if not openalex_dir.is_dir():
        return held
    for run_dir in openalex_dir.iterdir():
        if not run_dir.is_dir() or not is_sealed(run_dir):
            continue
        manifest = OpenAlexRunManifest.model_validate_json(
            (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        for request in manifest.requests:
            for o in request.outcomes:
                if o.outcome == "refused":
                    continue
                i = o.identifier
                held.add(i if isinstance(i, str) else (normalise_title(i.title), i.year))
    return held


def _find_resumable(openalex_dir: Path, plan: _Progress) -> Path | None:
    """The latest unsealed, refusal-free run whose plan equals ``plan``."""
    if not openalex_dir.is_dir():
        return None
    found: list[Path] = []
    for entry in openalex_dir.iterdir():
        sidecar = entry / PROGRESS_FILENAME
        if not entry.is_dir() or is_sealed(entry) or not sidecar.is_file():
            continue
        try:
            state = _Progress.model_validate_json(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable sidecar: left alone, not a candidate
        if state.refusal is None and state.same_plan(plan):
            found.append(entry)
    return max(found, key=lambda p: p.name) if found else None


def _seal(
    run_dir: Path, progress: _Progress, *, api_key_used: bool, criteria_version: str
) -> OpenAlexRunManifest:
    """Write ``manifest.json`` and drop the sidecar; refuses an unsealable run.

    Raises:
        ValidationError: If ``fallback_opt_in`` is false and any response is not
            200 or 404 (ADR 0031 Decision 5): such a run never seals.
    """
    if not progress.fallback_opt_in:
        bad = [r.file for r in progress.requests if r.http_status not in (200, 404)]
        if bad:
            raise ValidationError(
                f"Refusing to seal {run_dir.name}: without fallback_opt_in, {bad} "
                "are not 200 or 404 (ADR 0031 Decision 5)."
            )
    files = [r.file for r in progress.requests]
    manifest = OpenAlexRunManifest(
        run_id=run_dir.name,
        started_at=progress.started_at,
        finished_at=datetime.now(UTC),
        endpoint=OpenAlexClient.ENDPOINT,
        fallback_opt_in=progress.fallback_opt_in,
        api_key_used=api_key_used,
        refresh=progress.refresh,
        requests=progress.requests,
        payload_files=files,
        payload_sha256=_payload_sha256(run_dir, files),
        client_version=_CLIENT_VERSION,
        criteria_version=criteria_version,
    )
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / RUN_MANIFEST_FILENAME, manifest.model_dump_json(indent=2).encode("utf-8") + b"\n"
    )
    (run_dir / PROGRESS_FILENAME).unlink(missing_ok=True)
    return manifest


def capture_openalex(
    project: Project,
    *,
    dois: Iterable[str] = (),
    titles: Iterable[tuple[str, int]] = (),
    fallback_opt_in: bool = False,
    refresh: bool = False,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
) -> OpenAlexRunManifest | None:
    """Run (or resume) one OpenAlex acquisition and seal it under ``raw/openalex/``.

    Args:
        project: The project; everything is written under ``project.raw_dir``.
        dois: DOIs to look up; normalised and de-duplicated, order kept.
        titles: ``(title, year)`` pairs for exact-title search.
        fallback_opt_in: The ADR 0028 opt-in. Recorded in the manifest. Only then
            may a 401/403 be stored as ``refused`` and the run seal.
        refresh: Request identifiers already held by a sealed run again (off by
            default; ADR 0031 Decision 7).
        settings: Environment settings (the API key); defaults to ``Settings()``.
        transport: An ``httpx`` transport, for tests.
        rate_limiter: An injectable limiter, for tests.

    Returns:
        The sealed run's manifest, or ``None`` when every identifier is already
        held and so no run was created and no request made.

    Raises:
        AuthError: On 401 outside an opt-in run; the response is on disk and
            the run is a dead end.
        EntitlementError: On 403 outside an opt-in run; as above.
        ApiKeyInBodyError: If a response body contains the API key.
        ValidationError: On a malformed 200 body, or an unsealable run.
    """
    openalex_dir = project.raw_dir / OPENALEX_DIRNAME
    held = set() if refresh else _held(openalex_dir)
    doi_plan = [d for d in dict.fromkeys(normalise_doi(d) for d in dois) if d not in held]
    title_plan: list[OpenAlexTitle] = []
    seen: set[tuple[str, int]] = set()
    for title, year in titles:
        key = (normalise_title(title), year)
        if key not in held and key not in seen:
            seen.add(key)
            title_plan.append(OpenAlexTitle(title=title, year=year))
    if not doi_plan and not title_plan:
        logger.info("openalex.capture.nothing_to_request")
        return None

    resolved = settings if settings is not None else Settings()
    key_secret = resolved.openalex_api_key
    secret = key_secret.get_secret_value() if key_secret is not None else ""
    # Raw, percent-encoded, and JSON-escaped (a body may echo the key as ü).
    forms = {secret, quote_plus(secret, safe=""), json.dumps(secret)[1:-1]}
    secrets = [s.encode("utf-8") for s in forms if s]

    plan = _Progress(
        dois=doi_plan,
        titles=title_plan,
        fallback_opt_in=fallback_opt_in,
        refresh=refresh,
        started_at=datetime.now(UTC),
    )
    resumed = _find_resumable(openalex_dir, plan)
    if resumed is not None:
        run_dir = resumed
        progress = _Progress.model_validate_json(
            (run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8")
        )
        # Requests of a half-finished unit are orphans: asked again, rewritten.
        progress.requests = progress.requests[: progress.requests_in_done_units]
        logger.info(
            "openalex.capture.run_resumed", run_id=run_dir.name, units_done=progress.units_done
        )
    else:
        run_dir = openalex_dir / new_run_id()
        run_dir.mkdir(parents=True)
        progress = plan
        _save(run_dir, progress)
        logger.info("openalex.capture.run_started", run_id=run_dir.name)

    recorder = _Recorder(run_dir, progress, secrets=secrets, api_key_used=bool(secret))
    http = httpx.Client(
        transport=transport,
        timeout=30.0,
        event_hooks={"response": [recorder]},
    )
    units = _units(doi_plan, title_plan)
    try:
        with OpenAlexClient(
            resolved, http_client=http, rate_limiter=rate_limiter, cache=None
        ) as client:
            for position in range(progress.units_done, len(units)):
                unit = units[position]
                if isinstance(unit, OpenAlexTitle):
                    client.search_title(unit.title, unit.year, fallback_opt_in=fallback_opt_in)
                else:
                    client.lookup_dois(unit, fallback_opt_in=fallback_opt_in)
                progress.units_done = position + 1
                progress.requests_in_done_units = len(progress.requests)
                _save(run_dir, progress)
    finally:
        http.close()

    manifest = _seal(
        run_dir, progress, api_key_used=bool(secret), criteria_version=project.criteria.version
    )
    logger.info(
        "openalex.capture.run_sealed", run_id=run_dir.name, responses=len(manifest.requests)
    )
    return manifest


__all__ = ["PROGRESS_FILENAME", "ApiKeyInBodyError", "capture_openalex"]
