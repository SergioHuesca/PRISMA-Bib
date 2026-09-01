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
(:func:`~prismabib.capture.layout.is_sealed`), skipping by name every
directory under ``raw/`` that is not a search run
(:data:`~prismabib.capture.layout.NON_RUN_DIRNAMES`: the HTTP cache
``raw/_cache/``, and ``raw/abstracts/``, whose nested Abstract Retrieval
runs carry a different manifest schema -- see below), and walks each run's
``manifest.payload_files`` in fetch order, each page's lines in file order
(:func:`_iter_page_entries`). That traversal order -- sorted ``run_id``
(sortable by construction, oldest first), then ``payload_files`` order,
then line order -- is fixed and is what makes two runs of
:func:`build_store` over identical Layer 0 input produce byte-identical
table checksums (S03-AC1): nothing here depends on filesystem
directory-listing order or dict iteration order.

**Abstract Retrieval runs (ADR 0018).** ``raw/abstracts/<run_id>/`` is a
second, nested tree of sealed runs -- :mod:`prismabib.capture.enrich`'s
output, one :class:`~prismabib.capture.manifest.AbstractRunManifest` and one
``manifest.json`` seal per run, exactly as :func:`is_sealed` expects, but a
different payload shape entirely: verbatim Abstract Retrieval responses, not
search entries. :func:`_sealed_abstract_run_dirs` walks these separately from
:func:`_sealed_run_dirs`, sorted by ``run_id`` the same way, and folds them
in only *after* every search run has been loaded -- an abstract run
identifies no record (it re-describes one a search run already found) and
therefore writes no ``runs`` row, but it does write one ``abstract_runs`` row
per run and, per record it covers that is also in ``records``, one
``record_subject_area_coverage`` row recording whether Scopus assigned
areas, assigned none, or could not be reached (``not_found``/
``not_entitled``) -- the three states :class:`~prismabib.capture.manifest.AbstractUnavailableReason`
already names, plus the successful case. A record with no coverage row for a
given run was never asked, in that run; that is the fourth state, and
absence is how it is represented (adding a row for it would multiply the
table by the corpus size to record that nothing happened). Where two sealed
abstract runs both observe one record, the later ``run_id``'s codes replace
the earlier's in ``subject_areas`` -- a re-enrichment reports Scopus as it is
now -- while both runs' coverage rows are kept, since
``record_subject_area_coverage``'s primary key is ``(record_id, run_id)``. A
record an abstract run describes that is not in ``records`` at all (no
search run ever loaded it) is skipped, not written, and counted in
``StoreStats.unmatched_abstract_record_ids`` rather than silently dropped.
See ADR 0018 for the full design and the alternatives it rejects.

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
requests for a search, BUILD_PLAN §5 risk 1) does not carry Scopus
subject-area codes or indexed (non-author) keyword terms -- those live in
the separate Abstract Retrieval API. ``index_keywords`` is therefore always
``[]`` for every record this loader produces (Stage 2 never calls that API),
and a captured *search* entry carrying its own ``subject-area`` array is
schema-supported but currently unobserved in practice (see the module
docstring's "Abstract Retrieval runs" section above for the API that
actually supplies this data, now that :mod:`prismabib.capture.enrich` and
this loader both exist). ``keywords``/``record_keywords`` (kind
``"index"``) has no rows to insert from either source today. This is a
data-source limitation, not a modelling gap: nothing here silently drops a
field that Layer 0 did capture.

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

**``PrismaStage`` delegates to the Stage 4 PRISMA engine.**
``Corpus.records``/``keywords`` take a
:class:`~prismabib.stage.PrismaStage`. :attr:`~prismabib.stage.PrismaStage.RAW`
is answered directly from ``records`` -- everything else is delegated to
:mod:`prismabib.prisma.engine`, which is the only place that knows how to
compute a set past "everything captured" (``criteria.yaml`` plus the
Layer 2 decision log). That delegation is a **function-local** import
inside :meth:`Corpus._prisma_stage_record_ids`, not a module-level one:
BUILD_PLAN §0 rule 1 forbids ``store/`` from depending on ``prisma/`` at
import time (Stage 3 may not depend on Stage 4), so
``prismabib.prisma.engine`` is only imported once a non-``RAW`` stage is
actually requested, and only for a :class:`Corpus` opened from a
:class:`~prismabib.project.Project` (see :meth:`Corpus.open`) -- a bare
``Corpus(connection)`` has no project to resolve ``criteria.yaml`` or the
decision log against, and raises rather than guessing one. This inverted
dependency direction (``store`` reaching *up* into ``prisma`` at call time)
is flagged for architect-reviewer to confirm or overturn. The delegation
passes this ``Corpus``'s own connection down to the engine rather than
letting it open a second one: DuckDB rejects a second connection to one
file from one process when their configurations differ, so a ``Corpus``
opened writable would otherwise crash on any non-``RAW`` stage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Final, Literal

import duckdb
import pandas as pd
import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict

from prismabib.capture.layout import (
    ABSTRACTS_DIRNAME,
    NON_RUN_DIRNAMES,
    RUN_MANIFEST_FILENAME,
    is_sealed,
)
from prismabib.capture.manifest import AbstractRunManifest, RunManifest
from prismabib.countries import normalise_country
from prismabib.errors import StoreError, ValidationError
from prismabib.models import Affiliation, Author, PayloadRef, Record, Venue, normalise_doi
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.checksums import TABLE_NAMES
from prismabib.store.db import connect

logger = structlog.get_logger(__name__)

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

# The closed vocabulary of `malformed_entries.reason` (ADR 0012). Short, stable
# codes rather than the exception message: those messages embed the entry's
# absolute path, and an absolute path in a checksummed table would make
# S03-AC1's byte-stable checksums depend on where the repository is checked
# out. The message, path and all, goes to the log instead.
_REASON_MISSING_EID: Final = "missing_eid"
_REASON_INVALID_FIELD: Final = "invalid_field"

#: The share of a capture's entries that may be skipped before
#: :func:`_guard_against_unloadable_capture` refuses to write a store, and the
#: number of skips below which the ratio is not consulted at all. See that
#: function for why these numbers.
_MAX_SKIPPED_ENTRY_RATIO: Final = 0.05
_MIN_SKIPS_FOR_RATIO_GUARD: Final = 10

#: How many skipped-entry references the end-of-build warning names before it
#: says "and N more". The real capture that motivated skipping had 1 skip out
#: of 1,945; a systematically broken one has thousands, and a log line
#: carrying every one of them is unreadable in a terminal and expensive in a
#: structured log sink. The full list is in `malformed_entries`, which is
#: queryable and does not scroll past.
_MAX_LOGGED_MALFORMED_ENTRIES: Final = 20


