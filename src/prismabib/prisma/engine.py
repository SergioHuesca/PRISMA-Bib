"""The PRISMA set-theoretic screening engine (BUILD_PLAN §Stage 4, lines 939-951, 999-1002).

This module is exactly the formal-set-to-function table BUILD_PLAN pins
(lines 941-948), one function per row: :func:`raw_set` (``S_raw``),
:func:`automated_set` (``A``), :func:`language_set` (``L``),
:func:`manual_abstract_set` (``M_abs``), :func:`manual_fulltext_set`
(``M_full``), :func:`corpus` (``C``), and :func:`replay` for the criteria
amendment workflow. Nothing else in this codebase computes PRISMA-flow
record membership; a future module that needs to know "is this record
currently included" calls one of these seven functions rather than
re-deriving the answer.

**Every function here reads each source exactly once.** ``project.criteria``
re-parses ``criteria.yaml`` on every access and
:meth:`~prismabib.prisma.log.DecisionLog.fold` re-reads ``decisions.jsonl``
on every call -- both deliberately, so that an amendment or a second
reviewer's decision takes effect immediately. The corollary is that a
computation which reads either source twice can straddle a change to it and
produce numbers that describe two different instants. :func:`_capture_layer1`
and :func:`_capture_snapshot` are the two private entry points that take a
single consistent read (one criteria parse, one Layer 1 read, one fold) and
derive every set from it; the public functions below are thin wrappers over
them, and :func:`prismabib.prisma.flow.compute_flow_counts` -- which needs
half a dozen mutually-consistent counts at once -- takes one snapshot rather
than calling the wrappers in turn. Both accept an
already-open DuckDB connection, which is what lets
:meth:`prismabib.store.load.Corpus.records` resolve a named
:class:`~prismabib.stage.PrismaStage` against the connection it already holds
-- no public function below can take one without changing a frozen
signature, and DuckDB refuses a second, differently-configured connection to
one file from one process.

**Why ``A`` and ``L`` never read the decision log (BUILD_PLAN line 950).**
Both are pure functions of ``project.criteria`` (``criteria.yaml``) and
Layer 1 (the DuckDB store) alone, recomputed from scratch on every call.
Nothing in this module caches, memoises, or persists their result, and
neither reads :mod:`prismabib.prisma.log` at all -- so no sequence of
logged decisions, however constructed, can ever change what ``A`` or ``L``
contain for a fixed corpus and criteria. This is the property BUILD_PLAN
calls out by name ("a human decision can never widen an automated set")
and the one a dedicated property test enforces directly.

**Latitude taken filling in BUILD_PLAN's set definitions.** The table at
lines 941-948 names what each set *is* ("year ∧ subject ∧ doctype", "A
further filtered by language") but not the exact per-record matching rules
against the Layer 1 schema BUILD_PLAN itself froze in ``schema.sql`` and
``criteria.yaml``'s worked example. Every judgement call below is
documented at the function that makes it; the summary:

- ``doc_types.include`` is written in Scopus *code* form in BUILD_PLAN's
  own example (``[ar, cp]``), but ``records.doc_type`` (BUILD_PLAN
  ``store/load.py::_doc_type_from_entry``) is populated with the
  *description* form (``"Conference Paper"``) whenever the captured entry
  carries one -- which the reference fixture project always does. Matching
  only the literal code would therefore make ``automated_set()`` empty on
  the one real corpus this codebase ships. :data:`_DOC_TYPE_CODE_TO_DESCRIPTION`
  is a small, closed, Scopus-code-to-description table (same shape and
  spirit as ``store/load.py``'s own ``_AGGREGATION_TYPE_TO_VENUE_TYPE``)
  that lets a record match on either form. See :func:`_doc_type_matches`.
- ``doc_types.conference_whitelist`` is checked against Layer 1's own
  ``venues.venue_type == "conference"`` classification, not against a
  ``doc_type`` string -- the store already computed that classification
  reliably at build time (``store/load.py::_venue_type_from_entry``), and
  BUILD_PLAN's example codes cannot reliably distinguish "conference" from
  the venue name text alone. See :func:`_passes_conference_whitelist`.
- An **empty** ``criteria.yaml`` list (``subject_areas: []``,
  ``doc_types.include: []``, ``doc_types.conference_whitelist: []``,
  ``languages: []``) means *no restriction on that dimension*, not "match
  nothing". This is the only reading consistent with
  :meth:`~prismabib.project.Project.init`'s default, minimally-populated
  ``criteria.yaml`` (every one of these lists starts empty) not silently
  producing an empty ``automated_set()`` before an operator has edited it.
- A record with **no Layer 1 data on a given dimension** (no
  ``subject_areas`` rows, a ``NULL`` ``language``) never causes an
  automated exclusion on that dimension, even when
  ``criteria.yaml`` restricts it. This matters concretely for subject
  areas: ``store/load.py``'s module docstring documents that the Scopus
  Search API ``view=COMPLETE`` -- the only view this codebase currently
  captures -- never carries subject-area codes, so under a strict reading
  every record in every store built so far would fail a non-empty
  ``subject_areas`` filter and ``automated_set()`` would be silently empty
  for the reference project. Treating "no data" as "not excludable on this
  dimension" is what keeps a data-source limitation from masquerading as a
  screening decision. See :func:`_passes_subject_areas`/:func:`_passes_language`.
- The decision-log fold key is ``(stage, record_id, reviewer)`` -- more
  than one reviewer can log an independent decision for the same record at
  the same stage, and BUILD_PLAN's Stage 4 table does not specify a
  consensus rule for a disagreement (adjudication is a Stage 5
  screening-UI concern). :func:`_aggregate_record_decisions` applies the
  most conservative rule available from the data alone: any "exclude"
  wins, else any "unsure" wins, else "include" only if every reviewer who
  decided said "include". See that function's docstring for the full
  rationale, including how a reported ``reason_code`` is chosen when
  reviewers disagree on why.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

import duckdb

from prismabib.errors import ConfigError
from prismabib.prisma.criteria import resolve_criteria
from prismabib.prisma.events import Decision, DecisionEvent
from prismabib.prisma.log import DecisionLog, FoldKey
from prismabib.project import Criteria, Project
from prismabib.stage import PrismaStage
from prismabib.store.db import connect

# ---------------------------------------------------------------------------
# Layer 1 record attributes (the raw material for A and L)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordAttributes:
    """The Layer 1 metadata one record needs to evaluate ``automated_set``/``language_set``.

    Every field is read directly from Layer 1 (``records``, ``venues``,
    ``subject_areas``) -- never from the decision log -- which is what
    keeps ``A``/``L`` pure functions of ``criteria.yaml`` and Layer 1 alone
    (BUILD_PLAN line 950).
    """

    year: int
    doc_type: str
    language: str | None
    venue_type: str
    venue_name: str
    subject_areas: frozenset[str]


@contextmanager
def _layer1_connection(
    project: Project, connection: duckdb.DuckDBPyConnection | None
) -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield a Layer 1 connection, reusing ``connection`` when the caller supplied one.

    DuckDB refuses a second connection to the same database file from the
    same process whenever the two would disagree about configuration, so a
    caller that already holds a *writable* handle
    (``Corpus.open(project, read_only=False)``, whose ``read_only`` flag is
    part of a frozen signature) cannot have this module open its own
    read-only one underneath it -- it raises
    :class:`~prismabib.errors.StoreError` before any set is computed.
    Borrowing the caller's connection is what makes that path work at all.
    It is also what lets one caller take a single, consistent read of Layer 1
    (:func:`prismabib.prisma.flow.compute_flow_counts` reads ``runs`` and
    ``records`` through the same handle) rather than several reads that
    could disagree with each other.

    Args:
        project: The project whose Layer 1 store to open when ``connection``
            is ``None``.
        connection: An already-open Layer 1 connection to borrow, or
            ``None`` to open one for the duration of the caller's block.

    Yields:
        An open connection to ``project``'s Layer 1 store.

    Raises:
        StoreError: If no Layer 1 store exists yet for ``project`` (see
            :func:`prismabib.store.db.connect`). Only possible when
            ``connection`` is ``None`` -- a borrowed connection is already
            open.
    """
    if connection is not None:
        # Borrowed, not owned: closing it here would shut a connection the
        # caller is still using.
        yield connection
        return
    owned = connect(project, read_only=True)
    try:
        yield owned
    finally:
        owned.close()


