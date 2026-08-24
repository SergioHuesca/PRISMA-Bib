"""Build and read the Layer 1 normalised store (BUILD_PLAN §Stage 3, lines 841-931).

:func:`build_store` is the *one function* §2.2 (lines 104-105) requires:
"Layer 1 must be reconstructible from Layer 0 by running one function."
Nothing else in prismabib writes to ``project.db_path`` -- if a number
belongs in the store, it is derived here from ``raw/`` and ``schema.sql``,
never hand-edited into the database. :class:`Corpus` is the read-only
counterpart every later analysis stage is meant to use instead of talking
to DuckDB directly.

**Where each record comes from.** Layer 0 (:mod:`prismabib.capture.writer`)
writes one ``raw/<run_id>/page-NNNN.jsonl`` file per fetched Scopus Search
API page, true JSON Lines: **one Scopus entry object per line**, with the
page's response envelope (``opensearch:totalResults``, ``cursor``, ``link``,
...) written separately to the sibling ``page-NNNN.meta.json``, which this
module never reads (every table it populates is derived from either an
entry or ``manifest.json``, never the envelope). The run is sealed with
``raw/<run_id>/manifest.json`` once every page is written. This module
scans ``project.raw_dir`` for *sealed* run directories only
(:func:`~prismabib.capture.writer.is_sealed`), skipping the unsealed HTTP
cache directory (``raw/_cache/``, which carries no ``manifest.json`` and is
never a run), and walks each run's ``manifest.payload_files`` in fetch
order, each page's lines in file order (:func:`_iter_page_entries`). That
traversal order -- sorted ``run_id`` (sortable by construction, oldest
first), then ``payload_files`` order, then line order -- is fixed and is
what makes two runs of :func:`build_store` over identical Layer 0 input
produce byte-identical table checksums (S03-AC1): nothing here depends on
filesystem directory-listing order or dict iteration order.

**Provenance (``payload_file``/``payload_line``).** Because each
``page-NNNN.jsonl`` line *is* one record, ``records.payload_line`` is that
record's real 0-based line index in the file -- not a constant -- and
``PayloadRef(path, line).resolve()`` returns exactly that record's raw JSON
object, not the whole page. This is what makes S03-AC2 ("every
``payload_file``/``payload_line`` pair resolves to a valid raw JSON
object") a genuine provenance guarantee rather than a vacuously true one
(see ``capture/writer.py::_write_page``'s docstring for why the format
changed to this from an earlier one-line-per-page encoding).

**Deliberate scope: what this loader parses from the Scopus wire format.**
The Search API's ``view=COMPLETE`` response (the only view prismabib ever
requests, BUILD_PLAN §5 risk 1) does not carry Scopus subject-area codes or
indexed (non-author) keyword terms -- those live in the separate Abstract
Retrieval API, which Stage 2 does not call. ``index_keywords`` is therefore
always ``[]`` for every record this loader produces, and ``subject_areas``
is populated only in the (currently unobserved, but schema-supported)
case where a captured entry does carry a ``subject-area`` array; both
``keywords``/``record_keywords`` (kind ``"index"``) and ``subject_areas``
remain real, always-present tables that simply have no rows to insert
today. This is a data-source limitation, not a modelling gap: nothing here
silently drops a field that Layer 0 did capture.

**Re-captured records.** The same Scopus paper can legitimately appear in
more than one sealed run (e.g. the same query re-run later to refresh
citation counts). ``records.record_id`` is a primary key, so its row --
and every table it fans out to (``venues``, ``authors``,
``record_authors``, ``affiliations``, ``record_affiliations``,
``keywords``, ``record_keywords``, ``subject_areas``) -- is populated once,
from the run in which the record was *first* seen (per the traversal order
above). A later run's re-capture of the same paper still contributes its
own row to ``citation_snapshots`` (see below) -- that is the one piece of
information a re-capture actually carries that the first capture could not
have.

**Citation snapshots and ``retrieved_at``.** Every entry's
``citedby-count`` becomes one ``citation_snapshots`` row keyed on
``(record_id, retrieved_at)``, with ``retrieved_at`` set to that entry's
*run's* ``RunManifest.started_at`` -- never ``datetime.now()`` at load
time. This is the only choice consistent with S03-AC1 and S03-AC4: Layer 1
is derived purely from Layer 0, and Layer 0 already records exactly when
the citation count was observed (when the acquisition run that captured it
began). Stamping the load-time wall clock instead would give every
``build_store(rebuild=True)`` call a different ``retrieved_at`` -- a
different primary key on an unchanged input, which breaks both
"byte-stable checksums on the same Layer 0 input" and "loading twice does
not duplicate rows" (a second load would insert a second, distinct
snapshot row instead of being a no-op). All records captured by the same
run therefore share one ``retrieved_at`` timestamp, which is also the
methodologically honest granularity: Scopus reports every citation count
in a page as of that request, and a run's pages are fetched within seconds
of each other.

**Duplicate reporting (BUILD_PLAN critical modelling note 4).** Dedup is
reported, not applied: the frozen ``records`` schema has no duplicate-flag
column, so both records in a duplicate pair are always retained as
ordinary rows. What is *reported* is a normalised-DOI collision --
``StoreStats.duplicate_doi_groups``/``duplicate_records`` -- computed via a
``GROUP BY doi HAVING COUNT(*) > 1`` query, which is BUILD_PLAN's own
example ("a duplicate DOI keeps both records and flags them") and the case
`test_load__duplicate_doi__both_records_retained_and_flagged` names. The
richer ``(normalised_title, first_author_surname, year)`` fallback key of
BUILD_PLAN §3.2 is deliberately *not* reimplemented here in SQL: it already
has one authoritative implementation, :func:`prismabib.models.dedup_key`,
directly unit-tested by its own Stage 3 test cases
(`test_dedup_key__*`); duplicating its normalisation rules in SQL would
create a second, driftable definition of the same key for no
loader-visible benefit beyond DOI collisions.

**``PrismaStage`` before Stage 4 exists.** ``Corpus.records``/``keywords``
take a :class:`~prismabib.stage.PrismaStage`, but Stage 4's decision-log
engine -- the only thing that can compute any set past "everything
captured" -- does not exist yet. The one stage this loader can honestly
answer is :attr:`~prismabib.stage.PrismaStage.RAW` (every record in
``records``, unfiltered); every other member, including the frozen
signature's default ``PrismaStage.INCLUDED``, raises ``NotImplementedError``
naming the missing engine. This is why
``test_corpus__records_by_stage__delegates_to_prisma_engine`` is shipped
``xfail(strict=True)`` and is expected to flip in the same PR that lands
Stage 4.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd
import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict

from prismabib.capture.manifest import RunManifest
from prismabib.capture.writer import is_sealed
from prismabib.countries import normalise_country
from prismabib.errors import StoreError, ValidationError
from prismabib.models import Affiliation, Author, PayloadRef, Record, Venue, normalise_doi
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.checksums import TABLE_NAMES
from prismabib.store.db import connect

logger = structlog.get_logger(__name__)

# raw/_cache/ is the content-addressed HTTP cache HttpCache writes to
# (prismabib.capture.writer._CACHE_DIRNAME): it carries no manifest.json, is
# never sealed, and must never be mistaken for a run directory.
_CACHE_DIRNAME = "_cache"
_MANIFEST_FILENAME = "manifest.json"
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

VenueType = Literal["journal", "conference", "book", "other"]

# BUILD_PLAN modelling note 3 gives one worked example
# ("convolutional neural networks" -> "convolutional neural network") and
# calls for "a small closed list", not general English stemming -- a
# stemmer would also mangle non-plural terms ending in "s" (e.g. "means",
# "bayes"). This list is deliberately short and reviewable; extend it by
# adding entries, never by switching to a heuristic.
_KEYWORD_SINGULARISATION: dict[str, str] = {
    "convolutional neural networks": "convolutional neural network",
    "generative adversarial networks": "generative adversarial network",
    "recurrent neural networks": "recurrent neural network",
    "graph neural networks": "graph neural network",
    "support vector machines": "support vector machine",
    "long short-term memory networks": "long short-term memory network",
}

# Scopus prism:aggregationType values seen in practice, folded to the
# Venue.venue_type closed set (BUILD_PLAN schema line 861). Not part of the
# frozen spec -- a loader-level judgement call; anything unrecognised maps
# to "other" rather than raising, since a venue type is metadata, not an
# identifier a downstream join depends on.
_AGGREGATION_TYPE_TO_VENUE_TYPE: dict[str, VenueType] = {
    "journal": "journal",
    "trade journal": "journal",
    "conference proceeding": "conference",
    "conference proceedings": "conference",
    "book series": "book",
    "book": "book",
}

_PUNCTUATION_RE = re.compile(r"[^\w\s-]")
_WHITESPACE_RE = re.compile(r"\s+")


class StoreStats(BaseModel):
    """Summary counts from one :func:`build_store` call.

    Every count below is a row count in the freshly (re)built Layer 1
    store, computed by ``SELECT COUNT(*)`` (or an equivalent grouped
    query) against the table named, *after* loading -- never an in-memory
    tally kept alongside the load, so a caller reading ``StoreStats`` and a
    caller running the same query against :func:`prismabib.store.db.connect`
    always agree.
    """

    model_config = ConfigDict(frozen=True)

    rebuilt: bool
    """Whether this call actually (re)loaded Layer 0 (``True``), or reused
    an already-built store because ``rebuild=False`` and one existed
    (``False``). See :func:`build_store`."""

    runs_loaded: int
    """Rows in ``runs`` -- the number of sealed Layer 0 run directories
    folded into this store."""

    records_loaded: int
    """Rows in ``records`` -- distinct Scopus records (by ``record_id``).
    A paper re-captured by more than one run is counted once, at its
    first-seen run; see the module docstring."""

    duplicate_doi_groups: int
    """Number of distinct normalised DOIs shared by two or more rows in
    ``records`` (``GROUP BY doi HAVING COUNT(*) > 1``). Dedup is *reported*
    here, never applied: every one of those rows is still present in
    ``records``."""

    duplicate_records: int
    """Total number of ``records`` rows that belong to one of
    ``duplicate_doi_groups`` (always ``>= 2 * duplicate_doi_groups``)."""

    authors_loaded: int
    """Distinct authors in ``authors`` (by Scopus ``author_id``, used as-is
    per BUILD_PLAN modelling note 4 -- no disambiguation is performed)."""

    affiliations_loaded: int
    """Distinct institutions in ``affiliations`` (by Scopus ``afid``)."""

    venues_loaded: int
    """Distinct venues in ``venues``."""

    keywords_loaded: int
    """Distinct normalised terms in ``keywords`` (deduplicated by
    ``term_norm`` across both author and index keywords, and across every
    record)."""

    record_keyword_links_loaded: int
    """Rows in ``record_keywords`` -- one per (record, keyword, kind)
    occurrence."""

    subject_area_links_loaded: int
    """Rows in ``subject_areas``. Always ``0`` for a store built purely
    from Scopus Search API ``view=COMPLETE`` captures; see the module
    docstring."""

    citation_snapshots_loaded: int
    """Rows in ``citation_snapshots`` -- one per (record, run) pair whose
    entry carried a parseable ``citedby-count``."""

    unmapped_country_values: tuple[str, ...]
    """The sorted, deduplicated set of raw ``affiliation-country`` strings
    currently stored in ``affiliations.country_iso3`` that did not map to
    an ISO 3166-1 alpha-3 code (BUILD_PLAN §5 risk 8). Never a count of
    dropped rows -- every affiliation these strings came from is still
    present in ``affiliations``/``record_affiliations``; only the country
    field itself stays as the original free text, which
    :mod:`prismabib.countries` already logs a warning for at construction
    time."""


@dataclass
class _Accumulator:
    """In-memory staging area for one full :func:`build_store` pass.

    Every table is accumulated as plain Python rows (tuples, in the exact
    column order of ``schema.sql``) before a single bulk write per table
    (:func:`_write_accumulator`); tables keyed by a natural id
    (``venues``, ``authors``, ``affiliations``, ``keywords``) are
    dict-accumulated so a value repeated across many records is inserted
    once ("first seen wins" for anything beyond the id itself, e.g. a
    keyword's ``term_raw``).
    """

    runs: list[tuple[Any, ...]] = field(default_factory=list)
    records: list[tuple[Any, ...]] = field(default_factory=list)
    venues: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    authors: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    record_authors: list[tuple[Any, ...]] = field(default_factory=list)
    affiliations: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    record_affiliations: list[tuple[Any, ...]] = field(default_factory=list)
    keywords: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    record_keywords: set[tuple[str, str, str]] = field(default_factory=set)
    subject_areas: set[tuple[str, str]] = field(default_factory=set)
    citation_snapshots: dict[tuple[str, datetime], int] = field(default_factory=dict)
    seen_record_ids: set[str] = field(default_factory=set)


def _optional_str(value: object) -> str | None:
    """Coerce a raw JSON value to ``str | None``, treating an empty string as absent.

    Args:
        value: A value read from a parsed Scopus entry via ``.get(...)``.

    Returns:
        ``None`` if ``value`` is ``None`` or an empty/whitespace-only
        string; otherwise ``str(value)``.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _sealed_run_dirs(raw_dir: Path) -> list[Path]:
    """List every sealed Layer 0 run directory under ``raw_dir``, oldest first.

    Args:
        raw_dir: A project's ``raw/`` directory (``project.raw_dir``).

    Returns:
        Sealed run directories (those carrying ``manifest.json``,
        BUILD_PLAN §2.2), excluding the shared HTTP cache directory
        (``_cache``, never a run) and any unsealed (in-progress or
        interrupted) run. Sorted by directory name, which sorts
        chronologically by construction
        (:func:`prismabib.capture.writer._new_run_id`) -- this is the
        traversal order the module docstring's reproducibility argument
        depends on. Returns ``[]`` if ``raw_dir`` does not exist.
    """
    if not raw_dir.is_dir():
        return []
    candidates = [
        entry
        for entry in raw_dir.iterdir()
        if entry.is_dir() and entry.name != _CACHE_DIRNAME and is_sealed(entry)
    ]
    return sorted(candidates, key=lambda path: path.name)


def _iter_page_entries(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Iterate one page file's records, true JSON Lines (one entry per line).

    Args:
        path: A ``raw/<run_id>/page-NNNN.jsonl`` file (BUILD_PLAN §2.2 /
            Stage 2's ``capture/writer.py::_write_page``): one Scopus entry
            object per line, with the response envelope
            (``opensearch:totalResults``, ``cursor``, ``link``, ...)
            written separately to the sibling ``page-NNNN.meta.json``,
            which this loader never needs to read -- every table it
            populates is derived from either an entry or ``manifest.json``.

    Yields:
        ``(line_index, entry)`` for every non-blank line, where
        ``line_index`` is the 0-based line index into ``path`` -- exactly
        :class:`~prismabib.models.PayloadRef.line`'s convention, so a
        ``(payload_file, payload_line)`` pair this loader writes into
        ``records`` and ``PayloadRef(path, line).resolve()`` always agree
        on the same record (S03-AC2). A blank line, if the file ever has
        one, is skipped as a non-entry but still advances the index, since
        it still occupies a line position.
    """
    with path.open("r", encoding="utf-8") as handle:
        for line_index, raw_line in enumerate(handle):
            stripped = raw_line.strip()
            if not stripped:
                continue
            entry = json.loads(stripped)
            if isinstance(entry, dict):
                yield line_index, entry


def _record_id_from_entry(entry: dict[str, Any]) -> str | None:
    """Compute the canonical ``scopus:<eid>`` record id (BUILD_PLAN §3.2, line 372).

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        ``f"scopus:{eid}"``, or ``None`` if ``entry`` has no non-empty
        string ``eid`` -- a pseudo-entry (e.g. Scopus's empty-result-set
        placeholder, which carries an ``"error"`` key instead of a record)
        or a malformed entry, neither of which can be keyed.
    """
    eid = entry.get("eid")
    return f"scopus:{eid}" if isinstance(eid, str) and eid else None


def _cover_date_from_entry(entry: dict[str, Any], *, payload_ref: PayloadRef) -> date:
    """Parse an entry's ``prism:coverDate`` into a :class:`datetime.date`.

    Args:
        entry: One parsed Scopus search result entry.
        payload_ref: Where ``entry`` came from, for the error message.

    Returns:
        The parsed cover date, which also determines ``records.year``.

    Raises:
        ValidationError: If ``prism:coverDate`` is missing or not an
            ISO-8601 date string. Every Scopus Search API entry carries
            this field; its absence indicates a malformed capture, not a
            legitimate gap, so this fails loudly rather than guessing a
            year.
    """
    raw = entry.get("prism:coverDate")
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"Scopus entry at {payload_ref.path}:{payload_ref.line} is missing "
            "'prism:coverDate'; cannot derive a publication year."
        )
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(
            f"Scopus entry at {payload_ref.path}:{payload_ref.line} has an "
            f"unparseable prism:coverDate {raw!r}: {exc}"
        ) from exc


def _title_from_entry(entry: dict[str, Any], *, payload_ref: PayloadRef) -> str:
    """Read an entry's ``dc:title``, failing loudly if absent.

    Args:
        entry: One parsed Scopus search result entry.
        payload_ref: Where ``entry`` came from, for the error message.

    Returns:
        The title.

    Raises:
        ValidationError: If ``dc:title`` is missing or empty.
    """
    raw = entry.get("dc:title")
    if not isinstance(raw, str) or not raw:
        raise ValidationError(
            f"Scopus entry at {payload_ref.path}:{payload_ref.line} is missing 'dc:title'."
        )
    return raw


def _doc_type_from_entry(entry: dict[str, Any]) -> str:
    """Read an entry's document type, preferring the human-readable form.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        ``entry["subtypeDescription"]`` (e.g. ``"Conference Paper"``) when
        present; otherwise the raw ``entry["subtype"]`` code; otherwise
        ``"unknown"``.
    """
    for key in ("subtypeDescription", "subtype"):
        raw = entry.get(key)
        if isinstance(raw, str) and raw:
            return raw
    return "unknown"


def _open_access_from_entry(entry: dict[str, Any]) -> bool | None:
    """Read an entry's open-access flag.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        ``entry["openaccessFlag"]`` when it is already a boolean;
        otherwise ``entry["openaccess"]`` ("0"/"1") coerced to ``bool``;
        otherwise ``None`` when neither field is present.
    """
    flag = entry.get("openaccessFlag")
    if isinstance(flag, bool):
        return flag
    raw = entry.get("openaccess")
    if isinstance(raw, str) and raw in ("0", "1"):
        return raw == "1"
    return None


def _cited_by_count_from_entry(entry: dict[str, Any]) -> int | None:
    """Parse an entry's ``citedby-count``.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        The count as an ``int``, or ``None`` if the field is missing or not
        an integer string -- callers skip creating a citation snapshot row
        in that case rather than recording a fabricated ``0``.
    """
    raw = entry.get("citedby-count")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _doi_from_entry(entry: dict[str, Any]) -> str | None:
    """Read and normalise an entry's DOI (BUILD_PLAN §3.2, line 373).

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        ``normalise_doi(entry["prism:doi"])``, or ``None`` if the entry
        carries no DOI (common for editorials, conference reviews, and
        similar front-matter entries).
    """
    raw = entry.get("prism:doi")
    if isinstance(raw, str) and raw.strip():
        return normalise_doi(raw)
    return None


def _venue_type_from_entry(entry: dict[str, Any]) -> VenueType:
    """Map an entry's ``prism:aggregationType`` to the closed ``Venue.venue_type`` set.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        The mapped venue type via :data:`_AGGREGATION_TYPE_TO_VENUE_TYPE`
        (case-insensitively), or ``"other"`` for anything unrecognised or
        absent.
    """
    raw = entry.get("prism:aggregationType")
    if isinstance(raw, str):
        mapped = _AGGREGATION_TYPE_TO_VENUE_TYPE.get(raw.strip().casefold())
        if mapped is not None:
            return mapped
    return "other"


def _venue_from_entry(entry: dict[str, Any]) -> Venue:
    """Build the :class:`~prismabib.models.Venue` for an entry.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        A :class:`~prismabib.models.Venue`. Scopus's Search API never
        supplies a separate abbreviation field, so ``abbreviation`` is
        always ``None`` here.
    """
    return Venue(
        name=str(entry.get("prism:publicationName") or ""),
        issn=_optional_str(entry.get("prism:issn")),
        eissn=_optional_str(entry.get("prism:eIssn")),
        venue_type=_venue_type_from_entry(entry),
        abbreviation=None,
    )


def _venue_id_from_entry(entry: dict[str, Any], venue: Venue) -> str:
    """Compute a deterministic ``venues.venue_id``.

    Args:
        entry: One parsed Scopus search result entry.
        venue: This entry's already-built :class:`~prismabib.models.Venue`,
            used only for the fallback hash below.

    Returns:
        ``f"scopus-source:{entry['source-id']}"`` when the entry carries a
        Scopus source id (the normal case -- every fixture entry observed
        has one); otherwise a stable hash of the venue's
        name/ISSN/eISSN, so two entries describing the same nameless venue
        still collapse to one ``venues`` row instead of one per record.
        Deterministic in both cases, which is required for byte-stable
        checksums (S03-AC1).
    """
    source_id = entry.get("source-id")
    if isinstance(source_id, str) and source_id:
        return f"scopus-source:{source_id}"
    digest = sha1(f"{venue.name}|{venue.issn or ''}|{venue.eissn or ''}".encode()).hexdigest()
    return f"venue-hash:{digest[:16]}"


def _authors_from_entry(entry: dict[str, Any]) -> list[Author]:
    """Build the author list for an entry.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        One :class:`~prismabib.models.Author` per item of ``entry["author"]``
        when that list is present (the ``view=COMPLETE`` normal case);
        otherwise a single best-effort :class:`~prismabib.models.Author`
        built from ``entry["dc:creator"]`` (a bare display-name string,
        e.g. ``"Dubois H."``, stored as ``surname`` with no ``author_id``)
        when only that is present; otherwise ``[]`` (entries such as
        Scopus's own front-matter "Conference Review" records carry no
        author information at all).
    """
    raw_authors = entry.get("author")
    if isinstance(raw_authors, list):
        authors = []
        for item in raw_authors:
            if not isinstance(item, dict):
                continue
            authors.append(
                Author(
                    author_id=_optional_str(item.get("authid")),
                    surname=str(item.get("surname") or item.get("authname") or ""),
                    given_name=_optional_str(item.get("given-name")),
                    initials=_optional_str(item.get("initials")),
                )
            )
        return authors
    creator = entry.get("dc:creator")
    if isinstance(creator, str) and creator.strip():
        return [Author(author_id=None, surname=creator.strip(), given_name=None, initials=None)]
    return []


def _affiliations_from_entry(entry: dict[str, Any]) -> list[Affiliation]:
    """Build the affiliation list for an entry.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        One :class:`~prismabib.models.Affiliation` per item of
        ``entry["affiliation"]`` (already coerced to a list if Scopus
        emitted a single bare object -- see
        :class:`~prismabib.models.Record`'s own docstring for why that
        coercion exists); ``[]`` if the entry carries no affiliation data.
        Country normalisation, including the unmapped-country warning
        (BUILD_PLAN §5 risk 8), happens inside
        :class:`~prismabib.models.Affiliation`'s own validator -- this
        function does not duplicate that logic.
    """
    raw = entry.get("affiliation")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    affiliations = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        affiliations.append(
            Affiliation(
                afid=item.get("afid"),
                name=str(item.get("affilname") or ""),
                city=_optional_str(item.get("affiliation-city")),
                country=_optional_str(item.get("affiliation-country")),
            )
        )
    return affiliations


def _split_keywords(raw: object) -> list[str]:
    """Split Scopus's ``" | "``-joined ``authkeywords`` string into terms.

    Args:
        raw: ``entry.get("authkeywords")``.

    Returns:
        Each non-empty, stripped term between ``"|"`` separators; ``[]`` if
        ``raw`` is not a string or is empty/whitespace-only (no phantom
        keyword for a record with zero keywords).
    """
    if not isinstance(raw, str):
        return []
    return [term.strip() for term in raw.split("|") if term.strip()]


def _subject_areas_from_entry(entry: dict[str, Any]) -> list[str]:
    """Extract subject-area codes for an entry, if the capture carries any.

    Args:
        entry: One parsed Scopus search result entry.

    Returns:
        Area codes from ``entry["subject-area"]``, accepting either a list
        of plain strings or Scopus's richer ``{"@code": ..., "$": ...}``
        shape (preferring ``@code``); ``[]`` if the entry carries no
        subject-area data, which is every entry the Search API
        ``view=COMPLETE`` currently returns -- see the module docstring.
    """
    raw = entry.get("subject-area")
    if not isinstance(raw, list):
        return []
    codes = []
    for item in raw:
        if isinstance(item, dict):
            code = item.get("@code") or item.get("$")
            if isinstance(code, str) and code:
                codes.append(code)
        elif isinstance(item, str) and item:
            codes.append(item)
    return codes


def _record_from_entry(entry: dict[str, Any], *, record_id: str, payload_ref: PayloadRef) -> Record:
    """Build the full :class:`~prismabib.models.Record` for one Scopus entry.

    Args:
        entry: One parsed Scopus search result entry.
        record_id: This entry's canonical id (see
            :func:`_record_id_from_entry`).
        payload_ref: Where ``entry`` came from.

    Returns:
        The normalised :class:`~prismabib.models.Record`. Every field
        pydantic validator on :class:`~prismabib.models.Record`,
        :class:`~prismabib.models.Affiliation`, etc. runs as part of this
        construction (country normalisation, the affiliation
        scalar-vs-list coercion, ...).

    Raises:
        ValidationError: Via :func:`_title_from_entry` or
            :func:`_cover_date_from_entry`.
    """
    cover_date = _cover_date_from_entry(entry, payload_ref=payload_ref)
    return Record(
        record_id=record_id,
        doi=_doi_from_entry(entry),
        title=_title_from_entry(entry, payload_ref=payload_ref),
        abstract=_optional_str(entry.get("dc:description")),
        year=cover_date.year,
        cover_date=cover_date,
        doc_type=_doc_type_from_entry(entry),
        language=_optional_str(entry.get("language") or entry.get("dc:language")),
        venue=_venue_from_entry(entry),
        authors=_authors_from_entry(entry),
        affiliations=_affiliations_from_entry(entry),
        author_keywords=_split_keywords(entry.get("authkeywords")),
        # Never populated from a Search API capture -- see the module docstring.
        index_keywords=[],
        subject_areas=_subject_areas_from_entry(entry),
        open_access=_open_access_from_entry(entry),
        source_payload_ref=payload_ref,
    )


def _normalise_keyword_term(raw: str) -> str:
    """Normalise one keyword term (BUILD_PLAN modelling note 3, line 885).

    Args:
        raw: The raw term, e.g. as split by :func:`_split_keywords`.

    Returns:
        ``raw`` casefolded, with punctuation replaced by whitespace,
        internal whitespace runs collapsed to a single space, surrounding
        whitespace stripped, and singularised via
        :data:`_KEYWORD_SINGULARISATION` when it matches an entry there.
        ``raw`` itself is never discarded by this function -- callers store
        it separately as ``term_raw``.
    """
    folded = raw.casefold()
    stripped = _PUNCTUATION_RE.sub(" ", folded)
    collapsed = _WHITESPACE_RE.sub(" ", stripped).strip()
    return _KEYWORD_SINGULARISATION.get(collapsed, collapsed)


def _keyword_id(term_norm: str) -> str:
    """Compute a deterministic ``keywords.keyword_id`` for a normalised term.

    Args:
        term_norm: An already-normalised term (see
            :func:`_normalise_keyword_term`).

    Returns:
        A stable id derived from ``term_norm`` alone, so the same
        normalised term always maps to the same row regardless of which
        record or run it was first seen in (required for byte-stable
        checksums, S03-AC1).
    """
    return f"kw:{sha1(term_norm.encode()).hexdigest()[:16]}"


def _accumulate_keyword(acc: _Accumulator, *, record_id: str, raw_term: str, kind: str) -> None:
    """Register one (record, keyword, kind) occurrence into ``acc``.

    Args:
        acc: The in-progress load accumulator.
        record_id: The owning record.
        raw_term: The keyword's raw (pre-normalisation) text.
        kind: ``"author"`` or ``"index"`` (BUILD_PLAN schema comment on
            ``record_keywords``).

    A blank normalised term (e.g. a raw term that is pure punctuation)
    contributes no row -- see `test_load__record_with_zero_keywords`-style
    edge cases in the module docstring.
    """
    term_norm = _normalise_keyword_term(raw_term)
    if not term_norm:
        return
    keyword_id = _keyword_id(term_norm)
    # First-seen raw form wins for a given normalised term across the whole
    # store -- the frozen `keywords` schema has one term_raw column per
    # keyword_id, not one per occurrence, so this is the loader's one
    # necessary interpretation of "never discard the raw form" (it is
    # preserved, just not once per occurrence). See the module docstring.
    acc.keywords.setdefault(keyword_id, (keyword_id, raw_term, term_norm))
    acc.record_keywords.add((record_id, keyword_id, kind))


def _load_run(acc: _Accumulator, raw_dir: Path, run_dir: Path) -> None:
    """Fold one sealed Layer 0 run directory into ``acc``.

    Args:
        acc: The in-progress load accumulator, mutated in place.
        raw_dir: The project's ``raw/`` directory.
        run_dir: The sealed run directory to load (a member of
            :func:`_sealed_run_dirs`'s return value).
    """
    manifest = RunManifest.model_validate_json(
        (run_dir / _MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    acc.runs.append(
        (
            manifest.run_id,
            manifest.started_at,
            manifest.query,
            manifest.view,
            manifest.total_results,
            manifest.payload_sha256,
            manifest.criteria_version,
        )
    )
    # Point-in-time snapshot date for every citation this run's entries
    # carry -- the run's own start, never the wall clock at load time. See
    # the module docstring's "Citation snapshots" section.
    retrieved_at = manifest.started_at

    for payload_file in manifest.payload_files:
        relative_payload_file = f"{manifest.run_id}/{payload_file}"

        for line_index, entry in _iter_page_entries(run_dir / payload_file):
            if entry.get("error") is not None:
                # Scopus's own placeholder for an empty result set
                # (`{"error": "Result set was empty"}`), not a record.
                continue
            record_id = _record_id_from_entry(entry)
            if record_id is None:
                logger.warning(
                    "store.load.entry_missing_eid",
                    run_id=manifest.run_id,
                    payload_file=payload_file,
                    line=line_index,
                )
                continue

            payload_ref = PayloadRef(path=raw_dir / relative_payload_file, line=line_index)
            record = _record_from_entry(entry, record_id=record_id, payload_ref=payload_ref)

            cited_by_count = _cited_by_count_from_entry(entry)
            if cited_by_count is not None:
                acc.citation_snapshots[(record_id, retrieved_at)] = cited_by_count

            if record_id in acc.seen_record_ids:
                # Re-capture of a paper already loaded from an earlier run:
                # only the citation snapshot above is new; see the module
                # docstring's "Re-captured records" section.
                continue
            acc.seen_record_ids.add(record_id)

            venue_id = _venue_id_from_entry(entry, record.venue)
            acc.venues.setdefault(
                venue_id,
                (
                    venue_id,
                    record.venue.name,
                    record.venue.issn,
                    record.venue.eissn,
                    record.venue.venue_type,
                    record.venue.abbreviation,
                ),
            )

            acc.records.append(
                (
                    record_id,
                    manifest.run_id,
                    record.doi,
                    record.title,
                    record.abstract,
                    record.year,
                    record.cover_date,
                    record.doc_type,
                    record.language,
                    venue_id,
                    record.open_access,
                    relative_payload_file,
                    line_index,
                )
            )

            for position, author in enumerate(record.authors, start=1):
                if author.author_id is None:
                    # No stable Scopus author id to key on (e.g. the
                    # dc:creator-only fallback of _authors_from_entry).
                    # BUILD_PLAN modelling note 4 says to use Scopus ids
                    # as-is, never invent one, so this author simply has no
                    # authors/record_authors row.
                    continue
                acc.authors.setdefault(
                    author.author_id, (author.author_id, author.surname, author.given_name)
                )
                acc.record_authors.append((record_id, author.author_id, position))

            for affiliation in record.affiliations:
                if affiliation.afid is None:
                    continue
                acc.affiliations.setdefault(
                    affiliation.afid,
                    (affiliation.afid, affiliation.name, affiliation.city, affiliation.country),
                )
                acc.record_affiliations.append((record_id, affiliation.afid))

            for term in record.author_keywords:
                _accumulate_keyword(acc, record_id=record_id, raw_term=term, kind="author")
            # Unreachable today, and deliberately left that way rather than deleted or
            # excluded from coverage. `_record_from_entry` hardcodes `index_keywords=[]`
            # because the Search API's COMPLETE view does not carry indexed terms (see
            # the module docstring); they arrive only via the Abstract Retrieval API,
            # which no stage calls yet. The `keywords.kind` column already models
            # `"index"`, so this loop is the correct code waiting on its data source --
            # a `# pragma: no cover` would hide that it is dormant, and deleting it
            # would leave a future stage to rediscover the requirement.
            for term in record.index_keywords:
                _accumulate_keyword(acc, record_id=record_id, raw_term=term, kind="index")

            for area_code in record.subject_areas:
                acc.subject_areas.add((record_id, area_code))


def _insert_rows(
    connection: duckdb.DuckDBPyConnection, table: str, rows: Sequence[tuple[Any, ...]]
) -> None:
    """Bulk-insert ``rows`` into ``table``, tolerating an empty batch.

    Deliberately NOT ``executemany``. DuckDB is an analytical engine and its
    ``executemany`` carries a large *per-call* cost that is nearly independent of
    row count: measured on this project's fixture, inserting a single row into
    ``runs`` took 0.37 s and 452 rows into ``record_keywords`` took 1.4 s. Across
    the eleven tables that put ``build_store`` at 5.3 s for 120 records against
    BUILD_PLAN line 925's 5 s budget -- and it would have scaled linearly into
    minutes on a real 1,771-record corpus.

    Handing DuckDB a pandas frame instead lets it ingest the batch in one
    columnar operation: the same 452 rows drop from 1.417 s to 0.011 s (~125x).
    pandas is already a §2.4 dependency and is used here purely as the transfer
    format; no new dependency, and no behavioural difference -- the resulting
    rows are identical.

    Args:
        connection: An open, writable DuckDB connection.
        table: The target table name.
        rows: Row tuples, in ``schema.sql`` column order for ``table``. An empty
            batch is a no-op rather than an error -- routinely the case for
            ``subject_areas`` given the current data source (see the module
            docstring).
    """
    if not rows:
        return

    frame = pd.DataFrame(list(rows), columns=[f"c{index}" for index in range(len(rows[0]))])
    # A registered view is scoped and explicit; DuckDB's replacement scan would
    # otherwise resolve `frame` out of the caller's local namespace, which is
    # action-at-a-distance that breaks the moment this is refactored.
    connection.register("_prismabib_bulk", frame)
    try:
        connection.execute(f'INSERT INTO "{table}" SELECT * FROM _prismabib_bulk')
    finally:
        connection.unregister("_prismabib_bulk")


def _write_accumulator(connection: duckdb.DuckDBPyConnection, acc: _Accumulator) -> None:
    """Bulk-write every table of ``acc`` into ``connection``.

    Args:
        connection: An open, writable DuckDB connection whose schema has
            already been (re)created (see :func:`_reset_schema`).
        acc: A fully-folded accumulator (every sealed run processed).
    """
    _insert_rows(connection, "runs", acc.runs)
    _insert_rows(connection, "records", acc.records)
    _insert_rows(connection, "venues", list(acc.venues.values()))
    _insert_rows(connection, "authors", list(acc.authors.values()))
    _insert_rows(connection, "record_authors", acc.record_authors)
    _insert_rows(connection, "affiliations", list(acc.affiliations.values()))
    _insert_rows(connection, "record_affiliations", acc.record_affiliations)
    _insert_rows(connection, "keywords", list(acc.keywords.values()))
    _insert_rows(connection, "record_keywords", sorted(acc.record_keywords))
    _insert_rows(connection, "subject_areas", sorted(acc.subject_areas))
    _insert_rows(
        connection,
        "citation_snapshots",
        [
            (record_id, retrieved_at, count)
            for (record_id, retrieved_at), count in acc.citation_snapshots.items()
        ],
    )


def _reset_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Drop every Layer 1 table (if present) and recreate them from ``schema.sql``.

    Args:
        connection: An open, writable DuckDB connection.

    Executing the checked-in ``schema.sql`` file verbatim -- rather than a
    hand-rolled set of ``CREATE TABLE`` calls -- is what keeps it and the
    live catalogue from drifting apart
    (`test_schema__sql_file__matches_live_duckdb_introspection`).
    """
    for table in reversed(TABLE_NAMES):
        connection.execute(f'DROP TABLE IF EXISTS "{table}"')
    connection.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _count(connection: duckdb.DuckDBPyConnection, table: str) -> int:
    """Return ``SELECT COUNT(*) FROM table``.

    Args:
        connection: An open DuckDB connection.
        table: The table to count.

    Returns:
        The row count.
    """
    row = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
    return int(row[0]) if row is not None else 0


def _stats_from_connection(connection: duckdb.DuckDBPyConnection, *, rebuilt: bool) -> StoreStats:
    """Compute a :class:`StoreStats` snapshot from a store's current content.

    Args:
        connection: An open DuckDB connection onto a Layer 1 store whose
            schema has already been created.
        rebuilt: The value to report as ``StoreStats.rebuilt``.

    Returns:
        Fresh counts read directly from ``connection`` -- see
        :class:`StoreStats` for each field's exact counting convention.
    """
    duplicate_row = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(n), 0) FROM ("
        "  SELECT COUNT(*) AS n FROM records WHERE doi IS NOT NULL GROUP BY doi HAVING COUNT(*) > 1"
        ") AS duplicate_dois"
    ).fetchone()
    duplicate_doi_groups = int(duplicate_row[0]) if duplicate_row is not None else 0
    duplicate_records = int(duplicate_row[1]) if duplicate_row is not None else 0

    unmapped_rows = connection.execute(
        "SELECT DISTINCT country_iso3 FROM affiliations WHERE country_iso3 IS NOT NULL"
    ).fetchall()
    unmapped = sorted(value for (value,) in unmapped_rows if not normalise_country(value)[1])

    return StoreStats(
        rebuilt=rebuilt,
        runs_loaded=_count(connection, "runs"),
        records_loaded=_count(connection, "records"),
        duplicate_doi_groups=duplicate_doi_groups,
        duplicate_records=duplicate_records,
        authors_loaded=_count(connection, "authors"),
        affiliations_loaded=_count(connection, "affiliations"),
        venues_loaded=_count(connection, "venues"),
        keywords_loaded=_count(connection, "keywords"),
        record_keyword_links_loaded=_count(connection, "record_keywords"),
        subject_area_links_loaded=_count(connection, "subject_areas"),
        citation_snapshots_loaded=_count(connection, "citation_snapshots"),
        unmapped_country_values=tuple(unmapped),
    )


def build_store(project: Project, *, rebuild: bool = False) -> StoreStats:
    """(Re)build ``project``'s Layer 1 DuckDB store from Layer 0 (BUILD_PLAN line 891).

    This is the one function BUILD_PLAN §2.2 requires Layer 1 to be
    reconstructible by: it always derives every row from
    ``project.raw_dir``'s sealed run directories and the checked-in
    ``schema.sql``, never from anything already in ``project.db_path``.

    Args:
        project: The project to build the store for.
        rebuild: When ``False`` (the default) and ``project.db_path``
            already exists, this call does no work beyond reading back
            current counts -- an idempotent no-op reuse of the existing
            store (S03-AC4: calling this twice never duplicates a row,
            since the second call does not load anything a second time).
            When ``True``, or when no store exists yet, ``project.db_path``
            is deleted if present and fully rebuilt from Layer 0 -- the
            only path that guarantees byte-stable table checksums against
            the current Layer 0 content (S03-AC1) and that "deleting
            corpus.duckdb and rebuilding loses nothing" (S03-AC3).

    Returns:
        Summary counts for the store as it now stands; see
        :class:`StoreStats`. On the ``rebuild=False`` reuse path,
        ``duplicate_doi_groups``/``duplicate_records``/
        ``unmapped_country_values`` are recomputed fresh from the existing
        table content via the same queries as a full build (they are cheap,
        stateless queries, not a cached value that could go stale) -- only
        the *loading itself* is skipped, not the reporting.

    Raises:
        StoreError: If ``project.db_path`` exists, ``rebuild`` is
            ``False``, and it does not look like a Layer 1 store this
            function created (e.g. wrong schema); or if DuckDB refuses to
            open the file for any other reason.
        ValidationError: If a captured entry is missing a required field
            (``dc:title`` or ``prism:coverDate``) -- see
            :func:`_title_from_entry`/:func:`_cover_date_from_entry`.
    """
    db_path = project.db_path

    if not rebuild and db_path.is_file():
        connection = connect(project, read_only=True)
        try:
            return _stats_from_connection(connection, rebuilt=False)
        except duckdb.Error as exc:
            raise StoreError(
                f"{db_path} exists but does not look like a Layer 1 store built "
                f"by build_store ({exc}). Call build_store(project, rebuild=True) "
                "to recreate it."
            ) from exc
        finally:
            connection.close()

    if db_path.is_file():
        db_path.unlink()

    connection = connect(project, read_only=False)
    try:
        _reset_schema(connection)
        accumulator = _Accumulator()
        for run_dir in _sealed_run_dirs(project.raw_dir):
            _load_run(accumulator, project.raw_dir, run_dir)
        _write_accumulator(connection, accumulator)
        stats = _stats_from_connection(connection, rebuilt=True)
    finally:
        connection.close()

    if stats.unmapped_country_values:
        logger.warning("store.load.unmapped_countries", values=stats.unmapped_country_values)
    logger.info("store.build_store.complete", **stats.model_dump())
    return stats


class Corpus:
    """Read-facing handle onto a Layer 1 store (BUILD_PLAN line 893-897).

    Every analysis module is meant to read the store through this class
    rather than issuing raw SQL against
    :func:`prismabib.store.db.connect` directly, so the PRISMA-stage
    semantics of :meth:`records`/:meth:`keywords` have exactly one
    implementation.
    """

    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        """Wrap an already-open DuckDB connection.

        Args:
            connection: A connection onto a Layer 1 store, typically from
                :func:`prismabib.store.db.connect` or :meth:`Corpus.open`.
        """
        self._connection = connection

    @classmethod
    def open(cls, project: Project, *, read_only: bool = True) -> Corpus:
        """Open ``project``'s Layer 1 store as a :class:`Corpus`.

        Args:
            project: The project whose store to open.
            read_only: Forwarded to :func:`prismabib.store.db.connect`.

        Returns:
            A new :class:`Corpus` handle.

        Raises:
            StoreError: If the store cannot be opened; see
                :func:`prismabib.store.db.connect`.
        """
        return cls(connect(project, read_only=read_only))

    def _query(self, sql: str, params: Sequence[Any] = ()) -> pl.DataFrame:
        """Run ``sql`` and materialise the result as a polars DataFrame.

        Args:
            sql: The query to run.
            params: Positional ``?`` parameters, if any.

        Returns:
            A :class:`polars.DataFrame` with one column per result column,
            in query order. Built from plain Python rows
            (``DuckDBPyConnection.fetchall()``) rather than DuckDB's own
            Arrow/pandas export, which would pull in ``pyarrow`` -- a
            dependency this stage may not add.
        """
        relation = self._connection.execute(sql, list(params))
        columns = [column[0] for column in relation.description] if relation.description else []
        rows = relation.fetchall()
        return pl.DataFrame(rows, schema=columns, orient="row")

    def records(self, stage: PrismaStage = PrismaStage.INCLUDED) -> pl.DataFrame:
        """Return the ``records`` table for one named PRISMA set.

        Args:
            stage: Which PRISMA-flow record set to return. Only
                :attr:`~prismabib.stage.PrismaStage.RAW` (``S_raw``, every
                captured record) is answerable from Layer 1 alone -- see
                the module docstring's "``PrismaStage`` before Stage 4
                exists" section.

        Returns:
            Every column of ``records``, one row per record, ordered by
            ``record_id``, when ``stage`` is
            :attr:`~prismabib.stage.PrismaStage.RAW`.

        Raises:
            NotImplementedError: For every ``stage`` other than
                :attr:`~prismabib.stage.PrismaStage.RAW`, including the
                default ``PrismaStage.INCLUDED`` -- Stage 4's decision-log
                engine does not exist yet to compute it.
        """
        if stage is not PrismaStage.RAW:
            raise NotImplementedError(
                f"Corpus.records(stage={stage!r}) needs the Stage 4 PRISMA engine "
                "(prismabib.prisma), which does not exist yet. The only stage "
                "answerable from Layer 1 alone is PrismaStage.RAW (every "
                "captured record) -- pass that explicitly."
            )
        return self._query("SELECT * FROM records ORDER BY record_id")

    def keywords(
        self, kind: str = "author", stage: PrismaStage = PrismaStage.INCLUDED
    ) -> pl.DataFrame:
        """Return keyword occurrences of one kind, for one named PRISMA set.

        Args:
            kind: ``"author"`` or ``"index"`` (BUILD_PLAN schema comment on
                ``record_keywords``). Not validated against that closed set
                here -- an unrecognised ``kind`` simply matches no rows,
                since ``record_keywords.kind`` is plain ``TEXT``, not a
                DuckDB ``ENUM``.
            stage: See :meth:`records`; the same restriction applies.

        Returns:
            One row per (record, keyword) occurrence of ``kind``:
            ``record_id``, ``keyword_id``, ``term_raw``, ``term_norm``,
            ``kind``; ordered by ``record_id``, then ``term_norm``.

        Raises:
            NotImplementedError: For every ``stage`` other than
                :attr:`~prismabib.stage.PrismaStage.RAW`.
        """
        if stage is not PrismaStage.RAW:
            raise NotImplementedError(
                f"Corpus.keywords(stage={stage!r}) needs the Stage 4 PRISMA engine "
                "(prismabib.prisma), which does not exist yet. The only stage "
                "answerable from Layer 1 alone is PrismaStage.RAW (every "
                "captured record) -- pass that explicitly."
            )
        return self._query(
            "SELECT rk.record_id, k.keyword_id, k.term_raw, k.term_norm, rk.kind "
            "FROM record_keywords rk JOIN keywords k ON k.keyword_id = rk.keyword_id "
            "WHERE rk.kind = ? ORDER BY rk.record_id, k.term_norm",
            [kind],
        )

    def citations(self, at: datetime | None = None) -> pl.DataFrame:
        """Return one citation-count row per record, as of a point in time.

        Args:
            at: The point in time to query "as of". When ``None`` (the
                default), the *latest* snapshot on record is used for every
                record, regardless of how long ago it was retrieved --
                this is the documented default behaviour BUILD_PLAN pins
                (`test_citations__query_without_date__uses_latest_snapshot`).
                When given, the most recent snapshot with
                ``retrieved_at <= at`` is used for each record; a record
                with no snapshot at or before ``at`` is simply absent from
                the result (its citation count "as of" that date is
                unknown, not zero).

        Returns:
            Columns ``record_id``, ``retrieved_at``, ``cited_by_count``,
            one row per record that has a qualifying snapshot, ordered by
            ``record_id``. Every citation figure this returns carries the
            ``retrieved_at`` it was true as of (BUILD_PLAN modelling note
            1) -- callers must not report ``cited_by_count`` without it.
        """
        ranked = (
            "SELECT record_id, retrieved_at, cited_by_count, "
            "ROW_NUMBER() OVER (PARTITION BY record_id ORDER BY retrieved_at DESC) AS rn "
            "FROM citation_snapshots"
        )
        if at is None:
            sql = f"SELECT record_id, retrieved_at, cited_by_count FROM ({ranked}) WHERE rn = 1 ORDER BY record_id"
            return self._query(sql)
        sql = (
            f"SELECT record_id, retrieved_at, cited_by_count FROM ({ranked} WHERE retrieved_at <= ?) "
            "WHERE rn = 1 ORDER BY record_id"
        )
        return self._query(sql, [at])


__all__ = ["Corpus", "StoreStats", "build_store"]
