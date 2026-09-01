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
import statistics
from typing import TYPE_CHECKING, Any

from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.store.db import connect

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
        per recorded reason and a ``flow.excluded_fulltext.total``. Reasons
        are emitted in sorted order so the key sequence does not depend on a
        SQL result set's ordering.
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


def _citation_numbers(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Citation statistics over the latest snapshot per record.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        ``citations.*`` keys. The median is computed in Python from the
        retrieved column rather than in SQL, so its tie-breaking is the one
        :mod:`statistics` documents rather than one that varies with the
        engine's percentile implementation.
    """
    rows = connection.execute(
        """
        SELECT cited_by_count FROM citation_snapshots s
        WHERE s.retrieved_at = (
            SELECT max(retrieved_at) FROM citation_snapshots t WHERE t.record_id = s.record_id
        )
        """
    ).fetchall()
    counts = sorted(int(r[0]) for r in rows)
    if not counts:
        return {
            "citations.records_with_a_snapshot": 0,
            "citations.total": 0,
            "citations.median": 0.0,
            "citations.max": 0,
        }
    return {
        "citations.records_with_a_snapshot": len(counts),
        "citations.total": sum(counts),
        "citations.median": float(statistics.median(counts)),
        "citations.max": counts[-1],
    }


def _venue_numbers(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """The top :data:`TOP_N` venues by record count.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        ``venues.total`` plus ``venues.top<i>.name`` / ``.count`` for
        ``i`` in ``1..TOP_N``. Slots beyond the number of venues present are
        still emitted, with an empty name and a zero, so the key set does not
        depend on corpus size -- a manuscript citing ``venues.top3.name``
        must not start failing to fill because a re-capture dropped a venue.
    """
    rows = connection.execute(
        """
        SELECT v.name, count(*) AS n
        FROM records r JOIN venues v ON r.venue_id = v.venue_id
        GROUP BY v.name
        ORDER BY n DESC, v.name ASC
        LIMIT ?
        """,
        [TOP_N],
    ).fetchall()
    total = connection.execute("SELECT count(*) FROM venues").fetchone()
    numbers: dict[str, Any] = {"venues.total": int(total[0]) if total else 0}
    for index in range(TOP_N):
        name, count = (rows[index][0], int(rows[index][1])) if index < len(rows) else ("", 0)
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
        numbers.update(_citation_numbers(connection))
        numbers.update(_venue_numbers(connection))
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