def _fetch_record_attributes(
    connection: duckdb.DuckDBPyConnection,
) -> dict[str, _RecordAttributes]:
    """Read every record's automated/language-filter attributes from Layer 1.

    Args:
        connection: An open Layer 1 connection (see
            :func:`_layer1_connection`). Never closed here -- whoever opened
            it owns its lifetime.

    Returns:
        One :class:`_RecordAttributes` per ``record_id`` currently in
        ``records``, from one pair of queries on one connection: every set
        derived from the result therefore describes the same read of Layer 1
        rather than several that a concurrent ``build_store`` could have
        moved between.
    """
    rows = connection.execute(
        "SELECT r.record_id, r.year, r.doc_type, r.language, "
        "COALESCE(v.venue_type, ''), COALESCE(v.name, '') "
        "FROM records r LEFT JOIN venues v ON v.venue_id = r.venue_id"
    ).fetchall()
    area_rows = connection.execute("SELECT record_id, area_code FROM subject_areas").fetchall()

    areas_by_record: dict[str, set[str]] = defaultdict(set)
    for record_id, area_code in area_rows:
        areas_by_record[record_id].add(area_code)

    return {
        record_id: _RecordAttributes(
            year=year,
            doc_type=doc_type,
            language=language,
            venue_type=venue_type,
            venue_name=venue_name,
            subject_areas=frozenset(areas_by_record.get(record_id, ())),
        )
        for record_id, year, doc_type, language, venue_type, venue_name in rows
    }