class StoreStats(BaseModel):
    """Summary counts from one :func:`build_store` call.

    Every field below except ``unmatched_abstract_record_ids`` is read back
    out of the Layer 1 store *after* loading -- a ``SELECT COUNT(*)``, an
    equivalent grouped query, or (for the tuple-valued fields) a ``SELECT``
    over the rows themselves -- never an in-memory tally kept alongside the
    load, so a caller reading ``StoreStats`` and a caller running the same
    query against :func:`prismabib.store.db.connect` always agree.

    That is why ``malformed_entries_skipped`` has a table behind it
    (``malformed_entries``, ADR 0012) rather than being a list the loader
    happened to accumulate. An in-memory tally is empty on the
    ``rebuild=False`` reuse path -- which is the path ``prismabib build``
    takes by default -- and an empty tuple there would read as "nothing was
    skipped" rather than "this call did not load anything".
    ``unmatched_abstract_record_ids`` is the one deliberate exception to that
    rule -- see its own docstring for why ADR 0018 does not add a table for
    it too."""

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
    """Rows in ``subject_areas``. ``0`` for a store built purely from Scopus
    Search API ``view=COMPLETE`` captures (see the module docstring); once a
    project has sealed Abstract Retrieval runs (``prismabib enrich``) and
    those runs have been folded in, this is one row per (record, area code)
    the *latest* covering run reported (ADR 0018's "the later run wins")."""

    citation_snapshots_loaded: int
    """Rows in ``citation_snapshots`` -- one per (record, run) pair whose
    entry carried a parseable ``citedby-count``."""

    malformed_entries_skipped: tuple[str, ...]
    """``"<run_id>/<page>:<line>"`` for every row in ``malformed_entries``,
    sorted -- one per Layer 0 **entry** the loader could not turn into a
    record and therefore skipped rather than aborting on. One bad entry out of
    thousands must not make the rest unloadable; a real capture returned 1,945
    records of which exactly one lacked ``dc:title``, and the load refused all
    1,944 others.

    **Entries, not records.** ``records_loaded`` counts distinct records, and
    a skipped entry is not necessarily a lost record: a re-capture of a paper
    an earlier run already loaded can be skipped here while the record stays
    in the store (its citation snapshot is kept too -- see
    :func:`_resolve_pending_snapshots`). A non-empty tuple therefore means
    "some entry did not parse", not "the corpus is short by this many
    records". Subtracting it from anything is a mistake.

    **What counts as malformed.** Two things, distinguished by
    ``malformed_entries.reason``: an entry with no usable ``eid``
    (``"missing_eid"``, so there is no record id to key on), and an entry
    whose ``dc:title`` or ``prism:coverDate`` is absent or unparseable
    (``"invalid_field"``). Both are fields Scopus always sends. A *pydantic*
    failure constructing :class:`~prismabib.models.Record` is deliberately
    **not** in this set and still aborts the whole load -- see the comment at
    the ``except`` in :func:`_load_run` for why that line is where it is.

    A wholesale failure is not reported here at all:
    :func:`_guard_against_unloadable_capture` raises
    :class:`~prismabib.errors.StoreError` instead of returning a
    suspiciously small corpus with a long list attached."""

    unmapped_country_values: tuple[str, ...]
    """The sorted, deduplicated set of raw ``affiliation-country`` strings
    currently stored in ``affiliations.country_iso3`` that did not map to
    an ISO 3166-1 alpha-3 code (BUILD_PLAN §5 risk 8). Never a count of
    dropped rows -- every affiliation these strings came from is still
    present in ``affiliations``/``record_affiliations``; only the country
    field itself stays as the original free text, which
    :mod:`prismabib.countries` already logs a warning for at construction
    time."""

    abstract_runs_loaded: int
    """Rows in ``abstract_runs`` (ADR 0018) -- the number of sealed
    ``raw/abstracts/<run_id>/`` directories folded into this store. ``0``
    for a project that has never run ``prismabib enrich``."""

    record_subject_area_coverage_loaded: int
    """Rows in ``record_subject_area_coverage`` (ADR 0018) -- one per
    (record, abstract run) pair this store can say something about:
    ``"assigned"``, ``"none_assigned"``, ``"not_found"`` or
    ``"not_entitled"``. A record covered by more than one abstract run
    contributes one row per run, so this can exceed ``records_loaded``. A
    record with **no** row for a given run was never asked about in that
    run -- the fourth state, and the reason absence rather than a fourth
    status value represents it (see the module docstring)."""

    unmatched_abstract_record_ids: tuple[str, ...]
    """Sorted, deduplicated record ids that at least one loaded abstract run
    described but that are not in ``records`` -- e.g. an abstract run
    enriching a record a search run later stopped identifying, or run
    against a ``record_ids`` list that included one Layer 0 never captured.
    Per ADR 0018 / BUILD_PLAN §5 risk 8, such a record contributes **no**
    ``subject_areas`` or ``record_subject_area_coverage`` row -- there is
    nothing in ``records`` to attach one to -- so this tuple is the only
    place it is visible at all; skipped, never silently dropped.

    Unlike ``malformed_entries_skipped``/``unmapped_country_values``, this is
    **not** backed by a Layer 1 table (no table records what was skipped for
    this specific reason, and ADR 0018 adds exactly two): it is accumulated
    only during a real load and is therefore always ``()`` on the
    ``rebuild=False`` reuse path, even if a prior rebuild reported some. A
    caller that needs this number after the fact must call
    ``build_store(project, rebuild=True)`` again."""


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
    #: ``(query, record_id)`` pairs already counted toward `identified`.
    #:
    #: A *set of pairs*, not a record -> first-query mapping. The mapping was
    #: wrong in a way that only shows up with three runs: a record first seen
    #: under query A, re-found by B, then re-found again by a *refresh of B*
    #: compares unequal to A both times and is counted as a duplicate twice.
    #: `identified` adds each distinct query once, so each (query, record) may
    #: be subtracted at most once. Asking "has this record already been counted
    #: under *this* query" is the question that matches.
    counted_query_records: set[tuple[str, str]] = field(default_factory=set)
    #: ``malformed_entries`` rows, in ``schema.sql`` column order, for every
    #: entry that could not be turned into a record. Rows, not a count,
    #: because the operator's next question is always *which one* -- and
    #: Layer 0 is immutable, so the answer has to survive in Layer 1 rather
    #: than only in the return value of the call that happened to rebuild.
    malformed_entries: list[tuple[Any, ...]] = field(default_factory=list)
    #: Records already loaded by an earlier run -- PRISMA's "duplicates removed
    #: before screening". Observable only during the load: `records.run_id` keeps
    #: the FIRST run that loaded a record, so afterwards nothing in Layer 1 can
    #: say how many runs a record appeared in.
    cross_run_duplicates: dict[str, int] = field(default_factory=dict)
    #: Citation snapshots read off entries that were then skipped as
    #: malformed. Held back rather than written straight into
    #: ``citation_snapshots`` because the schema declares no foreign keys at
    #: all, so a snapshot for a record no run ever loaded would be an orphan
    #: row nothing would catch. :func:`_resolve_pending_snapshots` promotes
    #: exactly those whose record was loaded from some other run.
    pending_citation_snapshots: dict[tuple[str, datetime], int] = field(default_factory=dict)
    #: How many Layer 0 lines were considered as candidate records across
    #: every run -- the denominator :func:`_guard_against_unloadable_capture`
    #: measures the skipped share against. Excludes unparseable JSON lines
    #: and Scopus's empty-result-set placeholder, neither of which is an
    #: entry anyone claimed was a record.
    entries_seen: int = 0
    #: ``abstract_runs`` rows, in ``schema.sql`` column order, one per sealed
    #: ``raw/abstracts/<run_id>/`` directory (ADR 0018). Never a ``runs`` row --
    #: see :func:`_load_abstract_run`.
    abstract_runs: list[tuple[Any, ...]] = field(default_factory=list)
    #: ``record_subject_area_coverage`` rows, in ``schema.sql`` column order,
    #: one per (record, abstract run) pair whose record is also in ``records``
    #: (ADR 0018). Populated by :func:`_load_abstract_run`; sorted before
    #: insertion since it is built from dict iteration, per §3.7.3's rule
    #: against depending on that order.
    record_subject_area_coverage: list[tuple[Any, ...]] = field(default_factory=list)
    #: record_id -> the winning subject-area codes contributed by Abstract
    #: Retrieval runs (ADR 0018), resolved to the *latest* covering run by
    #: the time every abstract run has been folded in -- abstract runs are
    #: walked in ascending `run_id` order (:func:`_sealed_abstract_run_dirs`),
    #: and each successful (`assigned`/`none_assigned`) observation of a
    #: record simply replaces this dict's previous entry for it, so no
    #: explicit run-id comparison is needed; the value present at the end is
    #: always the last one written. Applied over `subject_areas` by
    #: :func:`_finalise_subject_areas`, which also lets an abstract
    #: observation replace a subject-area row a rare search-entry
    #: contribution had already added for the same record (see the module
    #: docstring).
    abstract_subject_areas: dict[str, frozenset[str]] = field(default_factory=dict)
    #: record_ids at least one loaded abstract run described that are not in
    #: `records` (ADR 0018 / BUILD_PLAN §5 risk 8: skipped, never silently
    #: dropped). See `StoreStats.unmatched_abstract_record_ids` for why this
    #: is reported as a set accumulated during the load rather than read back
    #: from a table.
    unmatched_abstract_record_ids: set[str] = field(default_factory=set)


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
        Sealed *search* run directories (those carrying ``manifest.json``,
        BUILD_PLAN §2.2), excluding every non-run directory under ``raw/``
        (:data:`~prismabib.capture.layout.NON_RUN_DIRNAMES`: the shared HTTP
        cache ``_cache``, and ``abstracts/``, whose nested runs carry a
        different manifest schema and whose payloads are Abstract Retrieval
        responses, not search entries) and any unsealed (in-progress or
        interrupted) run. Sorted by directory name, which sorts
        chronologically by construction
        (:func:`prismabib.capture.layout.new_run_id`) -- this is the
        traversal order the module docstring's reproducibility argument
        depends on. Returns ``[]`` if ``raw_dir`` does not exist.
    """
    if not raw_dir.is_dir():
        return []
    candidates = [
        entry
        for entry in raw_dir.iterdir()
        if entry.is_dir() and entry.name not in NON_RUN_DIRNAMES and is_sealed(entry)
    ]
    return sorted(candidates, key=lambda path: path.name)


def _sealed_abstract_run_dirs(raw_dir: Path) -> list[Path]:
    """List every sealed Layer 0 abstract-retrieval run directory, oldest first (ADR 0018).

    Args:
        raw_dir: A project's ``raw/`` directory (``project.raw_dir``).

    Returns:
        Sealed run directories under ``raw/abstracts/`` -- those carrying
        ``manifest.json``, exactly as :func:`_sealed_run_dirs` requires of a
        search run (:func:`~prismabib.capture.layout.is_sealed` answers the
        question for either without knowing which kind it is looking at) --
        sorted by directory name, which sorts chronologically by construction
        (:func:`prismabib.capture.layout.new_run_id`). This is the traversal
        order ADR 0018's "the later run wins" rule for ``subject_areas``
        depends on, and it is the reason abstract runs must be folded in only
        *after* this ordering has been established. An unsealed
        (in-progress or interrupted) abstract run is skipped entirely -- a
        partial load is worse than none. Returns ``[]`` if
        ``raw_dir / "abstracts"`` does not exist.
    """
    abstracts_dir = raw_dir / ABSTRACTS_DIRNAME
    if not abstracts_dir.is_dir():
        return []
    candidates = [entry for entry in abstracts_dir.iterdir() if entry.is_dir() and is_sealed(entry)]
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
            year. :func:`_load_run` catches it and skips that one entry;
            the load as a whole continues.
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
            :func:`_load_run` catches it and skips that one entry; the load
            as a whole continues.
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


def _coredata_from_abstract_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Read one Abstract Retrieval payload line's ``coredata`` object (ADR 0018).

    Args:
        payload: One parsed, verbatim
            ``raw/abstracts/<run_id>/abstracts-NNNN.jsonl`` line -- the whole
            ``{"abstracts-retrieval-response": {...}}`` response, exactly as
            Scopus sent it (:mod:`prismabib.capture.enrich`'s module
            docstring: no envelope of prismabib's own).

    Returns:
        ``payload["abstracts-retrieval-response"]["coredata"]``, or ``None``
        if either key is absent or not a mapping -- a payload this loader
        cannot recover a record id or subject-area data from at all, and
        which :func:`_load_abstract_run` therefore skips with a warning
        rather than crashing the whole build on.
    """
    retrieval = payload.get("abstracts-retrieval-response")
    if not isinstance(retrieval, dict):
        return None
    coredata = retrieval.get("coredata")
    return coredata if isinstance(coredata, dict) else None


