"""ISO 3166-1 alpha-3 country normalisation.

BUILD_PLAN Stage 3, modelling note 2 (line 884): Scopus affiliation
``country`` strings are free-text-ish (``"Korea, Republic of"``,
``"South Korea"``, ``"Viet Nam"``) and must be normalised to ISO-3166 alpha-3
via a **checked-in mapping table**, never a new dependency. Unmapped strings
must be preserved, never dropped, and the miss must be observable so
downstream geography analysis does not silently understate coverage
(BUILD_PLAN §5 risk 8).

This module owns that table. :mod:`prismabib.models` is the only expected
caller; it turns an unmapped miss into a ``structlog`` warning at the point
:class:`~prismabib.models.Affiliation` is validated.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Canonical ISO 3166-1 short names -> alpha-3.
#
# This is the ISO short name as commonly rendered in English, not exhaustive
# legal-name variants -- those are handled by _ALIASES below. Keys are looked
# up after normalising through `_normalise_key`, so casing/whitespace do not
# matter here.
# ---------------------------------------------------------------------------
_ISO_SHORT_NAMES: dict[str, str] = {
    "afghanistan": "AFG",
    "albania": "ALB",
    "algeria": "DZA",
    "andorra": "AND",
    "angola": "AGO",
    "antigua and barbuda": "ATG",
    "argentina": "ARG",
    "armenia": "ARM",
    "australia": "AUS",
    "austria": "AUT",
    "azerbaijan": "AZE",
    "bahamas": "BHS",
    "bahrain": "BHR",
    "bangladesh": "BGD",
    "barbados": "BRB",
    "belarus": "BLR",
    "belgium": "BEL",
    "belize": "BLZ",
    "benin": "BEN",
    "bhutan": "BTN",
    "bolivia": "BOL",
    "bolivia, plurinational state of": "BOL",
    "bosnia and herzegovina": "BIH",
    "botswana": "BWA",
    "brazil": "BRA",
    "brunei darussalam": "BRN",
    "brunei": "BRN",
    "bulgaria": "BGR",
    "burkina faso": "BFA",
    "burundi": "BDI",
    "cabo verde": "CPV",
    "cape verde": "CPV",
    "cambodia": "KHM",
    "cameroon": "CMR",
    "canada": "CAN",
    "central african republic": "CAF",
    "chad": "TCD",
    "chile": "CHL",
    "china": "CHN",
    "colombia": "COL",
    "comoros": "COM",
    "congo": "COG",
    "congo, democratic republic of the": "COD",
    "congo, the democratic republic of the": "COD",
    "democratic republic of the congo": "COD",
    "costa rica": "CRI",
    "cote d'ivoire": "CIV",
    "côte d'ivoire": "CIV",
    "ivory coast": "CIV",
    "croatia": "HRV",
    "cuba": "CUB",
    "cyprus": "CYP",
    "czechia": "CZE",
    "czech republic": "CZE",
    "denmark": "DNK",
    "djibouti": "DJI",
    "dominica": "DMA",
    "dominican republic": "DOM",
    "ecuador": "ECU",
    "egypt": "EGY",
    "el salvador": "SLV",
    "equatorial guinea": "GNQ",
    "eritrea": "ERI",
    "estonia": "EST",
    "eswatini": "SWZ",
    "swaziland": "SWZ",
    "ethiopia": "ETH",
    "fiji": "FJI",
    "finland": "FIN",
    "france": "FRA",
    "gabon": "GAB",
    "gambia": "GMB",
    "georgia": "GEO",
    "germany": "DEU",
    "ghana": "GHA",
    "greece": "GRC",
    "grenada": "GRD",
    "guatemala": "GTM",
    "guinea": "GIN",
    "guinea-bissau": "GNB",
    "guyana": "GUY",
    "haiti": "HTI",
    "holy see": "VAT",
    "vatican city": "VAT",
    "honduras": "HND",
    "hong kong": "HKG",
    "hungary": "HUN",
    "iceland": "ISL",
    "india": "IND",
    "indonesia": "IDN",
    "iran": "IRN",
    "iran, islamic republic of": "IRN",
    "iraq": "IRQ",
    "ireland": "IRL",
    "israel": "ISR",
    "italy": "ITA",
    "jamaica": "JAM",
    "japan": "JPN",
    "jordan": "JOR",
    "kazakhstan": "KAZ",
    "kenya": "KEN",
    "kiribati": "KIR",
    "korea, democratic people's republic of": "PRK",
    "north korea": "PRK",
    "korea, republic of": "KOR",
    "south korea": "KOR",
    "kosovo": "XKX",
    "kuwait": "KWT",
    "kyrgyzstan": "KGZ",
    "lao people's democratic republic": "LAO",
    "laos": "LAO",
    "latvia": "LVA",
    "lebanon": "LBN",
    "lesotho": "LSO",
    "liberia": "LBR",
    "libya": "LBY",
    "liechtenstein": "LIE",
    "lithuania": "LTU",
    "luxembourg": "LUX",
    "macao": "MAC",
    "macau": "MAC",
    "madagascar": "MDG",
    "malawi": "MWI",
    "malaysia": "MYS",
    "maldives": "MDV",
    "mali": "MLI",
    "malta": "MLT",
    "marshall islands": "MHL",
    "mauritania": "MRT",
    "mauritius": "MUS",
    "mexico": "MEX",
    "micronesia, federated states of": "FSM",
    "moldova": "MDA",
    "moldova, republic of": "MDA",
    "monaco": "MCO",
    "mongolia": "MNG",
    "montenegro": "MNE",
    "morocco": "MAR",
    "mozambique": "MOZ",
    "myanmar": "MMR",
    "burma": "MMR",
    "namibia": "NAM",
    "nauru": "NRU",
    "nepal": "NPL",
    "netherlands": "NLD",
    "netherlands, kingdom of the": "NLD",
    "new zealand": "NZL",
    "nicaragua": "NIC",
    "niger": "NER",
    "nigeria": "NGA",
    "north macedonia": "MKD",
    "macedonia": "MKD",
    "norway": "NOR",
    "oman": "OMN",
    "pakistan": "PAK",
    "palau": "PLW",
    "palestine, state of": "PSE",
    "palestine": "PSE",
    "panama": "PAN",
    "papua new guinea": "PNG",
    "paraguay": "PRY",
    "peru": "PER",
    "philippines": "PHL",
    "poland": "POL",
    "portugal": "PRT",
    "puerto rico": "PRI",
    "qatar": "QAT",
    "romania": "ROU",
    "russian federation": "RUS",
    "russia": "RUS",
    "rwanda": "RWA",
    "saint kitts and nevis": "KNA",
    "saint lucia": "LCA",
    "saint vincent and the grenadines": "VCT",
    "samoa": "WSM",
    "san marino": "SMR",
    "sao tome and principe": "STP",
    "saudi arabia": "SAU",
    "senegal": "SEN",
    "serbia": "SRB",
    "seychelles": "SYC",
    "sierra leone": "SLE",
    "singapore": "SGP",
    "slovakia": "SVK",
    "slovenia": "SVN",
    "solomon islands": "SLB",
    "somalia": "SOM",
    "south africa": "ZAF",
    "south sudan": "SSD",
    "spain": "ESP",
    "sri lanka": "LKA",
    "sudan": "SDN",
    "suriname": "SUR",
    "sweden": "SWE",
    "switzerland": "CHE",
    "syrian arab republic": "SYR",
    "syria": "SYR",
    "taiwan": "TWN",
    "taiwan, province of china": "TWN",
    "tajikistan": "TJK",
    "tanzania, united republic of": "TZA",
    "tanzania": "TZA",
    "thailand": "THA",
    "timor-leste": "TLS",
    "east timor": "TLS",
    "togo": "TGO",
    "tonga": "TON",
    "trinidad and tobago": "TTO",
    "tunisia": "TUN",
    "turkiye": "TUR",
    "türkiye": "TUR",
    "turkey": "TUR",
    "turkmenistan": "TKM",
    "tuvalu": "TUV",
    "uganda": "UGA",
    "ukraine": "UKR",
    "united arab emirates": "ARE",
    "united kingdom": "GBR",
    "united kingdom of great britain and northern ireland": "GBR",
    "uk": "GBR",
    "great britain": "GBR",
    "england": "GBR",
    "scotland": "GBR",
    "wales": "GBR",
    "northern ireland": "GBR",
    "united states of america": "USA",
    "united states": "USA",
    "usa": "USA",
    "u.s.a.": "USA",
    "u.s.": "USA",
    "uruguay": "URY",
    "uzbekistan": "UZB",
    "vanuatu": "VUT",
    "venezuela": "VEN",
    "venezuela, bolivarian republic of": "VEN",
    "viet nam": "VNM",
    "vietnam": "VNM",
    "yemen": "YEM",
    "zambia": "ZMB",
    "zimbabwe": "ZWE",
}

# ---------------------------------------------------------------------------
# Additional aliases beyond the ISO short-name variants folded into the table
# above. Kept separate so the provenance of "these are ISO names" vs "these
# are wire-format aliases we have actually seen" stays legible.
# ---------------------------------------------------------------------------
_ALIASES: dict[str, str] = {
    "republic of korea": "KOR",
    "democratic people's republic of korea": "PRK",
    "people's republic of china": "CHN",
    "prc": "CHN",
    "roc": "TWN",
    "republic of china": "TWN",
    "uae": "ARE",
    "ivory coast (cote d'ivoire)": "CIV",
    "republic of ireland": "IRL",
    "burma (myanmar)": "MMR",
}

# Every alpha-3 code maps to itself (case-insensitively), so re-normalising an
# already-normalised ``Affiliation.country`` -- as happens on every
# ``model_validate_json`` round trip -- is a no-op rather than a spurious
# "unmapped" warning.
_IDENTITY: dict[str, str] = {code.casefold(): code for code in _ISO_SHORT_NAMES.values()}

_COUNTRY_TABLE: dict[str, str] = {**_ISO_SHORT_NAMES, **_ALIASES, **_IDENTITY}

_WHITESPACE_RE = re.compile(r"\s+")


def _normalise_key(raw: str) -> str:
    """Fold a free-text country string into a lookup key.

    Args:
        raw: The raw country string as it appears in source data.

    Returns:
        A casefolded string with surrounding whitespace stripped and internal
        runs of whitespace collapsed to a single space, suitable for exact
        dictionary lookup against :data:`_COUNTRY_TABLE`.
    """
    return _WHITESPACE_RE.sub(" ", raw.strip()).casefold()


def normalise_country(raw: str) -> tuple[str, bool]:
    """Normalise a free-text country string to ISO 3166-1 alpha-3.

    Args:
        raw: A country string as emitted by a bibliographic source (e.g. a
            Scopus affiliation ``country`` field). Known variants include
            ``"Korea, Republic of"``, ``"South Korea"``, ``"Viet Nam"``,
            ``"USA"``, and ``"U.S.A."``.

    Returns:
        A tuple ``(value, matched)``. When ``raw`` maps to a known country,
        ``value`` is the ISO 3166-1 alpha-3 code and ``matched`` is ``True``.
        When ``raw`` is unmapped, ``value`` is ``raw`` unchanged (the string
        is *never* dropped or blanked) and ``matched`` is ``False``, so the
        caller can log the miss.
    """
    code = _COUNTRY_TABLE.get(_normalise_key(raw))
    if code is None:
        return raw, False
    return code, True