# ---------------------------------------------------------------------------
# The A / L filter predicates
# ---------------------------------------------------------------------------

#: Scopus's own published Search-API document-type codes, mapped to the
#: human-readable ``subtypeDescription`` string Layer 1's loader prefers
#: whenever a captured entry carries one (``store/load.py::_doc_type_from_entry``).
#: A small, closed, reviewable table in the same spirit as
#: ``store/load.py``'s own ``_AGGREGATION_TYPE_TO_VENUE_TYPE`` -- extend it
#: by adding entries, never by switching to a heuristic. See the module
#: docstring's "Latitude taken" section.
_DOC_TYPE_CODE_TO_DESCRIPTION: Final[dict[str, str]] = {
    "ar": "article",
    "ip": "article in press",
    "re": "review",
    "cp": "conference paper",
    "cr": "conference review",
    "ch": "book chapter",
    "bk": "book",
    "no": "note",
    "ed": "editorial",
    "le": "letter",
    "sh": "short survey",
    "er": "erratum",
}


def _doc_type_matches(doc_type: str, include_codes: Sequence[str]) -> bool:
    """Whether one record's ``doc_type`` satisfies ``criteria.yaml``'s ``doc_types.include``.

    Args:
        doc_type: ``records.doc_type`` for one record -- either a Scopus
            subtype code (``"cp"``) or its description (``"Conference
            Paper"``), depending on what the captured entry carried.
        include_codes: ``criteria.yaml``'s ``doc_types.include`` list, in
            code form (BUILD_PLAN's worked example: ``[ar, cp]``).

    Returns:
        ``True`` if ``include_codes`` is empty (no restriction), or if
        ``doc_type`` case-insensitively equals one of ``include_codes``
        directly, or equals :data:`_DOC_TYPE_CODE_TO_DESCRIPTION`'s mapped
        description for one of them. ``False`` otherwise.
    """
    if not include_codes:
        return True
    folded = doc_type.strip().casefold()
    for code in include_codes:
        code_folded = code.strip().casefold()
        if folded == code_folded or folded == _DOC_TYPE_CODE_TO_DESCRIPTION.get(code_folded):
            return True
    return False


def _passes_conference_whitelist(attrs: _RecordAttributes, whitelist: Sequence[str]) -> bool:
    """Whether a conference-venue record also clears ``doc_types.conference_whitelist``.

    Args:
        attrs: The candidate record's attributes.
        whitelist: ``criteria.yaml``'s ``doc_types.conference_whitelist``.

    Returns:
        ``True`` unconditionally when ``whitelist`` is empty (no
        additional restriction), or when ``attrs.venue_type`` is not
        ``"conference"`` (the whitelist only ever narrows conference-venue
        records; it never excludes a journal article). Otherwise, ``True``
        only if ``attrs.venue_name`` case-insensitively *contains* one of
        ``whitelist``'s tokens (e.g. ``"CVPR"`` inside ``"Proceedings of
        CVPR 2024"``) -- a substring match because venue names routinely
        embed an acronym inside a longer title, and an exact match would
        essentially never fire.
    """
    if not whitelist:
        return True
    if attrs.venue_type != "conference":
        return True
    name_folded = attrs.venue_name.casefold()
    return any(token.strip().casefold() in name_folded for token in whitelist)


def _passes_subject_areas(attrs: _RecordAttributes, subject_areas: Sequence[str]) -> bool:
    """Whether a record satisfies ``criteria.yaml``'s top-level ``subject_areas`` restriction.

    Args:
        attrs: The candidate record's attributes.
        subject_areas: ``criteria.yaml``'s ``subject_areas`` list.

    Returns:
        ``True`` if ``subject_areas`` is empty (no restriction), or if
        ``attrs.subject_areas`` is empty (no Layer 1 data to evaluate the
        filter against -- see the module docstring's "no Layer 1 data"
        latitude note). Otherwise, ``True`` only if the two
        (case-insensitively compared) code sets intersect.
    """
    if not subject_areas:
        return True
    if not attrs.subject_areas:
        return True
    allowed = {code.casefold() for code in subject_areas}
    return bool({code.casefold() for code in attrs.subject_areas} & allowed)


