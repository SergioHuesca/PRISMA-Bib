"""``numbers.json`` -- every scalar a manuscript is allowed to cite.

BUILD_PLAN §Stage 10 calls this *"the anti-drift mechanism"*, and the drift it
is against is the §1.4 failure mode: a manuscript says "1,110 records were
screened" because someone typed it once, the corpus is recaptured, and the
sentence is now quietly false. Here a manuscript says ``{{flow.after_language}}``
and :mod:`~prismabib.report.fill` substitutes it at build time.

Two properties make that work, and both are enforced rather than intended.

**Every value is a scalar.** ``int``, ``float``, ``str`` or ``bool`` -- never a
list or a mapping. A nested structure has no sensible rendering inside a
sentence, and allowing one would mean the substitution's output depended on a
repr that nobody chose deliberately.

**Every key is derived, never typed.** The flow keys are generated from
:class:`~prismabib.prisma.flow.FlowCounts`' own fields, so a field added to the
diagram cannot be forgotten here; the golden key-set test then makes its
appearance a reviewable diff rather than a surprise.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from prismabib.bibliometrics.citations import citation_statistics
from prismabib.bibliometrics.venues import top_venues
from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import Corpus

if TYPE_CHECKING:
    import duckdb

    from prismabib.project import Project

#: How many venues and papers the "top N" keys cover. Fixed rather than a
#: parameter: the key set is snapshotted by a golden test, so a caller that
#: could vary N could silently change which keys exist.
TOP_N = 5

#: The JSON scalar types a manuscript can be handed. Checked at build time by
#: :func:`numbers_map` rather than trusted, because the failure it prevents --
#: a Python ``repr`` landing in a sentence -- is silent.
_SCALAR_TYPES = (bool, int, float, str)


def _flow_numbers(counts: FlowCounts) -> dict[str, Any]:
    """Flatten ``counts`` into ``flow.*`` keys.

    Args:
        counts: The project's PRISMA flow counts.

    Returns:
        One key per integer field, plus ``flow.excluded_fulltext.<CODE>``
        per recorded reason, a ``flow.excluded_fulltext.total``, and
        ``flow.excluded_automated.<reason>`` per automated-exclusion reason.
        Reasons are emitted in sorted order so the key sequence does not
        depend on a SQL result set's ordering.
    """
    numbers: dict[str, Any] = {}
    for field in dataclasses.fields(counts):
        value = getattr(counts, field.name)
        if isinstance(value, int):
            numbers[f"flow.{field.name}"] = value
    reasons = dict(counts.excluded_fulltext)
    numbers["flow.excluded_fulltext.total"] = sum(reasons.values())
    for code in sorted(reasons):
        numbers[f"flow.excluded_fulltext.{code}"] = reasons[code]
    # The automated reasons are attributed by precedence (ADR 0016), so unlike
    # the full-text codes their key set is fixed and every key is always
    # present -- a reason that excluded nothing reports 0 rather than vanishing.
    # A key that appeared only when non-zero would make "we did not filter on
    # subject area" and "we filtered and it excluded nothing" indistinguishable
    # in the manuscript, and those are different methodological claims.
    # Sorted, not in precedence order, because every key in this file is sorted
    # and a reader looks a key up by name rather than reading the file in
    # sequence. The precedence order still carries meaning -- see ADR 0016 --
    # but it is the *figure* and `prismabib flow` that present it, where the
    # lines are read top to bottom.
    automated = dict(counts.excluded_automated_by_reason)
    for reason in sorted(automated):
        numbers[f"flow.excluded_automated.{reason}"] = automated[reason]
    return numbers


def _corpus_numbers(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Corpus-level descriptive scalars.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        ``corpus.*`` keys. Year bounds are ``0`` on an empty corpus rather
        than ``None``: a manuscript substituting ``None`` into a sentence is
        a worse outcome than one substituting a zero a reader can question.
    """
    row = connection.execute(
        "SELECT count(*), COALESCE(min(year), 0), COALESCE(max(year), 0) FROM records"
    ).fetchone()
    size, year_min, year_max = (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
    return {
        "corpus.size": size,
        "corpus.year_min": year_min,
        "corpus.year_max": year_max,
        "corpus.year_span": year_max - year_min if size else 0,
    }


#: Passed as ``top_n`` to :func:`~prismabib.bibliometrics.venues.top_venues`
#: when this module needs *every* normalised venue group (to count
#: ``venues.total``), not just the top :data:`TOP_N`. Large enough that no
#: real corpus has more distinct venues than this to truncate -- if one
#: ever did, ``venues.total`` would undercount, which is why this is a named
#: constant rather than a magic number at the call site.
_ALL_VENUES = 1_000_000


def _citation_numbers(corpus: Corpus) -> dict[str, Any]:
    """Citation statistics over the latest snapshot per record.

    ADR 0022 Decision 5: delegates to
    :func:`~prismabib.bibliometrics.citations.citation_statistics` (over
    :attr:`~prismabib.stage.PrismaStage.RAW`, matching this function's
    historical unfiltered scope -- see :func:`_venue_numbers`) rather than
    re-querying ``citation_snapshots``, so ``numbers.json`` and a Stage 7
    citation table can never disagree about the same corpus's statistics.

    Args:
        corpus: A :class:`~prismabib.store.load.Corpus` over ``project``'s
            store.

    Returns:
        ``citations.*`` keys, a subset of
        :func:`~prismabib.bibliometrics.citations.citation_statistics`'s
        row -- the fields this file has always published, kept stable so
        no manuscript key silently vanishes.
    """
    result = citation_statistics(corpus, stage=PrismaStage.RAW)
    row = result.data.row(0, named=True)
    return {
        "citations.records_with_a_snapshot": int(row["records_with_a_snapshot"]),
        "citations.total": int(row["total"]),
        "citations.median": float(row["median"]),
        "citations.max": int(row["max"]),
    }


def _venue_numbers(corpus: Corpus) -> dict[str, Any]:
    """The top :data:`TOP_N` venues by record count, after name normalisation.

    ADR 0022 Decision 5: delegates to
    :func:`~prismabib.bibliometrics.venues.top_venues` (over
    :attr:`~prismabib.stage.PrismaStage.RAW`, this function's historical
    scope: neither this nor the query it replaces has ever filtered by
    PRISMA stage -- both counted every record Layer 1 holds) rather than
    grouping by exact ``venues.name`` itself.

    What that delegation buys is the **anti-drift guarantee**: one
    definition of "a venue" shared by this function, ``top_venues_table``
    and Stage 7, so a table and the prose beside it cannot disagree. It does
    *not*, on the reference corpus, change the number: none of the
    normalisation rules fires on any of its 769 venue names, and
    ``venues.total`` is 769 before and after (ADR 0022 Decision 5 records
    the measurement). The variants Scopus emits there are conference
    editions -- "... WACV 2020" beside "... WACV 2024" -- which the rules
    cannot reach by design, since the difference is a year rather than
    formatting. ``venues.total`` therefore counts *distinct venue name
    strings*, with a recurring conference appearing once per edition; see
    ``docs/methodology/limitations.md``.

    Args:
        corpus: A :class:`~prismabib.store.load.Corpus` over ``project``'s
            store.

    Returns:
        ``venues.total`` plus ``venues.top<i>.name`` / ``.count`` for
        ``i`` in ``1..TOP_N``. Slots beyond the number of venues present are
        still emitted, with an empty name and a zero, so the key set does not
        depend on corpus size -- a manuscript citing ``venues.top3.name``
        must not start failing to fill because a re-capture dropped a venue.
    """
    every_venue = top_venues(corpus, stage=PrismaStage.RAW, top_n=_ALL_VENUES).data
    numbers: dict[str, Any] = {"venues.total": every_venue.height}
    for index in range(TOP_N):
        name, count = (
            (every_venue.item(index, "venue"), int(every_venue.item(index, "count")))
            if index < every_venue.height
            else ("", 0)
        )
        numbers[f"venues.top{index + 1}.name"] = name
        numbers[f"venues.top{index + 1}.count"] = count
    return numbers


def numbers_map(project: Project, *, counts: FlowCounts | None = None) -> dict[str, Any]:
    """Every scalar ``project``'s manuscript may cite, as a flat mapping.

    Args:
        project: The project to describe.
        counts: Pre-computed flow counts, to avoid recomputing them when the
            caller already has them. Recomputed from Layer 1 and the decision
            log when omitted -- never read from a cache, so this cannot report
            a number the current store does not support.

    Returns:
        A flat ``key -> scalar`` mapping, sorted by key. Sorted because the
        file is a golden artefact and a diff that reordered keys would read
        as a change to the numbers.

    Raises:
        ValidationError: If any value is not a JSON scalar. Raised here
            rather than at write time so the offending key is named while the
            code that produced it is still on the stack.
        StoreError: If no Layer 1 store exists yet.
    """
    from prismabib.errors import ValidationError

    resolved = counts if counts is not None else compute_flow_counts(project)
    numbers: dict[str, Any] = dict(_flow_numbers(resolved))
    numbers["criteria.version"] = project.criteria.version

    connection = connect(project, read_only=True)
    try:
        numbers.update(_corpus_numbers(connection))
        # A bare `Corpus(connection)` -- no `.open(project, ...)` -- is
        # deliberate and safe here: both delegated calls below pass
        # `stage=PrismaStage.RAW` (see `_citation_numbers`/`_venue_numbers`),
        # which `Corpus.records`/`Corpus.venues` answer straight from Layer 1
        # without ever reaching `_prisma_stage_record_ids`, the one code path
        # that needs a project-bound `Corpus` to resolve `criteria.yaml` and
        # the decision log. Reusing this connection (rather than opening a
        # second one via `Corpus.open`) also keeps every number in this
        # function reading one instant of the store.
        corpus = Corpus(connection)
        numbers.update(_citation_numbers(corpus))
        numbers.update(_venue_numbers(corpus))
    finally:
        connection.close()

    for key, value in numbers.items():
        if not isinstance(value, _SCALAR_TYPES):
            raise ValidationError(
                f"numbers.json value for {key!r} is {type(value).__name__}, not a JSON "
                "scalar -- every value must render inside a sentence, and a list or a "
                "mapping has no sensible rendering there"
            )
    return {key: numbers[key] for key in sorted(numbers)}


__all__ = ["TOP_N", "numbers_map"]
