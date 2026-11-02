"""Methods-text sentences that quote the registered and orphaned counts (ADR 0032 C8, ADR 0033).

Pure functions of numbers already computed: the sentence quotes
``flow.registered`` from the ``numbers.json`` mapping, so the text cannot
disagree with the data.

Both are exposed to manuscript templates as ``{{methods.registered_sentence}}`` and
``{{methods.orphaned_decisions_text}}`` through ``numbers.json``
(:func:`~prismabib.report.numbers.numbers_map`); each is ``""`` when there is nothing to say.
There is no methods generator beyond these two snippets: manuscripts are user templates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from prismabib.prisma.orphans import OrphanedDecision


def registered_sentence(numbers: Mapping[str, Any]) -> str:
    """ADR 0032 Consequence 8's sentence, or ``""`` when nothing was registered.

    Args:
        numbers: The ``numbers.json`` mapping (needs ``corpus.size`` and
            ``flow.registered``).

    Returns:
        ``"N records, of which M are registrations from a manual Google Scholar
        search (ADR 0029)."``; empty for a project without registrations,
        where the sentence would assert nothing.
    """
    registered = int(numbers["flow.registered"])
    if not registered:
        return ""
    return (
        f"{numbers['corpus.size']} records, of which {registered} are registrations "
        "from a manual Google Scholar search (ADR 0029)."
    )


def venue_unnamed_sentence(unnamed: int, corpus_size: int) -> str:
    """The sentence stating how many records have no venue name, or ``""`` for none.

    Args:
        unnamed: Records with no venue name, from
            :func:`~prismabib.bibliometrics.venues.count_unnamed_venues`.
        corpus_size: ``corpus.size``.

    Returns:
        ``"N of M records have no venue name and are not counted among the venues."``;
        empty when every record is named, where the sentence would assert nothing.
    """
    if not unnamed:
        return ""
    return (
        f"{unnamed} of {corpus_size} records have no venue name "
        "and are not counted among the venues."
    )


def orphaned_decisions_text(orphans: Sequence[OrphanedDecision]) -> str:
    """The methods paragraph listing orphaned decisions (ADR 0033 §3).

    Args:
        orphans: The orphans, from :func:`~prismabib.prisma.orphans.orphaned_decisions`.

    Returns:
        ``""`` when there are none; otherwise a count line and one line per
        orphan with its ``event_id``, ``record_id``, stage and reason.
    """
    if not orphans:
        return ""
    lines = [
        (
            f"{len(orphans)} screening decision(s) refer to a record no longer in the "
            "record set. They are kept in the decision log and are not counted:"
        )
    ]
    lines += [
        f"- {o.record_id} ({o.stage}, {o.decision}, event {o.event_id}): {o.reason}"
        for o in orphans
    ]
    return "\n".join(lines)


_MANUAL = ("manual", "entered manually")
_COMPLETE = ("level1", "from Scopus COMPLETE")
_OPENALEX = ("level4", "from OpenAlex")

#: ``(field, Plural, "no <noun>", clauses)``. Keywords take no manual value (ADR 0041 Decision 3).
_CASCADE_FIELDS: tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...] = (
    ("abstract", "Abstracts", "abstract", (_COMPLETE, _OPENALEX, _MANUAL)),
    ("authors", "Author lists", "author list", (_COMPLETE, _OPENALEX, _MANUAL)),
    (
        "affiliations",
        "Affiliations",
        "affiliation data",
        (
            _COMPLETE,
            ("level2", "from Scopus STANDARD"),
            ("level3", "from Scopus META"),
            _OPENALEX,
            _MANUAL,
        ),
    ),
    ("keywords", "Keywords", "keywords", (_COMPLETE, ("level4", "from OpenAlex topics"))),
)
_CONFLICT_NOUN = {"abstract": "abstract", "authors": "author", "affiliations": "affiliation"}


def cascade_sentence(numbers: Mapping[str, Any]) -> str:
    """The methods sentence describing where each field came from (ADR 0041 Decision 5).

    Args:
        numbers: The mapping holding the 30 integer ``methods.cascade*`` keys.

    Returns:
        One clause per field, with its conflict count after it (none for keywords), then
        the open and resolved totals. Never empty: every key is always present.
    """
    parts: list[str] = []
    for field, plural, none_noun, clauses in _CASCADE_FIELDS:

        def count(suffix: str, field: str = field) -> int:
            return int(numbers[f"methods.cascade.{field}.{suffix}"])

        phrases = [f"{phrase} for {count(suffix)}" for suffix, phrase in clauses]
        phrases[0] += " records"
        taken = ", ".join(phrases[:-1]) + f" and {phrases[-1]}"
        parts.append(f"{plural} were taken {taken}; {count('unavailable')} have no {none_noun}.")
        if field in _CONFLICT_NOUN:
            parts.append(
                f"{count('conflicts')} {_CONFLICT_NOUN[field]} values differ between "
                "Scopus and OpenAlex."
            )
    parts.append(
        f"{int(numbers['methods.cascade_conflicts_open'])} conflicts remain open and "
        f"{int(numbers['methods.cascade_conflicts_resolved'])} were resolved by a reviewer."
    )
    return " ".join(parts)


__all__ = [
    "cascade_sentence",
    "orphaned_decisions_text",
    "registered_sentence",
    "venue_unnamed_sentence",
]