def _passes_language(attrs: _RecordAttributes, languages: Sequence[str]) -> bool:
    """Whether a record satisfies ``criteria.yaml``'s ``languages`` restriction.

    Args:
        attrs: The candidate record's attributes.
        languages: ``criteria.yaml``'s ``languages`` list.

    Returns:
        ``True`` if ``languages`` is empty (no restriction), or if
        ``attrs.language`` is ``None`` (unknown language is not
        affirmatively excludable -- same "no data never drives an
        automated exclusion" convention as :func:`_passes_subject_areas`).
        Otherwise, ``True`` only if ``attrs.language`` case-insensitively
        equals one of ``languages``.
    """
    if not languages:
        return True
    if attrs.language is None:
        return True
    folded = attrs.language.strip().casefold()
    return any(folded == language.strip().casefold() for language in languages)


def _passes_temporal(attrs: _RecordAttributes, criteria: Criteria) -> bool:
    """Whether a record's year falls within ``criteria.yaml``'s ``temporal`` window (inclusive).

    Args:
        attrs: The candidate record's attributes.
        criteria: The criteria to check against.

    Returns:
        ``True`` if ``criteria.temporal.year_start <= attrs.year <=
        criteria.temporal.year_end``.
    """
    return criteria.temporal.year_start <= attrs.year <= criteria.temporal.year_end


# ---------------------------------------------------------------------------
# One consistent read: S_raw, A and L from a single snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Layer1View:
    """``S_raw``, ``A`` and ``L`` as derived from **one** read of Layer 1 and **one** criteria.

    The three sets and the criteria that produced them travel together
    precisely so they cannot disagree: ``language`` is a subset of
    ``automated`` is a subset of ``raw`` *as of a single instant*, not as of
    three separate reads that a concurrent ``build_store`` or a hand-edit of
    ``criteria.yaml`` could have moved between.

    That second hazard is not hypothetical. :attr:`~prismabib.project.Project.criteria`
    re-parses ``criteria.yaml`` from disk on **every** access, by design (an
    amendment is meant to take effect immediately), and the documented
    amendment workflow has the operator editing that file by hand. A caller
    that reads ``project.criteria`` twice can therefore legitimately observe
    two different criteria within one computation. This class exists so that
    no caller has to.

    Attributes:
        criteria: The criteria every set here was filtered against -- read
            once, by :func:`_capture_layer1`.
        raw: ``S_raw`` -- every ``record_id`` in Layer 1's ``records``.
        automated: ``A`` -- ``raw`` filtered by
            ``temporal``/``subject_areas``/``doc_types``.
        language: ``L`` -- ``automated`` further filtered by ``languages``.
    """

    criteria: Criteria
    raw: frozenset[str]
    automated: frozenset[str]
    language: frozenset[str]


def _compute_a_and_l(
    attributes: Mapping[str, _RecordAttributes], criteria: Criteria
) -> tuple[frozenset[str], frozenset[str]]:
    """Compute ``A`` and ``L`` together from one already-read set of record attributes.

    The one place both :func:`automated_set`/:func:`language_set` (under the
    project's *current* criteria) and :func:`replay` (under an arbitrary,
    possibly historical, criteria) go through -- so there is exactly one
    implementation of the filter logic to keep in sync with
    ``criteria.yaml``'s schema, never two that could drift apart. Pure: it
    performs no I/O of its own, which is what lets its caller decide how
    Layer 1 and ``criteria.yaml`` are read (and, in
    :func:`_capture_layer1`, guarantee they are each read exactly once).

    Args:
        attributes: Every record's Layer 1 attributes, from one read (see
            :func:`_fetch_record_attributes`).
        criteria: The criteria to filter against -- the project's current
            ``project.criteria``, or a historical one resolved by
            :func:`~prismabib.prisma.criteria.resolve_criteria`.

    Returns:
        ``(automated, language)``: ``automated`` is ``S_raw`` filtered by
        ``criteria.temporal``/``subject_areas``/``doc_types``; ``language``
        is ``automated`` further filtered by ``criteria.languages`` -- so
        ``language`` is always a subset of ``automated`` by construction,
        matching :class:`~prismabib.stage.PrismaStage`'s own description of
        ``L`` as "``A`` further filtered by language".
    """
    automated = frozenset(
        record_id
        for record_id, attrs in attributes.items()
        if _passes_temporal(attrs, criteria)
        and _passes_subject_areas(attrs, criteria.subject_areas)
        and _doc_type_matches(attrs.doc_type, criteria.doc_types.include)
        and _passes_conference_whitelist(attrs, criteria.doc_types.conference_whitelist)
    )
    language = frozenset(
        record_id
        for record_id in automated
        if _passes_language(attributes[record_id], criteria.languages)
    )
    return automated, language