def _subject_area_entries_from_abstract_payload(payload: dict[str, Any]) -> list[Any]:
    """Read the raw ``subject-area`` entries out of one Abstract Retrieval payload line.

    Args:
        payload: One parsed, verbatim Abstract Retrieval response; see
            :func:`_coredata_from_abstract_payload`.

    Returns:
        ``payload["abstracts-retrieval-response"]["subject-areas"]["subject-area"]``,
        normalised to a list. Scopus writes this as a lone mapping, not a
        one-item list, when a record has exactly one subject area (the same
        scalar-vs-list inconsistency
        :func:`prismabib.capture.enrich._subject_area_entries` already
        normalises on the write side, for the same reason -- see that
        function's docstring); ``[]`` if the payload carries no
        recognisable subject-area data at all.
    """
    retrieval = payload.get("abstracts-retrieval-response")
    if not isinstance(retrieval, dict):
        return []
    areas = retrieval.get("subject-areas")
    if not isinstance(areas, dict):
        return []
    entries = areas.get("subject-area")
    if isinstance(entries, dict):
        return [entries]
    if isinstance(entries, list):
        return entries
    return []


def _subject_areas_from_abstract_payload(payload: dict[str, Any]) -> list[str]:
    """Extract subject-area codes from one Abstract Retrieval payload line (ADR 0018).

    Args:
        payload: One parsed, verbatim Abstract Retrieval response.

    Returns:
        Every entry's ``@code``, in payload order. Entries without a
        non-empty ``@code`` are dropped.

    ``@code`` **only** -- deliberately not the ``@code or $`` fallback
    :func:`_subject_areas_from_entry` applies to a search entry. The write
    side already decided this question: ``capture.enrich._has_subject_areas``
    counts a record as carrying areas only if some entry has a non-empty
    ``@code``, "because an entry without a code cannot be matched against
    ``criteria.yaml``'s ``subject_areas`` list, so it is not evidence that
    the record has codes", and seals such a record as ``no_subject_areas``.

    Reusing the lenient extraction made the two layers contradict each
    other: Layer 0 said "no areas observed -- keep this record", Layer 1
    stored ``"Artificial Intelligence"`` as an area code, recorded the
    record as ``assigned``, and then *excluded* it, because a
    human-readable name matches no ASJC grouping. A record dropped from a
    published corpus on the strength of a log line is BUILD_PLAN §1.4, so
    the two predicates must agree by construction.
    """
    return [
        code
        for entry in _subject_area_entries_from_abstract_payload(payload)
        if isinstance(entry, dict)
        for code in [entry.get("@code")]
        if isinstance(code, str) and code
    ]


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


