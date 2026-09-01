"""ASJC subject-area code normalisation.

Scopus's Abstract Retrieval API returns each subject area as
``{"@code": "2202", "@abbrev": "ENGI", "$": "Aerospace Engineering"}``.
``@code`` is the four-digit **ASJC** classification number; ``@abbrev`` is the
four-letter **top-level grouping** that number belongs to, and the first two
digits of the code *are* that grouping.

``criteria.yaml`` declares subject areas as those four-letter abbreviations --
``COMP``, ``ENGI``, ``MATH`` -- because that is the level a researcher writes a
protocol at ("computer science", not "1702 Artificial Intelligence"), and it is
what the file's own template teaches. Layer 1 stores ``@code``, the precise
value, because it is strictly more informative and discarding it would make the
finer classification unrecoverable.

Something therefore has to bridge the two, and this module is it: a closed,
checked-in table in the same spirit as :mod:`prismabib.countries`. Extend it by
adding entries, never by inferring. An unmapped prefix is returned as a miss
rather than guessed at, so a subject area this table does not know about cannot
silently drop a record from a review.

Without the bridge the filter does not merely under-match, it inverts: every
*enriched* record fails ``subject_areas`` while every record that failed to
enrich passes it, because a record with no subject-area data is kept by design.
See ADR 0017.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# ASJC top-level groupings: the first two digits of a four-digit ASJC code,
# mapped to Scopus's own four-letter abbreviation for that grouping.
#
# Transcribed from Scopus's published ASJC category list. `10` is the
# multidisciplinary grouping, whose only member is the code 1000 itself.
# ---------------------------------------------------------------------------
_PREFIX_TO_ABBREV: Final[dict[str, str]] = {
    "10": "MULT",  # Multidisciplinary
    "11": "AGRI",  # Agricultural and Biological Sciences
    "12": "ARTS",  # Arts and Humanities
    "13": "BIOC",  # Biochemistry, Genetics and Molecular Biology
    "14": "BUSI",  # Business, Management and Accounting
    "15": "CENG",  # Chemical Engineering
    "16": "CHEM",  # Chemistry
    "17": "COMP",  # Computer Science
    "18": "DECI",  # Decision Sciences
    "19": "EART",  # Earth and Planetary Sciences
    "20": "ECON",  # Economics, Econometrics and Finance
    "21": "ENER",  # Energy
    "22": "ENGI",  # Engineering
    "23": "ENVI",  # Environmental Science
    "24": "IMMU",  # Immunology and Microbiology
    "25": "MATE",  # Materials Science
    "26": "MATH",  # Mathematics
    "27": "MEDI",  # Medicine
    "28": "NEUR",  # Neuroscience
    "29": "NURS",  # Nursing
    "30": "PHAR",  # Pharmacology, Toxicology and Pharmaceutics
    "31": "PHYS",  # Physics and Astronomy
    "32": "PSYC",  # Psychology
    "33": "SOCI",  # Social Sciences
    "34": "VETE",  # Veterinary
    "35": "DENT",  # Dentistry
    "36": "HEAL",  # Health Professions
}

#: Every four-letter grouping this table knows, for validating what a
#: ``criteria.yaml`` declares before a review is run against it.
KNOWN_ABBREVS: Final[frozenset[str]] = frozenset(_PREFIX_TO_ABBREV.values())


def area_abbrev(raw: str) -> tuple[str, bool]:
    """Normalise one stored subject-area value to its four-letter grouping.

    Args:
        raw: A value from Layer 1's ``subject_areas.area_code`` -- normally a
            four-digit ASJC code such as ``"2202"``, but a four-letter
            abbreviation is accepted unchanged so that a store built from a
            capture that already carried abbreviations still matches.

    Returns:
        ``(abbrev, matched)``. ``matched`` is ``False`` when the value is
        neither a known ASJC prefix nor a known abbreviation, and ``abbrev``
        is then the input, upper-cased and stripped -- preserved, never
        dropped, so a caller can report the miss (§5 risk 8's discipline
        applied to subject areas).
    """
    folded = raw.strip().upper()
    if folded in KNOWN_ABBREVS:
        return folded, True
    if len(folded) == 4 and folded.isdigit():
        abbrev = _PREFIX_TO_ABBREV.get(folded[:2])
        if abbrev is not None:
            return abbrev, True
    return folded, False


__all__ = ["KNOWN_ABBREVS", "area_abbrev"]