def _capture_layer1(
    project: Project,
    *,
    criteria: Criteria | None = None,
    connection: duckdb.DuckDBPyConnection | None = None,
) -> _Layer1View:
    """Take one consistent snapshot of ``S_raw``, ``A`` and ``L``.

    Exactly one read of ``criteria.yaml`` (or none, when the caller already
    resolved a historical :class:`~prismabib.project.Criteria`) and exactly
    one read of Layer 1 per call. Every set in the returned
    :class:`_Layer1View` is derived from those same two reads.

    Args:
        project: The project to snapshot.
        criteria: The criteria to filter against. ``None`` (the default)
            reads ``project.criteria`` -- once.
        connection: An open Layer 1 connection to borrow, or ``None`` to
            open one for this call (see :func:`_layer1_connection`).

    Returns:
        The :class:`_Layer1View` for that instant.

    Raises:
        ConfigError: If ``criteria`` is ``None`` and ``criteria.yaml``
            fails to parse.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    resolved = project.criteria if criteria is None else criteria
    with _layer1_connection(project, connection) as open_connection:
        attributes = _fetch_record_attributes(open_connection)
    _refuse_unenforceable_subject_filter(project, resolved, attributes)
    automated, language = _compute_a_and_l(attributes, resolved)
    return _Layer1View(
        criteria=resolved,
        raw=frozenset(attributes),
        automated=automated,
        language=language,
    )


def _refuse_unenforceable_subject_filter(
    project: Project,
    criteria: Criteria,
    attributes: Mapping[str, _RecordAttributes],
) -> None:
    """Refuse a ``subject_areas`` restriction that this corpus cannot evaluate.

    :func:`_passes_subject_areas` treats "this record carries no subject-area
    data" as "passes", which is the right latitude for one sparse record
    among many. Applied to a corpus where *no* record carries the data, the
    same rule silently turns the whole filter into a no-op: every record
    passes, the automated-exclusion count omits a restriction the reviewer
    believes they applied, and the published PRISMA diagram claims a filter
    that never ran. The numbers look entirely plausible, which is precisely
    what makes it dangerous (BUILD_PLAN §1.4).

    That is not hypothetical for Scopus. The Search API's ``view=COMPLETE``
    -- the only call this project makes -- does not return subject-area
    codes, so a corpus captured from it has none for any record. The filter
    does work when the data is present (a Layer 0 entry carrying a
    ``subject-area`` array loads normally), so this refuses on the evidence
    rather than on the source: empty restriction, or no data at all for the
    whole corpus, are the two conditions checked.

    Args:
        project: The project being screened, named in the error.
        criteria: The resolved criteria whose ``subject_areas`` to check.
        attributes: Every record's Layer 1 attributes, from this snapshot.

    Raises:
        ConfigError: If ``subject_areas`` is non-empty and not one record in
            the corpus carries subject-area data.
    """
    if not criteria.subject_areas or not attributes:
        return
    if any(record.subject_areas for record in attributes.values()):
        return
    raise ConfigError(
        f"{project.root / 'criteria.yaml'} restricts subject_areas to "
        f"{list(criteria.subject_areas)!r}, but not one of the {len(attributes)} records "
        "in this corpus carries subject-area data, so the restriction would match every "
        "record and exclude nothing.\n"
        "\nThe Scopus Search API (view=COMPLETE) does not return subject-area codes, so "
        "a corpus captured from it never has them. Continuing would put a filter in your "
        "PRISMA diagram that never ran.\n"
        "\nEither:\n"
        "  - set subject_areas to [] and record the limitation in your protocol. Most "
        "reviews can express the same restriction through doc_types, the venue "
        "whitelist, or the query terms themselves; or\n"
        "  - move the restriction into the query, where Scopus applies it server-side. "
        "The [query] table in project.toml cannot express this -- it renders every entry "
        'as FIELD("term"), so a SUBJAREA(...) entry would become a literal text search '
        "for that string. Pass the whole query instead: "
        "capture_search(project, query='TITLE-ABS-KEY(...) AND SUBJAREA(MEDI)'). The "
        "exact string is recorded in the run manifest, so provenance is preserved even "
        "though project.toml no longer holds it. Note this narrows what is *identified*, "
        "so those records never appear in the automated-exclusion count."
    )


# ---------------------------------------------------------------------------
# The BUILD_PLAN table, lines 941-948
# ---------------------------------------------------------------------------


def raw_set(project: Project) -> frozenset[str]:
    """``S_raw`` -- every record captured from the query (BUILD_PLAN line 942).

    Args:
        project: The project whose Layer 1 store to read.

    Returns:
        Every ``record_id`` in Layer 1's ``records`` table -- the universe
        every other set in this module is a subset of.

    Raises:
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    connection = connect(project, read_only=True)
    try:
        rows = connection.execute("SELECT record_id FROM records").fetchall()
    finally:
        connection.close()
    return frozenset(record_id for (record_id,) in rows)