def _as_naive_utc(moment: datetime) -> datetime:
    """Normalise a timestamp to naive UTC before it enters DuckDB.

    DuckDB's ``TIMESTAMP`` columns are timezone-naive. Handing one a
    timezone-*aware* datetime makes DuckDB convert it to the host's LOCAL time,
    so the same Layer 0 archive produces different stored values on different
    machines: a manifest recording ``09:00Z`` lands as ``09:00`` on a UTC runner
    and ``03:00`` on a UTC-6 workstation.

    That is not merely a golden-snapshot nuisance. It breaks S03-AC1 ("byte-stable
    checksums on the same Layer 0 input"), and it would break Stage 11's
    reproducibility criterion outright -- "a clean clone on a different machine
    reproduces ``numbers.json``" -- because every citation snapshot date would
    shift by the reader's UTC offset. Two researchers running identical code over
    identical data would publish different dates, with nothing to indicate why.

    Args:
        moment: A timestamp from the run manifest, aware or naive.

    Returns:
        The same instant as a naive datetime in UTC. A naive input is assumed to
        already be UTC and returned unchanged -- the manifest is written by
        prismabib itself, always in UTC.
    """
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _load_run(acc: _Accumulator, raw_dir: Path, run_dir: Path) -> None:
    """Fold one sealed Layer 0 run directory into ``acc``.

    Args:
        acc: The in-progress load accumulator, mutated in place.
        raw_dir: The project's ``raw/`` directory.
        run_dir: The sealed run directory to load (a member of
            :func:`_sealed_run_dirs`'s return value).
    """
    manifest = RunManifest.model_validate_json(
        (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    acc.runs.append(
        (
            manifest.run_id,
            _as_naive_utc(manifest.started_at),
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
    retrieved_at = _as_naive_utc(manifest.started_at)

    for payload_file in manifest.payload_files:
        relative_payload_file = f"{manifest.run_id}/{payload_file}"

        for line_index, entry in _iter_page_entries(run_dir / payload_file):
            if entry.get("error") is not None:
                # Scopus's own placeholder for an empty result set
                # (`{"error": "Result set was empty"}`), not a record.
                continue
            acc.entries_seen += 1
            record_id = _record_id_from_entry(entry)
            if record_id is None:
                # No `eid`, so no record id: this entry cannot become a row in
                # `records` and cannot be keyed against one that already
                # exists. It is reported through the same channel as a failed
                # field parse (ADR 0012) -- it is the same outcome for the
                # corpus, a Layer 0 line that produced no record, and the
                # field would otherwise say "nothing skipped" for a load that
                # dropped a record.
                acc.malformed_entries.append(
                    (
                        manifest.run_id,
                        relative_payload_file,
                        line_index,
                        None,
                        _REASON_MISSING_EID,
                    )
                )
                logger.warning(
                    "store.load.entry_missing_eid",
                    run_id=manifest.run_id,
                    payload_file=payload_file,
                    line=line_index,
                )
                continue

            payload_ref = PayloadRef(path=raw_dir / relative_payload_file, line=line_index)
            cited_by_count = _cited_by_count_from_entry(entry)
            try:
                record = _record_from_entry(entry, record_id=record_id, payload_ref=payload_ref)
            # `prismabib.errors.ValidationError`, deliberately *not*
            # `pydantic.ValidationError`, and the difference is the whole line
            # between "skip this entry" and "abort this load".
            #
            # The only two raisers of the former are `_title_from_entry` and
            # `_cover_date_from_entry`: hand-written checks that Scopus sent a
            # field Scopus always sends. That is a defect in the captured
            # bytes, is confined to the one entry, and must not cost the other
            # thousands -- this aborted the whole load until a real capture hit
            # it, 1 record out of 1,945 with no `dc:title`, leaving the other
            # 1,944 unloadable with no way forward because Layer 0 is
            # immutable and re-capturing means a drifted index.
            #
            # A pydantic failure is the other thing. Every value handed to
            # `Record(...)` has already been read, coerced, and defaulted by
            # the `_*_from_entry` helpers above, so pydantic rejecting one
            # means *prismabib* built a record its own model forbids -- a
            # loader or model defect, not a bad entry. That is not confined to
            # one entry and would silently shrink every corpus it touched, so
            # it still aborts the load, loudly and without a partial store.
            # Skip what Layer 0 got wrong; abort on what prismabib got wrong.
            except ValidationError as exc:
                acc.malformed_entries.append(
                    (
                        manifest.run_id,
                        relative_payload_file,
                        line_index,
                        record_id,
                        _REASON_INVALID_FIELD,
                    )
                )
                logger.warning(
                    "store.load.malformed_entry_skipped",
                    run_id=manifest.run_id,
                    payload_file=payload_file,
                    line=line_index,
                    record_id=record_id,
                    reason=_REASON_INVALID_FIELD,
                    detail=str(exc),
                )
                if cited_by_count is not None:
                    # The citation count is present, parseable, and does not
                    # depend on the field that failed. If some other run
                    # loaded this record, discarding its count would turn a
                    # re-capture into a hole in the citation trend -- "5 as of
                    # January, nothing since" -- for a record that is in the
                    # store. Held back rather than written, because with no
                    # foreign keys in the schema a snapshot for a record no
                    # run loaded would be an orphan nothing would catch.
                    acc.pending_citation_snapshots[(record_id, retrieved_at)] = cited_by_count
                continue

            if cited_by_count is not None:
                acc.citation_snapshots[(record_id, retrieved_at)] = cited_by_count

            if record_id in acc.seen_record_ids:
                # Re-capture of a paper already loaded from an earlier run:
                # only the citation snapshot above is new; see the module
                # docstring's "Re-captured records" section.
                #
                # Counted here because here is the only place it is
                # observable. `records.run_id` keeps the *first* run that
                # loaded a record, so once the load finishes nothing in
                # Layer 1 can say how many runs a record appeared in. PRISMA
                # needs that number for its "duplicates removed before
                # screening" box, and deriving it as a remainder
                # (`identified - |S_raw|`) would make the flow diagram's
                # first equation an identity that cannot fail -- absorbing a
                # manifest that disagrees with the corpus, which is the one
                # defect BUILD_PLAN line 993 says the guard exists to catch.
                # Only a search finding a paper it has not already contributed
                # is a PRISMA duplicate. A refresh of the same query re-finding
                # its own results is not: `identified` counts each distinct
                # query once, so subtracting again would break equation 1.
                if (manifest.query, record_id) not in acc.counted_query_records:
                    acc.counted_query_records.add((manifest.query, record_id))
                    acc.cross_run_duplicates[manifest.run_id] = (
                        acc.cross_run_duplicates.get(manifest.run_id, 0) + 1
                    )
                continue
            acc.seen_record_ids.add(record_id)
            acc.counted_query_records.add((manifest.query, record_id))

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
            # which `prismabib.capture.enrich` now calls (ADR 0011/0018) but only ever
            # to read subject areas out of, not indexed keywords, so this stays
            # unreachable. The `keywords.kind` column already models `"index"`, so
            # this loop is the correct code waiting on its data source -- a
            # `# pragma: no cover` would hide that it is dormant, and deleting it would
            # leave a future stage to rediscover the requirement.
            for term in record.index_keywords:
                _accumulate_keyword(acc, record_id=record_id, raw_term=term, kind="index")

            # A rare, currently-unobserved search entry carrying its own
            # `subject-area` array (see the module docstring). Superseded outright by
            # any Abstract Retrieval observation of the same record --
            # `_finalise_subject_areas` applies `acc.abstract_subject_areas` over
            # this set once every run has been folded in.
            for area_code in record.subject_areas:
                acc.subject_areas.add((record_id, area_code))


def _load_abstract_run(acc: _Accumulator, run_dir: Path) -> None:
    """Fold one sealed Layer 0 abstract-retrieval run directory into ``acc`` (ADR 0018).

    Args:
        acc: The in-progress load accumulator, mutated in place. Must
            already reflect every search run -- ``acc.seen_record_ids`` is
            how a record this run covers is checked against ``records``.
        run_dir: The sealed abstract run directory to load (a member of
            :func:`_sealed_abstract_run_dirs`'s return value). Unlike
            :func:`_load_run`, no project-relative ``raw_dir`` is needed:
            neither ``abstract_runs`` nor ``record_subject_area_coverage``
            carries a payload provenance column (see ``schema.sql``), so
            there is no ``PayloadRef`` to build here.

    Writes no ``runs`` row: an abstract run identifies no record (ADR 0011 /
    ADR 0018's "``runs`` gains no row"). For every record this run describes
    that *is* in ``acc.seen_record_ids``, appends one
    ``record_subject_area_coverage`` row and, for the two statuses that
    reflect a successful fetch (``"assigned"``/``"none_assigned"``), records
    this run's codes as the current winner in ``acc.abstract_subject_areas``
    -- later calls (for a later-sorted run) simply overwrite that entry, so
    "the later run wins" falls out of traversal order
    (:func:`_sealed_abstract_run_dirs`) with no explicit comparison. A
    record this run describes that is *not* in ``acc.seen_record_ids`` is
    added to ``acc.unmatched_abstract_record_ids`` instead and contributes
    no row at all.
    """
    manifest = AbstractRunManifest.model_validate_json(
        (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    acc.abstract_runs.append(
        (
            manifest.run_id,
            _as_naive_utc(manifest.started_at),
            _as_naive_utc(manifest.finished_at),
            manifest.endpoint,
            manifest.view,
            manifest.records_requested,
            manifest.records_fetched,
            manifest.payload_sha256,
            manifest.client_version,
            manifest.criteria_version,
        )
    )

    # record_id -> (status, area codes) this run's payload lines observed.
    # A record id repeated across this run's own payload lines is not
    # expected in a real capture (each record is requested once) but is not
    # assumed; a repeat simply resolves to whichever line this run's own
    # file/line iteration visits last, deterministically.
    observed: dict[str, tuple[str, frozenset[str]]] = {}
    for payload_file in manifest.payload_files:
        for _, entry in _iter_page_entries(run_dir / payload_file):
            coredata = _coredata_from_abstract_payload(entry)
            if coredata is None:
                logger.warning(
                    "store.load.abstract_entry_missing_coredata",
                    run_id=manifest.run_id,
                    payload_file=payload_file,
                )
                continue
            record_id = _record_id_from_entry(coredata)
            if record_id is None:
                logger.warning(
                    "store.load.abstract_entry_missing_eid",
                    run_id=manifest.run_id,
                    payload_file=payload_file,
                )
                continue
            codes = frozenset(_subject_areas_from_abstract_payload(entry))
            observed[record_id] = ("assigned" if codes else "none_assigned", codes)

    # `AbstractUnavailable` entries with reason "not_found"/"not_entitled" have no
    # payload line at all -- there was nothing to fetch, so `observed` above never
    # sees them. "no_subject_areas" is deliberately skipped here: its payload line
    # *is* written (`AbstractUnavailableReason`'s docstring), so it is already
    # `observed` above with status "none_assigned"; consulting `unavailable` for it
    # too would be redundant confirmation, not additional information. The two
    # reasons handled here are exactly `record_subject_area_coverage.status`'s two
    # non-"assigned"/"none_assigned" values (ADR 0018's closed vocabulary), so no
    # translation is needed.
    for unavailable in manifest.unavailable:
        if unavailable.reason == "no_subject_areas":
            continue
        observed[unavailable.record_id] = (unavailable.reason, frozenset())

    for record_id, (status, codes) in observed.items():
        if record_id not in acc.seen_record_ids:
            # Described by this abstract run, but no search run ever loaded it
            # into `records` -- nothing to attach a coverage or subject-area row
            # to. Counted, not silently dropped (BUILD_PLAN §5 risk 8).
            acc.unmatched_abstract_record_ids.add(record_id)
            continue
        acc.record_subject_area_coverage.append((record_id, manifest.run_id, status))
        if status in ("assigned", "none_assigned"):
            # A "not_found"/"not_entitled" run observed no new data about this
            # record's subject areas and must not erase an earlier run's real
            # observation, so only the two successful statuses reach here.
            acc.abstract_subject_areas[record_id] = codes


def _finalise_subject_areas(acc: _Accumulator) -> None:
    """Apply Abstract Retrieval subject-area data over ``subject_areas`` (ADR 0018).

    Args:
        acc: The accumulator, after every sealed run -- search and abstract
            alike -- has been folded in. ``acc.subject_areas`` is replaced
            in place; ``acc.abstract_subject_areas`` is left untouched (it
            is not written anywhere else, but mutating it here would make
            this function's idempotency dependent on call order).

    A record with a winning entry in ``acc.abstract_subject_areas`` (already
    resolved to the latest covering run's codes by :func:`_load_abstract_run`)
    has *every* existing ``subject_areas`` row for it -- whether contributed
    by a rare search entry that happened to carry its own ``subject-area``
    array, or by an earlier abstract run -- replaced by that winning set,
    which may be empty (``"none_assigned"``: Scopus was asked and assigned
    none, which is not the same as no data at all; that distinction survives
    only in ``record_subject_area_coverage`` for such a record, never in
    ``subject_areas`` itself, which has no way to represent "asked, got
    nothing" as opposed to "never asked").
    """
    if not acc.abstract_subject_areas:
        return
    overridden = acc.abstract_subject_areas.keys()
    kept = {
        (record_id, area_code)
        for record_id, area_code in acc.subject_areas
        if record_id not in overridden
    }
    replaced = {
        (record_id, area_code)
        for record_id, codes in acc.abstract_subject_areas.items()
        for area_code in codes
    }
    acc.subject_areas = kept | replaced


def _resolve_pending_snapshots(acc: _Accumulator) -> None:
    """Promote a skipped entry's citation snapshot iff its record was loaded.

    A citation count is independent of the field that made its entry
    malformed. When the same record was loaded from another run -- the
    ordinary case for a re-capture, which is the only reason to run a second
    search at all -- the count is a real, dated observation about a record
    that is in the store, and dropping it leaves a chart reading "5 as of
    January, nothing since" for a paper whose February count was captured,
    parsed, and thrown away.

    When no run loaded the record, the count is dropped. ``schema.sql``
    declares no foreign keys anywhere, so a ``citation_snapshots`` row for an
    absent ``record_id`` would be rejected by neither DuckDB nor any query
    that joins -- it would simply vanish from every join and inflate
    ``citation_snapshots_loaded``. That is why these are held back through the
    whole load instead of being written where they are found: whether a
    record was loaded is not knowable until every run has been walked, since
    the malformed capture may sort before the well-formed one.

    Args:
        acc: The accumulator, after every sealed run has been folded in.
            ``acc.pending_citation_snapshots`` is emptied.
    """
    for key, count in acc.pending_citation_snapshots.items():
        record_id, _ = key
        if record_id in acc.seen_record_ids:
            # `setdefault`, not assignment: a well-formed entry's count for
            # the same (record, run) always wins over a malformed one's.
            acc.citation_snapshots.setdefault(key, count)
    acc.pending_citation_snapshots.clear()


def _guard_against_unloadable_capture(acc: _Accumulator) -> None:
    """Refuse to write a store when the skipped share means the capture is broken.

    Skipping a malformed entry is right for *an* entry and wrong for a
    capture. With no floor under it, stripping ``dc:title`` from all 120
    entries of the reference fixture returned normally with
    ``records_loaded=0`` and ``prismabib build --rebuild`` exited ``0``
    printing ``records 0`` and a cheerful next step -- a plausible wrong
    number in a published paper, which is the failure BUILD_PLAN §1.4 exists
    to prevent. There has to be a line between "one bad record" and "this
    capture is broken", and past it the honest outcome is no store at all.

    Two rules, both deliberately conservative:

    * **Nothing loaded.** Every entry considered was skipped. There is no
      judgement call here: a store with zero records built from a Layer 0
      that had entries in it is broken by definition.
    * **A systematic share.** More than
      ``_MAX_SKIPPED_ENTRY_RATIO`` (5%) of the entries were skipped, *and* at
      least ``_MIN_SKIPS_FOR_RATIO_GUARD`` (10) of them were. 5% is two
      orders of magnitude above the only rate ever observed in a real capture
      (1 entry in 1,945, 0.05%), so a normal capture does not approach it,
      while a wrong parser, a truncated download, or a capture of the wrong
      response shape fails on a large fraction rather than a handful. The
      floor of 10 exists because a ratio alone would fire on 1 bad entry in a
      15-entry pilot capture, which is exactly the case skipping was
      introduced to survive; below 10 skips the ratio is not consulted at
      all.

    Neither rule is a substitute for reading ``malformed_entries``. A skip
    count under both thresholds is still reported, logged, and persisted.

    Args:
        acc: The accumulator, after every sealed run has been folded in.

    Raises:
        StoreError: If the skipped share trips either rule.
    """
    skipped = len(acc.malformed_entries)
    seen = acc.entries_seen
    if skipped == 0 or seen == 0:
        return

    nothing_loaded = skipped == seen
    systematic = skipped >= _MIN_SKIPS_FOR_RATIO_GUARD and skipped > _MAX_SKIPPED_ENTRY_RATIO * seen
    if not (nothing_loaded or systematic):
        return

    named = [f"{payload_file}:{line}" for _, payload_file, line, _, _ in acc.malformed_entries]
    shown = ", ".join(named[:_MAX_LOGGED_MALFORMED_ENTRIES])
    if len(named) > _MAX_LOGGED_MALFORMED_ENTRIES:
        shown += f", ... and {len(named) - _MAX_LOGGED_MALFORMED_ENTRIES:,} more"
    raise StoreError(
        f"refusing to build a store from this capture: {skipped:,} of the {seen:,} Layer 0 "
        f"entries could not be turned into a record, leaving {seen - skipped:,}. "
        "That is a broken capture, not a few bad records, so no store has been written -- "
        "a corpus this much smaller than its capture would look complete to every later "
        f"stage. First entries: {shown}. Layer 0 is untouched: inspect those lines, and if "
        "the entries really are what Scopus sent, capture again. "
        f"(The threshold is every entry skipped, or more than "
        f"{_MAX_SKIPPED_ENTRY_RATIO:.0%} of them with at least "
        f"{_MIN_SKIPS_FOR_RATIO_GUARD} skips.)"
    )


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

    # `dtype=object` keeps pandas' type inference out of the byte-stable write path
    # entirely. Without it a nullable integer column is inferred as float64 and a NULL
    # becomes NaN, so `total_results` would round-trip through a float on its way into
    # DuckDB -- an inference step whose behaviour can change between pandas versions,
    # sitting directly under S03-AC1's byte-stability claim. DuckDB casts each value to
    # the column's declared type on insert, so the schema stays the single source of
    # truth for types. It is also measurably faster on large batches.
    frame = pd.DataFrame(
        list(rows), columns=[f"c{index}" for index in range(len(rows[0]))], dtype=object
    )
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
    _insert_rows(connection, "malformed_entries", acc.malformed_entries)
    _insert_rows(
        connection,
        "run_duplicates",
        # sorted: dict order is insertion order, and a checksummed table
        # must not depend on which run happened to be walked first.
        [(run_id, count) for run_id, count in sorted(acc.cross_run_duplicates.items())],
    )
    _insert_rows(connection, "abstract_runs", acc.abstract_runs)
    _insert_rows(
        connection,
        "record_subject_area_coverage",
        # sorted for the same reason as `run_duplicates` above: `observed`
        # inside `_load_abstract_run` is a dict, and a checksummed table must
        # not depend on that iteration order.
        sorted(acc.record_subject_area_coverage),
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


def _stats_from_connection(
    connection: duckdb.DuckDBPyConnection,
    *,
    rebuilt: bool,
    unmatched_abstract_record_ids: tuple[str, ...] = (),
) -> StoreStats:
    """Compute a :class:`StoreStats` snapshot from a store's current content.

    Every field except ``unmatched_abstract_record_ids``, ``malformed_entries_skipped``
    included, comes from a query against ``connection`` and from nothing else. That is
    what makes the ``rebuild=False`` reuse path -- the path ``prismabib build`` takes by
    default -- report the same skips as the rebuild that created the store, instead of
    an empty tuple that reads as "nothing was skipped" (ADR 0012).

    Args:
        connection: An open DuckDB connection onto a Layer 1 store whose
            schema has already been created.
        rebuilt: The value to report as ``StoreStats.rebuilt``.
        unmatched_abstract_record_ids: Forwarded straight through to the
            returned :class:`StoreStats` (see that field's own docstring for
            why it cannot be read back from ``connection`` the way every
            other field here is). Defaults to ``()``, which is what the
            ``rebuild=False`` reuse path in :func:`build_store` passes --
            deliberately, not by omission.

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

    # Sorted in SQL rather than by insertion order: this is read back on a
    # path that never saw the load, so there is no insertion order to inherit,
    # and an ORDER BY makes the two paths agree by construction.
    malformed_rows = connection.execute(
        "SELECT payload_file, payload_line FROM malformed_entries "
        "ORDER BY payload_file, payload_line"
    ).fetchall()

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
        malformed_entries_skipped=tuple(
            f"{payload_file}:{payload_line}" for payload_file, payload_line in malformed_rows
        ),
        unmapped_country_values=tuple(unmapped),
        abstract_runs_loaded=_count(connection, "abstract_runs"),
        record_subject_area_coverage_loaded=_count(connection, "record_subject_area_coverage"),
        unmatched_abstract_record_ids=unmatched_abstract_record_ids,
    )


def _delete_stale_store(db_path: Path) -> None:
    """Remove an existing store file before rebuilding it, or say why it cannot.

    A rebuild has to start from an empty file -- that is what makes Layer 1
    a pure function of Layer 0. On POSIX the unlink always succeeds, even
    with the file open, so this used to be a bare ``unlink()``. Windows does
    not work that way: while *any* handle is open on the database, deleting
    it raises ``PermissionError`` with no indication of what is holding it,
    and the most likely holder is the caller's own still-open ``Corpus`` or
    a notebook kernel from earlier in the session.

    Args:
        db_path: The existing store file to delete.

    Raises:
        StoreError: If the file cannot be deleted, naming the real cause.
    """
    try:
        db_path.unlink()
    except OSError as exc:
        raise StoreError(
            f"cannot rebuild {db_path}: the existing store could not be deleted ({exc}). "
            "A rebuild starts from an empty database, and on Windows the file cannot be "
            "removed while any connection to it is open. Close every Corpus and DuckDB "
            "connection to this project -- including one held by another notebook kernel "
            "or another prismabib process -- and run the rebuild again. Nothing has been "
            "changed."
        ) from exc


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

    A Layer 0 entry that cannot be turned into a record does not abort the
    load. It is skipped, written to ``malformed_entries``, reported in
    ``StoreStats.malformed_entries_skipped``, logged individually, and warned
    about once at the end -- but only up to a point: past
    :func:`_guard_against_unloadable_capture`'s threshold the capture is
    broken rather than blemished, and this raises instead of returning a
    corpus that is quietly short.

    Returns:
        Summary counts for the store as it now stands; see
        :class:`StoreStats`. On the ``rebuild=False`` reuse path, every field
        -- ``duplicate_doi_groups``, ``duplicate_records``,
        ``unmapped_country_values`` and ``malformed_entries_skipped``
        included -- is recomputed fresh from the existing table content via
        the same queries as a full build (they are cheap, stateless queries,
        not a cached value that could go stale). Only the *loading itself* is
        skipped, never the reporting.

    Raises:
        StoreError: If ``project.db_path`` exists, ``rebuild`` is
            ``False``, and it does not look like a Layer 1 store this
            function created (e.g. wrong schema); if an existing store
            cannot be deleted before a rebuild (see
            :func:`_delete_stale_store`); if so many Layer 0 entries could
            not be turned into records that the capture itself is unusable
            (see :func:`_guard_against_unloadable_capture`, which leaves no
            store behind rather than a half-loaded one); or if DuckDB
            refuses to open the file for any other reason.
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
        _delete_stale_store(db_path)

    try:
        connection = connect(project, read_only=False)
        try:
            _reset_schema(connection)
            accumulator = _Accumulator()
            for run_dir in _sealed_run_dirs(project.raw_dir):
                _load_run(accumulator, project.raw_dir, run_dir)
            _resolve_pending_snapshots(accumulator)
            _guard_against_unloadable_capture(accumulator)
            # Abstract runs are folded in only after every search run: their
            # records must already be in `accumulator.seen_record_ids` for
            # `_load_abstract_run` to tell "in this corpus" from "unmatched"
            # apart, and their subject-area data must be able to supersede
            # anything `_load_run` accumulated (ADR 0018). Never subject to
            # the malformed-entry ratio guard above -- that guard exists for
            # a broken *search* capture, and an abstract run identifies no
            # record for it to have an opinion about.
            for abstract_run_dir in _sealed_abstract_run_dirs(project.raw_dir):
                _load_abstract_run(accumulator, abstract_run_dir)
            _finalise_subject_areas(accumulator)
            _write_accumulator(connection, accumulator)
            stats = _stats_from_connection(
                connection,
                rebuilt=True,
                unmatched_abstract_record_ids=tuple(
                    sorted(accumulator.unmatched_abstract_record_ids)
                ),
            )
        finally:
            connection.close()
    except StoreError:
        # A refused build leaves *no* store, not an empty or half-written
        # one. `connect` creates the file before anything is loaded, and a
        # store that exists is a store the next `prismabib build` (no
        # `--rebuild`) reuses and reports as a clean load -- which is how a
        # broken capture would become a plausible wrong number after all.
        # The store is derived data; its absence means "not built", which is
        # the truth.
        with suppress(OSError):
            db_path.unlink(missing_ok=True)
        raise

    if stats.malformed_entries_skipped:
        skipped = stats.malformed_entries_skipped
        logger.warning(
            "store.load.malformed_entries_skipped",
            count=len(skipped),
            entries=skipped[:_MAX_LOGGED_MALFORMED_ENTRIES],
            truncated=len(skipped) > _MAX_LOGGED_MALFORMED_ENTRIES,
        )
    if stats.unmapped_country_values:
        logger.warning("store.load.unmapped_countries", values=stats.unmapped_country_values)
    if stats.unmatched_abstract_record_ids:
        unmatched = stats.unmatched_abstract_record_ids
        logger.warning(
            "store.load.unmatched_abstract_records",
            count=len(unmatched),
            record_ids=unmatched[:_MAX_LOGGED_MALFORMED_ENTRIES],
            truncated=len(unmatched) > _MAX_LOGGED_MALFORMED_ENTRIES,
        )
    # `malformed_entries_skipped`/`unmatched_abstract_record_ids` are replaced by
    # their lengths here on purpose: the real capture that motivated the former had
    # 1 skip, but a bad one has thousands, and a single log event carrying every
    # reference is unreadable and expensive. The references are in
    # `malformed_entries` (queryable) or the warning above, respectively.
    logger.info(
        "store.build_store.complete",
        **stats.model_dump(exclude={"malformed_entries_skipped", "unmatched_abstract_record_ids"}),
        malformed_entries_skipped_count=len(stats.malformed_entries_skipped),
        unmatched_abstract_record_ids_count=len(stats.unmatched_abstract_record_ids),
    )
    return stats


#: The non-``RAW`` stages whose membership is a pure function of Layer 1 and
#: ``criteria.yaml``, with no dependence on the Layer 2 decision log
#: (BUILD_PLAN line 950). Answered without loading ``decisions.jsonl`` at all.
_LAYER1_ONLY_STAGES: Final[frozenset[PrismaStage]] = frozenset(
    {PrismaStage.AUTOMATED, PrismaStage.LANGUAGE}
)


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
        # Set by `open()`, never by this constructor: a bare
        # `Corpus(connection)` has no `Project` to resolve `criteria.yaml`
        # or the decision log against, so `records`/`keywords` for a
        # non-RAW stage must raise rather than silently having nothing to
        # delegate to. See `_prisma_stage_record_ids`.
        self._project: Project | None = None

    @classmethod
    def open(cls, project: Project, *, read_only: bool = True) -> Corpus:
        """Open ``project``'s Layer 1 store as a :class:`Corpus`.

        Args:
            project: The project whose store to open.
            read_only: Forwarded to :func:`prismabib.store.db.connect`.

        Returns:
            A new :class:`Corpus` handle, bound to ``project`` so
            :meth:`records`/:meth:`keywords` can answer a non-``RAW``
            stage.

        Raises:
            StoreError: If the store cannot be opened; see
                :func:`prismabib.store.db.connect`.
        """
        instance = cls(connect(project, read_only=read_only))
        instance._project = project
        return instance

    def _prisma_stage_record_ids(self, stage: PrismaStage) -> frozenset[str]:
        """Delegate a non-``RAW`` :class:`~prismabib.stage.PrismaStage` to the Stage 4 engine.

        Args:
            stage: Any :class:`~prismabib.stage.PrismaStage` other than
                :attr:`~prismabib.stage.PrismaStage.RAW`.

        Returns:
            The record ids belonging to that PRISMA-flow set, per
            :mod:`prismabib.prisma.engine`.

        Raises:
            StoreError: If this :class:`Corpus` was constructed directly
                from a connection (:meth:`Corpus.__init__`) rather than via
                :meth:`Corpus.open`, so no :class:`~prismabib.project.Project`
                is available to resolve ``criteria.yaml`` or the decision
                log against.
            ConfigError: Forwarded from :mod:`prismabib.prisma.engine` if
                ``project.criteria.yaml`` fails to parse.
            LogError: Forwarded from :mod:`prismabib.prisma.engine` if the
                decision log fails to load. Raised only for the screened
                stages (``TITLE_ABSTRACT``/``FULLTEXT``/``INCLUDED``).
                ``AUTOMATED`` and ``LANGUAGE`` are pure functions of Layer 1
                and ``criteria.yaml`` (BUILD_PLAN line 950), so they are
                answered without reading the log at all and cannot fail on
                one that is corrupt or absent.
        """
        if self._project is None:
            raise StoreError(
                f"Corpus.records/keywords(stage={stage!r}) needs a project-bound "
                "Corpus -- open it with Corpus.open(project, ...) rather than "
                "constructing Corpus(connection) directly, so the Stage 4 PRISMA "
                "engine has a Project to resolve criteria.yaml and the decision "
                "log against."
            )
        # Function-local, not module-level: see the module docstring's
        # "PrismaStage delegates to the Stage 4 PRISMA engine" section for
        # why `store/` must not import `prisma/` at import time.
        #
        # `_capture_snapshot`, not the seven public `engine` functions, for
        # two reasons.
        #
        # It accepts an already-open connection, and this Corpus already
        # holds one. Each public function opens its own read-only connection
        # instead, and DuckDB refuses a second connection to the same file
        # from one process when the two disagree about configuration -- so
        # `Corpus.open(project, read_only=False)` (a frozen signature,
        # therefore a reachable public path) followed by `.records()` for
        # any non-RAW stage died on "Can't open a connection to same
        # database file with a different configuration than existing
        # connections".
        #
        # And it reads Layer 1, `criteria.yaml` and the decision log exactly
        # once, so the record ids returned here and the rows selected for
        # them describe one instant rather than several.
        from prismabib.prisma.engine import _capture_layer1, _capture_snapshot

        # `AUTOMATED` and `LANGUAGE` are pure functions of Layer 1 and
        # `criteria.yaml` (BUILD_PLAN line 950: computed, never logged), so
        # they are answered from a Layer 1 view that never touches Layer 2.
        # Reading the decision log for them would be worse than wasteful: it
        # would make `Corpus.records(stage=LANGUAGE)` fail with `LogError` on
        # a corrupt log whose contents cannot affect the answer, and -- since
        # `DecisionLog` opens `decisions.jsonl` with `O_CREAT` -- would
        # create a screening log for a project that has never screened.
        if stage in _LAYER1_ONLY_STAGES:
            layer1 = _capture_layer1(self._project, connection=self._connection)
            return layer1.automated if stage is PrismaStage.AUTOMATED else layer1.language

        snapshot = _capture_snapshot(self._project, connection=self._connection)
        # Every screened member of PrismaStage, so a stage added to the enum
        # without a set here fails the exhaustiveness check below rather
        # than silently returning the wrong flow set.
        stage_to_ids: dict[PrismaStage, frozenset[str]] = {
            PrismaStage.TITLE_ABSTRACT: snapshot.manual_abstract,
            PrismaStage.FULLTEXT: snapshot.manual_fulltext,
            # C == M_full (BUILD_PLAN line 948); PrismaStage.INCLUDED and
            # PrismaStage.FULLTEXT therefore name the same set by design.
            PrismaStage.INCLUDED: snapshot.manual_fulltext,
        }
        try:
            return stage_to_ids[stage]
        except KeyError:  # pragma: no cover - RAW never reaches here
            raise StoreError(
                f"Corpus._prisma_stage_record_ids received stage {stage!r}, which has "
                "no PRISMA-flow set. RAW is answered from Layer 1 directly by the "
                "caller and must never reach this method."
            ) from None

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
        # `infer_schema_length=None` scans every row instead of polars' default first
        # 100. With the default, a column whose first 100 values are NULL is typed
        # Null, and row 101 raises `ComputeError: could not append value`. That is
        # ordinary data, not a pathological case: `records.doi` is nullable and a
        # corpus can easily open with 100 DOI-less conference papers. The 120-record
        # fixture cannot surface it, and it would first appear on a real corpus as a
        # crash inside the frozen `Corpus` contract.
        return pl.DataFrame(rows, schema=columns, orient="row", infer_schema_length=None)

    def records(self, stage: PrismaStage = PrismaStage.INCLUDED) -> pl.DataFrame:
        """Return the ``records`` table for one named PRISMA set.

        Args:
            stage: Which PRISMA-flow record set to return.
                :attr:`~prismabib.stage.PrismaStage.RAW` (``S_raw``, every
                captured record) is answered directly from Layer 1; every
                other member is delegated to
                :meth:`_prisma_stage_record_ids` (the Stage 4 PRISMA
                engine) -- see the module docstring.

        Returns:
            Every column of ``records``, one row per record, ordered by
            ``record_id``.

        Raises:
            StoreError: If ``stage`` is not
                :attr:`~prismabib.stage.PrismaStage.RAW` and this
                :class:`Corpus` was not opened via :meth:`Corpus.open`; see
                :meth:`_prisma_stage_record_ids`.
            ConfigError: See :meth:`_prisma_stage_record_ids`.
            LogError: See :meth:`_prisma_stage_record_ids`.
        """
        if stage is PrismaStage.RAW:
            return self._query("SELECT * FROM records ORDER BY record_id")
        record_ids = self._prisma_stage_record_ids(stage)
        return self._query(
            "SELECT * FROM records WHERE record_id = ANY(?) ORDER BY record_id",
            [sorted(record_ids)],
        )

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
            stage: See :meth:`records`; the same delegation applies.

        Returns:
            One row per (record, keyword) occurrence of ``kind``:
            ``record_id``, ``keyword_id``, ``term_raw``, ``term_norm``,
            ``kind``; ordered by ``record_id``, then ``term_norm``.

        Raises:
            StoreError: See :meth:`records`.
            ConfigError: See :meth:`_prisma_stage_record_ids`.
            LogError: See :meth:`_prisma_stage_record_ids`.
        """
        if stage is PrismaStage.RAW:
            return self._query(
                "SELECT rk.record_id, k.keyword_id, k.term_raw, k.term_norm, rk.kind "
                "FROM record_keywords rk JOIN keywords k ON k.keyword_id = rk.keyword_id "
                "WHERE rk.kind = ? ORDER BY rk.record_id, k.term_norm",
                [kind],
            )
        record_ids = self._prisma_stage_record_ids(stage)
        return self._query(
            "SELECT rk.record_id, k.keyword_id, k.term_raw, k.term_norm, rk.kind "
            "FROM record_keywords rk JOIN keywords k ON k.keyword_id = rk.keyword_id "
            "WHERE rk.kind = ? AND rk.record_id = ANY(?) ORDER BY rk.record_id, k.term_norm",
            [kind, sorted(record_ids)],
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
        # Normalise the QUERY bound the same way the stored values were normalised on
        # write. Guarding only the write path is not enough: DuckDB converts an aware
        # datetime to host-local time here too, so the identical call against the
        # identical store returned 120 rows under UTC and 0 under UTC-6. Passing
        # `manifest.started_at` -- the most natural argument there is, and itself
        # timezone-aware -- lands exactly on the boundary, so the comparison flips
        # from "everything" to "nothing" purely on the reader's location.
        return self._query(sql, [_as_naive_utc(at)])


__all__ = ["Corpus", "StoreStats", "build_store"]
