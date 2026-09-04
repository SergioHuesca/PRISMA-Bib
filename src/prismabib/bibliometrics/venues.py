"""Top venues and venue-type split (BUILD_PLAN Stage 7, ADR 0022 Decision 5).

Venue normalisation is deliberately conservative -- casefold, collapse
whitespace, strip a trailing parenthetical, strip a leading ``The``, unify
``&``/``and``, drop trailing punctuation. Nothing here merges venues on a
similarity score: a wrong merge silently invents a venue that published
papers it did not.

The display name for a normalised group is the **most frequent** raw
variant, ties broken lexicographically, so the choice is deterministic and
reproducible (ADR 0022 Decision 5).

``report/numbers.py::_venue_numbers`` and ``report/tables.py::top_venues_table``
delegate to :func:`top_venues` rather than grouping by exact ``name``
themselves -- see those modules for the re-pointing.
"""

from __future__ import annotations

import re
from typing import Any

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

#: A trailing parenthetical that is *purely a year*, and nothing else.
#:
#: Deliberately not `\([^()]*\)`. A parenthetical suffix is precisely how
#: Scopus disambiguates same-titled journals -- `Sensors (Basel,
#: Switzerland)`, `Nature (London)` -- so stripping any parenthetical would
#: merge two distinct venues into one, which is the "a wrong merge silently
#: invents a venue that published papers it did not" failure ADR 0022
#: Decision 5 opens by forbidding. A bare year carries no such meaning.
#: What `top_venues` puts in the `venue_type` cell when no variant of a
#: normalised venue group declares one. The empty string, preserving the
#: `COALESCE(venue_type, '')` the pre-ADR-0022 SQL emitted, so
#: `report/tables.py::top_venues_table`'s golden does not move.
_NO_VENUE_TYPE_CELL = ""

#: What `venue_type_split` uses for the same fact. A *row label* rather than
#: a cell, and a blank row label is unreadable -- so it is named. The two
#: spellings are pinned together by
#: `test_venue_type_sentinels__are_the_documented_pair`, so a future author
#: changing one is told about the other.
_UNKNOWN_VENUE_TYPE = "unknown"