def automated_set(project: Project) -> frozenset[str]:
    """``A`` -- ``S_raw`` filtered by year, subject area, and document type (BUILD_PLAN line 943).

    Deterministic: a pure function of ``project.criteria`` and Layer 1
    alone, recomputed from scratch on every call -- never read from, or
    influenced by, the decision log (BUILD_PLAN line 950; see the module
    docstring).

    Args:
        project: The project to compute ``A`` for.

    Returns:
        The subset of :func:`raw_set` that satisfies ``project.criteria``'s
        ``temporal``, ``subject_areas``, and ``doc_types`` (including
        ``doc_types.conference_whitelist``) constraints.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return _capture_layer1(project).automated


def language_set(project: Project) -> frozenset[str]:
    """``L`` -- ``A`` further filtered by language (BUILD_PLAN line 944).

    Deterministic, for the same reason as :func:`automated_set`.

    Args:
        project: The project to compute ``L`` for.

    Returns:
        The subset of :func:`automated_set` that satisfies
        ``project.criteria.languages`` -- always a subset of
        :func:`automated_set`'s result, by construction.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return _capture_layer1(project).language


# ---------------------------------------------------------------------------
# Folding the decision log (M_abs, M_full)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RecordDecision:
    """One record's aggregated decision at one screening stage, across every reviewer.

    Attributes:
        decision: The aggregated :data:`~prismabib.prisma.events.Decision`,
            or ``None`` if no reviewer has logged one yet (pending).
        reason_code: The ``reason_code`` attributed to an aggregated
            ``"exclude"`` (see :func:`_aggregate_record_decisions`);
            ``None`` for every other ``decision`` value.
    """

    decision: Decision | None
    reason_code: str | None


#: The aggregate for a record with no folded event at a stage at all --
#: shared as one instance so every lookup site compares against the exact
#: same sentinel rather than constructing (and immediately discarding) a
#: fresh, equal-but-not-identical object per call.
_PENDING: Final[_RecordDecision] = _RecordDecision(decision=None, reason_code=None)


def _aggregate_record_decisions(
    fold: Mapping[FoldKey, DecisionEvent], stage: PrismaStage
) -> dict[str, _RecordDecision]:
    """Reduce every reviewer's folded decision at one stage to one aggregate per record.

    The decision-log fold key is ``(stage, record_id, reviewer)``
    (:data:`~prismabib.prisma.log.FoldKey`): more than one reviewer can log
    an independent decision for the same record at the same stage, and
    nothing in BUILD_PLAN's Stage 4 table adjudicates a disagreement
    between them (that is a Stage 5 screening-UI concern). Absent an
    adjudication feature, this applies the most conservative rule
    available from the data alone:

    - Any reviewer's ``"exclude"`` wins over anything else -- a record is
      not safe to advance while even one reviewer has excluded it. The
      *attributed* ``reason_code`` is the reason_code of the exclude event
      with the greatest ``(ts, event_id)`` -- the most recently logged
      exclude -- reusing the exact tie-break :func:`~prismabib.prisma.log.fold_events`
      already uses, so the same rule decides which of several reviewers'
      conflicting reasons is the one reported.
    - Otherwise, any reviewer's ``"unsure"`` wins over an unopposed
      ``"include"``: an unresolved disagreement keeps the record in the
      queue rather than silently advancing it (BUILD_PLAN line 973:
      "unsure never resolves to inclusion").
    - Only when every reviewer who has decided said ``"include"`` does the
      record's aggregate decision become ``"include"``.

    Args:
        fold: A folded decision log
            (:func:`~prismabib.prisma.log.fold_events`'s result, or
            :meth:`~prismabib.prisma.log.DecisionLog.fold`'s), covering any
            set of stages.
        stage: Which screening stage to aggregate.

    Returns:
        One :class:`_RecordDecision` per ``record_id`` that has at least
        one folded event at ``stage``. A record with no logged decision at
        ``stage`` at all is simply absent from the mapping -- callers that
        need a closed partition (``flow.py``) look it up with
        :data:`_PENDING` as the default.
    """
    events_by_record: dict[str, list[DecisionEvent]] = defaultdict(list)
    for (event_stage, record_id, _reviewer), event in fold.items():
        if event_stage is stage:
            events_by_record[record_id].append(event)

    aggregated: dict[str, _RecordDecision] = {}
    for record_id, events in events_by_record.items():
        excludes = [event for event in events if event.decision == "exclude"]
        if excludes:
            attributed = max(excludes, key=lambda event: (event.ts, event.event_id))
            aggregated[record_id] = _RecordDecision(
                decision="exclude", reason_code=attributed.reason_code
            )
            continue
        if any(event.decision == "unsure" for event in events):
            aggregated[record_id] = _RecordDecision(decision="unsure", reason_code=None)
            continue
        aggregated[record_id] = _RecordDecision(decision="include", reason_code=None)
    return aggregated


