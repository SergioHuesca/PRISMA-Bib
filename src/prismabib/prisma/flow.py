"""PRISMA 2020 flow-diagram counts (BUILD_PLAN §Stage 4, lines 976-991).

:class:`FlowCounts` and :func:`compute_flow_counts` are the two things this
module owns: a frozen dataclass whose fields are exactly BUILD_PLAN's own
worked shape (lines 978-991) plus the two ``unsure`` fields the operator
approved (see the class docstring), and the one function that derives a
populated instance from a project's Layer 1 store and decision log --
*entirely derived*, per BUILD_PLAN's own description of this module,
never a number typed in by hand or cached from a previous run.

**Why ``unsure_title_abstract``/``unsure_fulltext`` exist.** BUILD_PLAN
line 973 says ``unsure`` "never resolves to inclusion; it keeps the record
in the queue and is reported separately". Without a field to report it
into, an unsure record would have nowhere to go in a closed partition of
``after_language`` into "excluded" and "retrieved_fulltext": the frozen
dataclass literally cannot close its own arithmetic on a record that is
neither. Adding the two ``unsure`` fields is what makes
:meth:`FlowCounts.assert_consistent` a *closed* accounting identity rather
than one with a silent gap -- exactly the class of error BUILD_PLAN says
this module exists to guard against (§1.4).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

import duckdb

from prismabib.errors import ValidationError
from prismabib.prisma.engine import _PENDING, _capture_snapshot, _RecordDecision
from prismabib.project import Project
from prismabib.store.db import connect

#: Bucket key for an aggregated ``"exclude"`` at ``fulltext`` whose
#: attributed event carries no ``reason_code``. ``DecisionLog.append``
#: enforces "``reason_code`` is mandatory for exclude" at write time, but
#: :meth:`~prismabib.prisma.log.DecisionLog.load`/``fold`` (what this
#: module reads) does not re-check that business rule -- a decisions.jsonl
#: that conforms to the event *schema* but was not written exclusively
#: through ``DecisionLog.append`` could still reach this module missing
#: one. Bucketing it under a named sentinel, rather than letting a ``None``
#: dict key silently merge every such record together under a key that
#: does not even print, keeps the count visible and attributable.
_UNKNOWN_REASON_CODE: Final[str] = "UNKNOWN"


@dataclass(frozen=True)
class FlowCounts:
    """The PRISMA 2020 flow-diagram numbers for one project, as of one moment.

    Every field is a plain count; :meth:`assert_consistent` is what
    verifies they close into a single coherent diagram. Fields match
    BUILD_PLAN lines 978-991 exactly, plus ``unsure_title_abstract`` and
    ``unsure_fulltext`` (operator-approved additions -- see the module
    docstring).

    Attributes:
        identified: Records identified by the search, read from
            :class:`~prismabib.capture.manifest.RunManifest.total_results`
            **only** -- never a row count (see :func:`_identified_count`
            for exactly which run's ``total_results`` this is, and why).
        excluded_automated: ``|S_raw| - |S_raw ∩ A|`` -- records removed by
            the automated year/subject/doc-type filter.
        after_automated: ``|A|``.
        excluded_language: ``|A| - |L|`` -- records removed by the
            automated language filter.
        after_language: ``|L|`` -- the unique records that reach
            title/abstract screening.
        excluded_title_abstract: Records in ``L`` whose aggregated
            ``title_abstract`` decision is ``"exclude"``.
        unsure_title_abstract: Records in ``L`` that are neither excluded
            nor advanced -- an aggregated ``"unsure"``, or no decision
            logged yet at all. Never resolves to inclusion (BUILD_PLAN
            line 973).
        retrieved_fulltext: ``|M_abs|`` -- records advanced to full-text
            screening.
        excluded_fulltext: Records in ``M_abs`` whose aggregated
            ``fulltext`` decision is ``"exclude"``, grouped by
            ``reason_code``.
        unsure_fulltext: Records in ``M_abs`` that are neither excluded
            nor included -- an aggregated ``"unsure"``, or no ``fulltext``
            decision logged yet.
        included: ``|C|`` (``= |M_full|``) -- the final corpus.
    """

    identified: int
    duplicates_across_searches: int
    removed_other_reasons: int
    excluded_automated: int
    after_automated: int
    excluded_language: int
    after_language: int
    excluded_title_abstract: int
    unsure_title_abstract: int
    retrieved_fulltext: int
    excluded_fulltext: dict[str, int]
    unsure_fulltext: int
    included: int

    def assert_consistent(self) -> None:
        """Verify every PRISMA-flow count is a cardinality and every identity closes.

        Delegates to :func:`_assert_flow_counts_consistent`, which holds every
        equation. That indirection exists for one reason: mutmut does not
        mutate the body of a decorated class, and ``FlowCounts`` is a
        ``@dataclass(frozen=True)``. Written inline, the ~51 mutants of the
        four PRISMA identities were never generated -- the check that decides
        whether a published diagram adds up had 100% line and branch coverage
        and no mutation testing at all, which is precisely the combination
        BUILD_PLAN §3.7.6 warns proves nothing.

        Raises:
            ValidationError: Naming the first count or identity that fails.
        """
        _assert_flow_counts_consistent(self)


def _assert_flow_counts_consistent(flow: FlowCounts) -> None:
    """Verify every PRISMA-flow count is a cardinality and every identity closes.

    First, every count must be non-negative -- every integer field and
    every value of ``excluded_fulltext``. This precondition is checked
    **before** the equations, and it is not redundant with them: each
    equation is an equality between two sums, and a *pair* of errors
    that cancel satisfies it. Concretely, ``unsure_title_abstract`` and
    ``unsure_fulltext`` are computed by
    :func:`compute_flow_counts` as the remainders of their partitions
    (ADR 0007), so an over-count anywhere else in the partition drives
    the remainder below zero and equation 3 or 4 still closes exactly.
    A negative count is not a diagram that adds up; it is a diagram
    whose error has been absorbed. That is also the state a
    ``FlowCounts`` assembled by hand, mutated, or deserialised can
    arrive in, which ADR 0007 names as the population these checks stay
    load-bearing for.

    Then four equations, each checked independently so a failure names
    exactly which step of the diagram does not add up:

    1. ``identified - duplicates_across_searches - removed_other_reasons
       - excluded_automated == after_automated``
    2. ``after_automated - excluded_language == after_language``
    3. ``after_language == excluded_title_abstract +
       unsure_title_abstract + retrieved_fulltext``
    4. ``retrieved_fulltext == sum(excluded_fulltext.values()) +
       unsure_fulltext + included``

    Raises:
        ValidationError: On the *first* negative count (in field
            order, ``excluded_fulltext``'s entries last and in sorted
            reason-code order), naming the field and its value; or,
            failing that, on the first equation (in the order above)
            that does not hold, naming that equation verbatim, both
            sides' actual values, and the signed difference between
            them ("off by ...") -- "inconsistent" alone tells a reader
            nothing actionable; this is meant to point them straight
            at which stage's bookkeeping is wrong.
    """
    # Every integer field, in declaration order, then every
    # `excluded_fulltext` entry. Written out rather than reflected over
    # `dataclasses.fields` so the check reads as a checklist a reviewer
    # can compare against the class above; a field added there without a
    # line here is a gap, and the class is frozen by BUILD_PLAN plus one
    # ADR, so it does not move often.
    counts: tuple[tuple[str, int], ...] = (
        ("identified", flow.identified),
        ("duplicates_across_searches", flow.duplicates_across_searches),
        ("removed_other_reasons", flow.removed_other_reasons),
        ("excluded_automated", flow.excluded_automated),
        ("after_automated", flow.after_automated),
        ("excluded_language", flow.excluded_language),
        ("after_language", flow.after_language),
        ("excluded_title_abstract", flow.excluded_title_abstract),
        ("unsure_title_abstract", flow.unsure_title_abstract),
        ("retrieved_fulltext", flow.retrieved_fulltext),
        ("unsure_fulltext", flow.unsure_fulltext),
        ("included", flow.included),
        *(
            (f"excluded_fulltext[{reason!r}]", count)
            for reason, count in sorted(flow.excluded_fulltext.items())
        ),
    )
    for field_name, count in counts:
        if count < 0:
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise ValidationError(
                f"FlowCounts is inconsistent: {field_name} is negative: {count} -- "
                "every flow count is a number of records, so it cannot be below "
                "zero. The equations below cannot catch this on their own: an "
                "equality between two sums closes over a negative term exactly as "
                "happily as a positive one"
            )
            # pragma: no mutate end

    equations = (
        (
            (
                "identified - duplicates_across_searches - removed_other_reasons "
                "- excluded_automated == after_automated"
            ),
            flow.identified
            - flow.duplicates_across_searches
            - flow.removed_other_reasons
            - flow.excluded_automated,
            flow.after_automated,
        ),
        (
            "after_automated - excluded_language == after_language",
            flow.after_automated - flow.excluded_language,
            flow.after_language,
        ),
        (
            (
                "after_language == excluded_title_abstract + unsure_title_abstract "
                "+ retrieved_fulltext"
            ),
            flow.after_language,
            flow.excluded_title_abstract + flow.unsure_title_abstract + flow.retrieved_fulltext,
        ),
        (
            "retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included",
            flow.retrieved_fulltext,
            sum(flow.excluded_fulltext.values()) + flow.unsure_fulltext + flow.included,
        ),
    )
    for equation, left, right in equations:
        if left != right:
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise ValidationError(
                f"FlowCounts is inconsistent: {equation!r} does not hold: "
                f"{left} != {right} (off by {left - right})"
            )
            # pragma: no mutate end


def _identified_count(connection: duckdb.DuckDBPyConnection) -> int:
    """The PRISMA "records identified" count -- ``RunManifest.total_results``, never a row count.

    Args:
        connection: An open Layer 1 connection whose ``runs`` table to read
            (each row's ``total_results`` column is copied verbatim from
            that run's ``RunManifest.total_results`` at store-build time --
            reading it back here is reading the manifest, not counting rows
            written or records parsed). Never closed here: it is the same
            connection every other Layer 1 read in this module goes through,
            and :func:`compute_flow_counts` owns its lifetime.

    Returns:
        The sum, over each **distinct query string** in ``runs``, of that
        query's *earliest* run's ``total_results`` -- or ``0`` if no run has
        been loaded yet (ADR 0013).

        Neither a plain ``SUM`` nor the earliest single run. Both halves of
        that rule carry weight:

        *Summing across distinct queries* is what makes a review with more
        than one search string report the number it actually identified. This
        function previously answered with the earliest run alone, which was a
        defensible reading while a project meant one search; a real corpus
        with two different searches then reported 651 against a store of
        1,864 records, drove ``excluded_automated`` negative, and made
        equation 1 fail permanently.

        *One total per query, the earliest* is what preserves the property
        the old rule existed to protect. Re-running the same query to refresh
        citation counts adds no record to the store, and summing its
        ``total_results`` would inflate "identified" for a project that has
        only ever refreshed itself. Taking the earliest keeps the number as
        of the original search date rather than letting it drift with the
        index between refreshes.
    """
    row = connection.execute(
        # GROUP BY the query, then MIN(run_id) picks that search's first run;
        # `run_id` sorts chronologically by construction (`%Y%m%dT%H%M%SZ-...`),
        # so MIN is "earliest" without parsing a timestamp.
        """
        SELECT COALESCE(SUM(total_results), 0)
        FROM runs
        WHERE (query, run_id) IN (SELECT query, MIN(run_id) FROM runs GROUP BY query)
        """
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _unloadable_count(connection: duckdb.DuckDBPyConnection) -> int:
    """How many Layer 0 entries `build_store` could not turn into a record.

    PRISMA 2020's "records removed before screening" box has a line for
    reasons other than duplication; this is that line. The entries are
    recorded in Layer 1's ``malformed_entries`` table by the loader (ADR
    0012), so this reads what was persisted rather than recomputing it --
    the loader is the only thing that can know, and it is immutable once
    written.

    Args:
        connection: An open Layer 1 connection; not closed here.

    Returns:
        The row count, or ``0`` for a store built before the table existed.
    """
    row = connection.execute(
        # Records genuinely lost, not rows. `malformed_entries` is keyed per
        # Layer 0 *line*, so the same paper failing in two runs of one search
        # is two rows -- subtracting both would double-count it. And an entry
        # another run loaded successfully cost no record at all: it must not be
        # subtracted, which is why the DISTINCT set is filtered against
        # `records` (ADR 0012 predicts exactly this: "a row here does not imply
        # a lost record").
        #
        # A `missing_eid` row carries no record_id -- nothing identifies the
        # paper it was -- so each is counted individually as its own loss.
        """
        SELECT
          (
            SELECT count(*) FROM (
              SELECT DISTINCT m.record_id
              FROM malformed_entries m
              WHERE m.record_id IS NOT NULL
                AND m.record_id NOT IN (SELECT record_id FROM records)
            )
          )
          + (SELECT count(*) FROM malformed_entries WHERE record_id IS NULL)
        """
    ).fetchone()
    return int(row[0]) if row is not None else 0


def _cross_run_duplicate_count(connection: duckdb.DuckDBPyConnection) -> int:
    """Papers a later search returned that an earlier one had already captured.

    PRISMA 2020's "duplicates removed before screening". Read from what the
    loader recorded (``run_duplicates``), **not** derived as
    ``identified - |S_raw| - removed_other_reasons``.

    That distinction is the point. The remainder always closes equation 1 by
    construction, so it would turn the diagram's first identity into one that
    cannot fail -- silently absorbing a run manifest that disagrees with the
    corpus it produced, which is the single defect BUILD_PLAN line 993 says
    this guard exists to catch. Measured independently, the equation can
    disagree, and a disagreement means something real.

    It cannot be recomputed later either: ``records.run_id`` keeps only the
    first run that loaded a record, so after the load Layer 1 no longer knows
    how many runs a record appeared in.

    Args:
        connection: An open Layer 1 connection; not closed here.

    Returns:
        The total across runs, or ``0`` for a store built before the table
        existed.
    """
    row = connection.execute("SELECT COALESCE(SUM(duplicates), 0) FROM run_duplicates").fetchone()
    return int(row[0]) if row is not None else 0


def _excluded_by_reason(
    record_ids: Iterable[str], decisions: Mapping[str, _RecordDecision]
) -> dict[str, int]:
    """Count aggregated ``"exclude"`` decisions by ``reason_code``, in a fixed key order.

    Args:
        record_ids: The records eligible at this stage -- the domain the
            partition is taken over. Its iteration order does not reach the
            result (see below), so a ``frozenset`` is a fine thing to pass.
        decisions: Every record's aggregated decision at this stage, from
            one fold (see
            :func:`~prismabib.prisma.engine._aggregate_record_decisions`).
            A record absent from it has nothing logged and is not excluded.

    Returns:
        ``{reason_code: count}`` **sorted by reason code**. The sort is
        load-bearing, not cosmetic: ``record_ids`` is a set, whose iteration
        order varies with ``PYTHONHASHSEED`` from one process to the next,
        and this dict is serialised into published outputs where
        ``json.dumps`` preserves insertion order. Without the sort, the same
        project would produce byte-different JSON on two machines while
        every count in it was identical (§3.7.3 rule 3: never rely on set
        ordering).
    """
    counts: Counter[str] = Counter()
    for record_id in record_ids:
        decision = decisions.get(record_id, _PENDING)
        if decision.decision == "exclude":
            counts[decision.reason_code or _UNKNOWN_REASON_CODE] += 1
    return dict(sorted(counts.items()))


def compute_flow_counts(project: Project) -> FlowCounts:
    """Derive every PRISMA 2020 flow-diagram number for ``project``.

    Every count is recomputed from Layer 1 and the decision log on every
    call -- nothing here is cached or persisted, so three fresh calls
    against an unchanged project and log reproduce identical integers
    (BUILD_PLAN's reproducibility acceptance criterion).

    **One snapshot, not several reads.** ``criteria.yaml`` is parsed once,
    Layer 1 is read once (through a single connection, shared with
    :func:`_identified_count`), and ``decisions.jsonl`` is folded once; every
    number below is derived from that one
    :func:`~prismabib.prisma.engine._capture_snapshot`. This matters because
    both sources are live: :attr:`~prismabib.project.Project.criteria`
    re-reads the file on every access (the amendment how-to has the operator
    editing it by hand), and
    :class:`~prismabib.prisma.log.DecisionLog` explicitly supports two
    instances "in one process or two", so a second reviewer can be appending
    decisions while these numbers are being generated. Computing
    ``excluded_title_abstract`` from one fold and ``retrieved_fulltext`` from
    another would let a change landing between them be absorbed into the
    ``unsure_*`` remainders -- as a *negative* count that
    :meth:`FlowCounts.assert_consistent`'s equations, being remainders of
    each other, would still close over. (That specific escape is now also
    caught: ``assert_consistent`` rejects a negative count before it checks
    any equation.)

    Args:
        project: The project to compute flow counts for.

    Returns:
        A :class:`FlowCounts`. Equations 2-4 of
        :meth:`FlowCounts.assert_consistent` hold by construction here (over
        one snapshot, so "by construction" is now a property of a single
        consistent read rather than of three reads that happened to agree) --
        ``unsure_title_abstract``/``unsure_fulltext`` are each computed as
        the remainder of their partition, not measured independently --
        but equation 1 is a genuine cross-check this function does
        **not** force to hold: ``identified`` comes from the earliest
        run's server-reported total (see :func:`_identified_count`) while
        ``after_automated`` is derived from Layer 1's own ``records`` rows
        (the snapshot's ``A``), and a project with an
        incomplete capture or an unexpected duplicate can legitimately
        make those disagree. Note that ``records`` is **not** deduplicated:
        duplicates are *reported, not applied* (see
        :mod:`prismabib.store.load`), and only same-``record_id``
        collisions collapse, via the primary key. PRISMA's "duplicate
        records removed" count is therefore *not* derived from ``records``:
        it is measured during the load, when a run re-finds a paper an
        earlier search already contributed, and read back from
        ``run_duplicates`` (ADR 0013). This function does **not** call
        :meth:`FlowCounts.assert_consistent` itself, precisely so that
        disagreement is returned for a caller to inspect and act on,
        rather than raised from inside a function whose job is only to
        compute, not to judge.

    Raises:
        ConfigError: If ``project.criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    connection = connect(project, read_only=True)
    try:
        identified = _identified_count(connection)
        removed_other_reasons = _unloadable_count(connection)
        duplicates_across_searches = _cross_run_duplicate_count(connection)
        snapshot = _capture_snapshot(project, connection=connection)
    finally:
        connection.close()

    raw = snapshot.layer1.raw
    automated = snapshot.layer1.automated
    language = snapshot.layer1.language

    excluded_automated = len(raw) - len(automated)
    after_automated = len(automated)
    excluded_language = len(automated) - len(language)
    after_language = len(language)

    excluded_title_abstract = sum(
        1
        for record_id in language
        if snapshot.abstract_decisions.get(record_id, _PENDING).decision == "exclude"
    )
    retrieved_fulltext = len(snapshot.manual_abstract)
    unsure_title_abstract = after_language - excluded_title_abstract - retrieved_fulltext

    excluded_fulltext = _excluded_by_reason(snapshot.manual_abstract, snapshot.fulltext_decisions)
    included = len(snapshot.manual_fulltext)
    unsure_fulltext = retrieved_fulltext - sum(excluded_fulltext.values()) - included

    return FlowCounts(
        identified=identified,
        duplicates_across_searches=duplicates_across_searches,
        removed_other_reasons=removed_other_reasons,
        excluded_automated=excluded_automated,
        after_automated=after_automated,
        excluded_language=excluded_language,
        after_language=after_language,
        excluded_title_abstract=excluded_title_abstract,
        unsure_title_abstract=unsure_title_abstract,
        retrieved_fulltext=retrieved_fulltext,
        excluded_fulltext=excluded_fulltext,
        unsure_fulltext=unsure_fulltext,
        included=included,
    )


__all__ = ["FlowCounts", "compute_flow_counts"]