_TRAILING_PARENTHETICAL_RE = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
_LEADING_THE_RE = re.compile(r"^\s*the\s+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCTUATION_RE = re.compile(r"[\s.,;:]+$")

_EMPTY_VENUE_SCHEMA = {"venue": pl.Utf8, "venue_type": pl.Utf8, "count": pl.Int64}
_EMPTY_TYPE_SCHEMA = {"venue_type": pl.Utf8, "count": pl.Int64}

#: BUILD_PLAN names no default; 20 mirrors ``report/numbers.py::TOP_N`` being
#: a fixed constant rather than something that changes the key set of a
#: golden artefact -- callers who want more pass ``top_n`` explicitly, and
#: it is always recorded in ``params``.
DEFAULT_TOP_N = 20


def _normalise_venue_name(raw: str) -> str:
    """Fold one raw Scopus venue name into a grouping key (ADR 0022 Decision 5).

    Args:
        raw: A ``venues.name`` value as loaded from Scopus.

    Returns:
        The casefolded, conservatively normalised key. Never used as a
        display name -- see :func:`_display_names`.
    """
    text = raw.strip()
    text = _TRAILING_PARENTHETICAL_RE.sub("", text)
    text = _LEADING_THE_RE.sub("", text)
    text = text.replace("&", "and")
    text = _WHITESPACE_RE.sub(" ", text).strip()
    text = _TRAILING_PUNCTUATION_RE.sub("", text)
    return text.casefold()


def _group_venues(venues: pl.DataFrame) -> list[tuple[str, str, int]]:
    """Group raw venue rows by normalised name into ``(display_name, venue_type, count)``.

    A single pass over plain Python lists rather than a polars group-by
    chain, because the tie-break rules (most-frequent raw variant for the
    display name; ``"mixed"`` only when two *non-null* ``venue_type``
    values are both observed) are easier to state correctly this way than
    as a join of several aggregations, and a normalised corpus has at most
    a few thousand venue rows -- this is not the hot path.

    Args:
        venues: A ``Corpus.venues(...)``-shaped frame (``name``,
            ``venue_type`` columns read).

    Returns:
        One tuple per normalised venue group. Iteration and insertion order
        follow ``venues``'s own row order (``ORDER BY record_id``, a total
        order from the store), never a ``set``'s hash-dependent order --
        see ``bibliometrics/network.py``'s module docstring for why that
        distinction matters under ``pytest-randomly``.
    """
    groups: dict[str, dict[str, Any]] = {}
    variant_counts: dict[str, dict[str, int]] = {}
    for name, venue_type in zip(
        venues.get_column("name").to_list(), venues.get_column("venue_type").to_list(), strict=True
    ):
        norm = _normalise_venue_name(name)
        group = groups.setdefault(norm, {"count": 0, "types": set()})
        group["count"] += 1
        if venue_type is not None:
            group["types"].add(venue_type)
        variants = variant_counts.setdefault(norm, {})
        variants[name] = variants.get(name, 0) + 1

    rows: list[tuple[str, str, int]] = []
    for norm, group in groups.items():
        display = min(variant_counts[norm].items(), key=lambda item: (-item[1], item[0]))[0]
        types: set[str] = group["types"]
        if len(types) == 1:
            venue_type = next(iter(types))
        elif len(types) > 1:
            venue_type = "mixed"
        else:
            # The empty string, not `_UNKNOWN_VENUE_TYPE`: this value is
            # rendered directly into `report/tables.py::top_venues_table`,
            # whose golden pins the `COALESCE(venue_type, '')` the old SQL
            # emitted. `venue_type_split` uses the named sentinel instead,
            # because there the value is a *grouping key* a reader sees as a
            # row label, where a blank label is unreadable. Two spellings of
            # one fact is a drift risk (ADR 0022 Decision 5), so they are
            # tied together by `test_venue_type_sentinels__are_the_documented_pair`
            # rather than left to coincidence.
            venue_type = _NO_VENUE_TYPE_CELL
        rows.append((display, venue_type, group["count"]))
    rows.sort(key=lambda row: (-row[2], row[0]))
    return rows


def top_venues(
    corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED, top_n: int = DEFAULT_TOP_N
) -> AnalysisResult:
    """The most frequent publication venues, after name normalisation.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.
        top_n: How many venues to return.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``venue``, ``venue_type``, ``count``, sorted by
        ``count`` descending then ``venue`` ascending (a total order),
        limited to ``top_n`` rows. ``venue_type`` is the single type when
        every raw variant in the group agrees, ``"mixed"`` when Scopus
        indexes the same venue under more than one ``prism:aggregationType``
        (naming the disagreement is more useful to a reader than silently
        picking one), and ``""`` when no variant carries one.
    """
    records = corpus.records(stage)
    venues = corpus.venues(stage)

    if venues.height == 0:
        data = pl.DataFrame(schema=_EMPTY_VENUE_SCHEMA)
    else:
        rows = _group_venues(venues)[:top_n]
        data = pl.DataFrame(
            rows, schema=["venue", "venue_type", "count"], orient="row"
        ).with_columns(pl.col("count").cast(pl.Int64))

    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params={"top_n": top_n}, provenance=provenance)


def venue_type_split(
    corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED
) -> AnalysisResult:
    """Record counts by ``venues.venue_type`` (journal, conference, ...).

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``venue_type``, ``count``, sorted by ``count``
        descending then ``venue_type`` ascending. A record whose venue
        carries no ``venue_type`` is reported under ``"unknown"``, never
        dropped.
    """
    records = corpus.records(stage)
    venues = corpus.venues(stage)

    if venues.height == 0:
        data = pl.DataFrame(schema=_EMPTY_TYPE_SCHEMA)
    else:
        data = (
            venues.with_columns(
                pl.col("venue_type").fill_null(_UNKNOWN_VENUE_TYPE).alias("venue_type")
            )
            .group_by("venue_type")
            .agg(pl.len().alias("count"))
            .sort(["count", "venue_type"], descending=[True, False])
            .with_columns(pl.col("count").cast(pl.Int64))
        )

    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params={}, provenance=provenance)


__all__ = ["DEFAULT_TOP_N", "top_venues", "venue_type_split"]