# ---------------------------------------------------------------------------
# One consistent read: the whole PRISMA-flow state (Layer 1 + criteria + log)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    """A whole PRISMA-flow state -- Layer 1, ``criteria.yaml`` and the decision log -- at one instant.

    Built by :func:`_capture_snapshot` from exactly one Layer 1 read, one
    ``criteria.yaml`` parse and one fold of ``decisions.jsonl``. Every set
    and every aggregated decision below therefore describes the same
    moment, which is what makes counts derived from several of them
    (:func:`prismabib.prisma.flow.compute_flow_counts` derives all of them)
    add up to each other rather than to two different instants of a log a
    second reviewer may be appending to concurrently
    (:class:`~prismabib.prisma.log.DecisionLog` explicitly supports two
    instances "in one process or two").

    Attributes:
        layer1: ``S_raw``/``A``/``L`` and the criteria they were filtered
            against (see :class:`_Layer1View`).
        abstract_decisions: Every record's aggregated ``title_abstract``
            decision, from the one fold. Records with nothing logged at
            that stage are absent -- look them up with :data:`_PENDING` as
            the default.
        fulltext_decisions: The same, at ``fulltext``.
        manual_abstract: ``M_abs`` -- the subset of ``layer1.language``
            whose aggregated ``title_abstract`` decision is ``"include"``.
        manual_fulltext: ``M_full`` (``= C``) -- the subset of
            ``manual_abstract`` whose aggregated ``fulltext`` decision is
            ``"include"``.
    """

    layer1: _Layer1View
    abstract_decisions: Mapping[str, _RecordDecision]
    fulltext_decisions: Mapping[str, _RecordDecision]
    manual_abstract: frozenset[str]
    manual_fulltext: frozenset[str]


def _build_snapshot(layer1: _Layer1View, fold: Mapping[FoldKey, DecisionEvent]) -> _Snapshot:
    """Derive ``M_abs`` and ``M_full`` from one Layer 1 view and one fold.

    Args:
        layer1: The Layer 1 view to screen (only ``layer1.language`` is
            eligible -- a decision logged against a record outside ``L``
            never admits it).
        fold: One fold of the decision log, covering both stages.

    Returns:
        The assembled :class:`_Snapshot`.
    """
    abstract_decisions = _aggregate_record_decisions(fold, PrismaStage.TITLE_ABSTRACT)
    manual_abstract = frozenset(
        record_id
        for record_id in layer1.language
        if abstract_decisions.get(record_id, _PENDING).decision == "include"
    )
    fulltext_decisions = _aggregate_record_decisions(fold, PrismaStage.FULLTEXT)
    manual_fulltext = frozenset(
        record_id
        for record_id in manual_abstract
        if fulltext_decisions.get(record_id, _PENDING).decision == "include"
    )
    return _Snapshot(
        layer1=layer1,
        abstract_decisions=abstract_decisions,
        fulltext_decisions=fulltext_decisions,
        manual_abstract=manual_abstract,
        manual_fulltext=manual_fulltext,
    )


def _capture_snapshot(
    project: Project, *, connection: duckdb.DuckDBPyConnection | None = None
) -> _Snapshot:
    """Take one consistent snapshot of every PRISMA-flow set for ``project``.

    One ``criteria.yaml`` parse, one Layer 1 read, one decision-log fold --
    and every set derived from those. A caller that instead called
    :func:`language_set`, :func:`manual_abstract_set` and :func:`corpus` in
    turn would fold the log three times and read the criteria three times,
    and any change landing between two of those reads would be absorbed
    silently into whichever counts were computed after it.

    Args:
        project: The project to snapshot.
        connection: An open Layer 1 connection to borrow, or ``None`` to
            open one for this call (see :func:`_layer1_connection`).

    Returns:
        The :class:`_Snapshot` for that instant.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    layer1 = _capture_layer1(project, connection=connection)
    # An empty `L` makes `M_abs` and `M_full` empty whatever the log says, so
    # the log is not read at all -- the short circuit `manual_abstract_set`
    # has always had. It is not only a saved read: `DecisionLog` opens
    # `decisions.jsonl` with `O_CREAT`, so folding it would also create the
    # file (and raise on a corrupt one) for a project whose screening set is
    # empty and whose answer cannot depend on it.
    fold = DecisionLog(project).fold() if layer1.language else {}
    return _build_snapshot(layer1, fold)


