"""DOI registrant-prefix publisher normalisation (ADR 0019, Stage 6).

The Stage 6 coverage table (S06-AC3) has to report full-text coverage *by
publisher*, and BUILD_PLAN does not say how "publisher" is derived. ADR 0019
decides it: from the DOI's registrant prefix (``10.1016`` -> Elsevier), never
from which resolver happened to succeed and never from the venue name.

**Why not the resolver.** Deriving publisher from which resolver served the
text is circular and defeats the acceptance criterion it is meant to serve:
every resolved paper would be Elsevier by construction (the only resolver
entitled to fetch Elsevier content is :class:`~prismabib.fulltext.resolve.ScienceDirectResolver`)
and every unresolved paper would have no publisher at all -- a coverage table
that reports its own method back to itself and can never show a gap. The DOI
prefix is assigned by the registrant and is knowable for a record this stage
never managed to resolve, which is exactly the population the table has to
describe: ``10.1016`` is Elsevier whether or not prismabib could read the
paper.

**Why not the venue name.** Venue names are not unique across publishers,
change over time, and the mapping would need thousands of entries to cover a
real corpus, where the DOI prefix needs a few dozen and is authoritative.

Shaped exactly like :mod:`prismabib.countries` and :mod:`prismabib.asjc`: a
closed, checked-in table, a ``(value, matched)`` return, and an unmapped
value preserved and surfaced rather than guessed at -- the same §5 risk 8
discipline applied to publishers. A record with no DOI is reported as
``"unknown"``, counted, and never silently dropped (a missing publisher would
otherwise vanish from the coverage table rather than appear as a gap in it).
"""

from __future__ import annotations

import re
from typing import Final

#: Every publisher this table currently maps, by DOI registrant prefix
#: (``10.XXXX``, the segment before the first ``/``). Extend by adding an
#: entry, never by inferring one -- an unmapped prefix is returned as a miss
#: (see :func:`publisher_from_doi`) rather than guessed at, so a publisher
#: this table does not know about cannot silently disappear from the
#: coverage table.
_PREFIX_TO_PUBLISHER: Final[dict[str, str]] = {
    "10.1016": "Elsevier",
    "10.1109": "IEEE",
    "10.1007": "Springer",
    "10.3390": "MDPI",
    "10.1145": "ACM",
    "10.1002": "Wiley",
    "10.1080": "Taylor & Francis",
    "10.1177": "SAGE",
    "10.1038": "Nature Portfolio",
    "10.1371": "PLOS",
    "10.3389": "Frontiers",
    "10.1155": "Hindawi",
    "10.1093": "Oxford University Press",
    "10.1017": "Cambridge University Press",
    "10.1088": "IOP Publishing",
    "10.1063": "AIP Publishing",
    "10.1103": "American Physical Society",
    "10.48550": "arXiv",
    # Additional publishers verifiable against the DOI Foundation's public
    # prefix registry, beyond BUILD_PLAN's required minimum.
    "10.1201": "CRC Press (Taylor & Francis)",
    "10.1049": "IET",
    "10.1186": "BioMed Central (Springer Nature)",
    "10.3233": "IOS Press",
    "10.2139": "SSRN",
    "10.1287": "INFORMS",
    "10.1061": "ASCE",
    "10.2514": "AIAA",
    "10.1115": "ASME",
    "10.1029": "AGU",
    "10.1039": "Royal Society of Chemistry",
    "10.1021": "American Chemical Society",
    "10.1073": "PNAS",
    "10.1126": "AAAS (Science)",
    "10.1364": "Optica Publishing Group",
    "10.4230": "Dagstuhl (LIPIcs)",
    # 10.24963 is IJCAI's registrant prefix (International Joint Conferences on
    # Artificial Intelligence), not AAAI's -- AAAI's own prefix is 10.1609. The
    # two were swapped in an earlier version of this table, which misattributed
    # every IJCAI paper to AAAI and left every real AAAI paper unmapped.
    "10.24963": "IJCAI",
    "10.1609": "AAAI",
    "10.1137": "SIAM",
    "10.2200": "Morgan & Claypool",
    "10.1142": "World Scientific",
}

#: The label used for a record that carries no DOI at all. Distinct from any
#: real DOI prefix, so it can never collide with a genuine miss.
UNKNOWN_PUBLISHER: Final[str] = "unknown"

#: A DOI registrant prefix: ``10.`` followed by 2-9 digits (DOI.org's stated
#: range), immediately followed by the ``/`` that separates it from the
#: suffix. Matched at the start of the (already URL/scheme-stripped) string.
_DOI_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^10\.\d{2,9}(?=/)")

# The same "DOI as a URL" forms `prismabib.models.normalise_doi` strips,
# duplicated rather than imported: `models.py` is Stage 1's bibliographic
# domain layer and this module has no other reason to depend on it, and a
# raw `records.doi` cell read back out of Layer 1 is not guaranteed to have
# already been normalised by every caller of this function.
_DOI_URL_PREFIXES: Final[tuple[str, ...]] = (
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://doi.org/",
    "http://doi.org/",
    "doi:",
)


def _strip_doi_url(raw: str) -> str:
    """Strip a DOI URL/scheme prefix, if present, case-insensitively.

    Args:
        raw: A DOI as stored or received, possibly wrapped in a URL or
            ``doi:`` scheme.

    Returns:
        The bare DOI text, whitespace-stripped, with a matched prefix (if
        any) removed. Case is otherwise preserved -- the DOI prefix this
        module matches against is numeric and case has no bearing on it.
    """
    text = raw.strip()
    lowered = text.casefold()
    for prefix in _DOI_URL_PREFIXES:
        if lowered.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def publisher_from_doi(doi: str | None) -> tuple[str, bool]:
    """Derive a publisher label from a DOI's registrant prefix.

    Args:
        doi: A record's DOI, in any of the forms prismabib encounters it
            (bare ``10.xxxx/...``, wrapped in a ``https://doi.org/`` URL or
            a ``doi:`` scheme, or ``None``/blank for a record that carries
            none).

    Returns:
        A tuple ``(value, matched)``:

        - No DOI (``None`` or all-whitespace): ``(UNKNOWN_PUBLISHER, False)``,
        counted as a distinct, visible bucket in the coverage table, never
        dropped and never guessed at.
        - A DOI whose registrant prefix is one of :data:`_PREFIX_TO_PUBLISHER`:
        ``(publisher_name, True)``.
        - A DOI whose prefix this table does not recognise, or that does not
        parse as a DOI at all: ``(value, False)`` where ``value`` is the
        (URL-stripped) prefix when one could be parsed, or the stripped input
        otherwise, preserved rather than blanked, so a miss can be reported
        and the table extended, never silently absorbed into
        ``UNKNOWN_PUBLISHER``, which means something different (no DOI at
        all).
    """
    if doi is None or not doi.strip():
        return UNKNOWN_PUBLISHER, False

    bare = _strip_doi_url(doi)
    match = _DOI_PREFIX_RE.match(bare)
    if match is None:
        return bare, False

    prefix = match.group(0)
    publisher = _PREFIX_TO_PUBLISHER.get(prefix)
    if publisher is None:
        return prefix, False
    return publisher, True


__all__ = ["UNKNOWN_PUBLISHER", "publisher_from_doi"]