def manual_abstract_set(project: Project) -> frozenset[str]:
    """``M_abs`` -- ``L`` folded through the decision log at ``title_abstract`` (BUILD_PLAN line 945).

    Args:
        project: The project to compute ``M_abs`` for.

    Returns:
        The subset of :func:`language_set` whose aggregated
        ``title_abstract`` decision (see :func:`_aggregate_record_decisions`)
        is ``"include"``. A record with no logged decision, or an
        aggregated ``"unsure"``/``"exclude"``, is not a member -- ``unsure``
        never resolves to inclusion (BUILD_PLAN line 973).

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return _capture_snapshot(project).manual_abstract


def manual_fulltext_set(project: Project) -> frozenset[str]:
    """``M_full`` -- ``M_abs`` folded through the decision log at ``fulltext`` (BUILD_PLAN line 946).

    Args:
        project: The project to compute ``M_full`` for.

    Returns:
        The subset of :func:`manual_abstract_set` whose aggregated
        ``fulltext`` decision is ``"include"``. Mirrors
        :func:`manual_abstract_set` one stage further along.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return _capture_snapshot(project).manual_fulltext


def corpus(project: Project) -> frozenset[str]:
    """``C`` -- the final corpus, ``= M_full`` (BUILD_PLAN line 947).

    Args:
        project: The project to compute ``C`` for.

    Returns:
        :func:`manual_fulltext_set`'s result, unchanged -- a distinct name
        because BUILD_PLAN's table names it separately, not because the
        computation differs. Because ``unsure`` never folds into
        ``"include"`` (BUILD_PLAN line 973) and :func:`manual_fulltext_set`
        only ever admits an aggregated ``"include"``, no record whose
        current decision is ``"unsure"`` at any stage can appear here.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return manual_fulltext_set(project)


# ---------------------------------------------------------------------------
# Criteria amendment support
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayResult:
    """What amending ``criteria.yaml`` to a different version would change.

    Nothing here mutates the decision log; every field is a read-only
    report computed by re-running the deterministic ``A``/``L`` filters
    under ``criteria`` and comparing the result against the *existing*
    ``title_abstract`` decision log (BUILD_PLAN: "reporting which logged
    human decisions remain valid and which records newly require
    screening").

    Attributes:
        criteria_version: The resolved criteria's ``version`` (equal to
            the ``criteria_version`` :func:`replay` was called with).
        automated: ``A`` recomputed under this criteria.
        language: ``L`` recomputed under this criteria.
        decisions_still_valid: Records with an existing
            ``title_abstract`` decision that remain inside ``language`` --
            those decisions are still applicable and need no re-screening.
        newly_requires_screening: Records inside ``language`` with no
            existing ``title_abstract`` decision -- either because they
            were previously outside ``A``/``L`` (an amendment widened
            scope) or genuinely never screened.
        no_longer_in_scope: Records with an existing ``title_abstract``
            decision that now fall outside ``language`` -- their decisions
            remain in the log untouched, but no longer determine
            membership in this criteria's corpus.
    """

    criteria_version: str
    automated: frozenset[str]
    language: frozenset[str]
    decisions_still_valid: frozenset[str]
    newly_requires_screening: frozenset[str]
    no_longer_in_scope: frozenset[str]


def replay(project: Project, *, criteria_version: str) -> ReplayResult:
    """Recompute set membership under a different ``criteria.yaml`` version.

    Args:
        project: The project to replay.
        criteria_version: The ``criteria.yaml`` ``version`` to replay
            against -- the project's current version, or a prior one
            resolved from git history (see
            :func:`~prismabib.prisma.criteria.resolve_criteria`).

    Returns:
        A :class:`ReplayResult`. No event is read beyond a single
        :meth:`~prismabib.prisma.log.DecisionLog.fold`, and none is
        written, deleted, or rewritten -- amending criteria never touches
        the decision log itself, only how it is *interpreted*.

    Raises:
        ConfigError: If ``criteria_version`` cannot be resolved (see
            :func:`~prismabib.prisma.criteria.resolve_criteria`).
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    criteria = resolve_criteria(project, criteria_version)
    layer1 = _capture_layer1(project, criteria=criteria)
    automated, language = layer1.automated, layer1.language
    fold = DecisionLog(project).fold()
    decided = {
        record_id for (stage, record_id, _reviewer) in fold if stage is PrismaStage.TITLE_ABSTRACT
    }
    return ReplayResult(
        criteria_version=criteria.version,
        automated=automated,
        language=language,
        decisions_still_valid=frozenset(decided & language),
        newly_requires_screening=frozenset(language - decided),
        no_longer_in_scope=frozenset(decided - language),
    )


__all__ = [
    "ReplayResult",
    "automated_set",
    "corpus",
    "language_set",
    "manual_abstract_set",
    "manual_fulltext_set",
    "raw_set",
    "replay",
]
