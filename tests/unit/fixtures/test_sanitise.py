"""Unit tests for ``tests/fixtures/sanitise.py`` (BUILD_PLAN §3.7.5, line 535).

"The sanitiser is itself tested" -- these are pure-function tests (no I/O,
no network) over the redaction and content-substitution helpers, so they
belong under ``unit``, same as any other pure-logic module in this repo.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest

from tests.fixtures.sanitise import (
    REDACTED,
    UnsanitisedFieldError,
    sanitise_abstract,
    sanitise_headers,
    sanitise_page,
    sanitise_query_string,
)


def _query_params(url: str) -> dict[str, str]:
    return dict(parse_qsl(urlsplit(url).query))


@pytest.mark.unit
def test_sanitise__real_key_present__is_redacted() -> None:
    real_key = "sk-live-9f8e7d6c5b4a3210abcdef1234567890"
    headers = {"X-ELS-APIKey": real_key, "Accept": "application/json"}
    url = f"https://api.elsevier.com/content/search/scopus?apiKey={real_key}&query=x"

    sanitised_headers = sanitise_headers(headers)
    sanitised_url = sanitise_query_string(url)

    assert real_key not in sanitised_headers.values()
    assert real_key not in sanitised_url
    assert sanitised_headers["X-ELS-APIKey"] == REDACTED
    assert _query_params(sanitised_url)["apiKey"] == REDACTED


@pytest.mark.unit
def test_sanitise__insttoken_present_in_header_and_query__both_redacted() -> None:
    real_token = "insttoken-abc123"
    headers = {"X-ELS-Insttoken": real_token}
    url = f"https://api.elsevier.com/content/search/scopus?insttoken={real_token}&query=x"

    sanitised_headers = sanitise_headers(headers)
    sanitised_url = sanitise_query_string(url)

    assert sanitised_headers["X-ELS-Insttoken"] == REDACTED
    assert real_token not in sanitised_url


@pytest.mark.unit
def test_sanitise__secret_leaked_into_an_unrelated_param__is_still_scrubbed() -> None:
    """A secret can leak somewhere unexpected (e.g. echoed into a ``query`` param
    by a buggy caller); the ``secrets`` argument exists to catch that too, not
    just the well-known parameter names.
    """
    real_key = "leaked-into-query-param-value"
    url = f"https://api.elsevier.com/content/search/scopus?query={real_key}"

    sanitised_url = sanitise_query_string(url, secrets=[real_key])

    assert real_key not in sanitised_url


@pytest.mark.unit
def test_sanitise_page__title_and_abstract__preserve_length_and_drop_original_text() -> None:
    page = {
        "search-results": {
            "entry": [
                {
                    "dc:title": "A Real, Licensed Paper Title: 2024 Edition!",
                    "dc:description": "This is the licensed abstract prose, verbatim.",
                }
            ]
        }
    }

    sanitised = sanitise_page(page, seed=0)

    entry = sanitised["search-results"]["entry"][0]
    assert len(entry["dc:title"]) == len("A Real, Licensed Paper Title: 2024 Edition!")
    assert len(entry["dc:description"]) == len("This is the licensed abstract prose, verbatim.")
    assert entry["dc:title"] != "A Real, Licensed Paper Title: 2024 Edition!"
    assert entry["dc:description"] != "This is the licensed abstract prose, verbatim."


@pytest.mark.unit
def test_sanitise_page__field_presence_and_absence__is_preserved() -> None:
    page = {
        "search-results": {
            "entry": [
                {"dc:identifier": "SCOPUS_ID:1", "dc:title": "Has authors"},
                {"dc:identifier": "SCOPUS_ID:2", "dc:title": "No authors", "author": []},
            ]
        }
    }
    page["search-results"]["entry"][0]["author"] = [{"authname": "Real N.", "surname": "Real"}]

    sanitised = sanitise_page(page, seed=0)

    first, second = sanitised["search-results"]["entry"]
    assert "author" in first and first["author"] != []
    assert second["author"] == []


# ---------------------------------------------------------------------------
# sanitise_abstract -- the Abstract Retrieval envelope
# ---------------------------------------------------------------------------


def _abstract_recording() -> dict[str, object]:
    """A recording-shaped Abstract Retrieval response with real-looking content."""
    return {
        "abstracts-retrieval-response": {
            "coredata": {
                "eid": "2-s2.0-85021234567",
                "dc:identifier": "SCOPUS_ID:85021234567",
                "prism:doi": "10.1016/j.example.2021.03.014",
                "prism:coverDate": "2021-03-01",
                "citedby-count": "37",
                "dc:title": "Vibration-based damage detection in steel truss bridges",
                "dc:description": "We present a data-driven framework for damage detection.",
                "prism:publicationName": "Journal of Applied Vibration Analysis",
                "dc:creator": {
                    "author": [
                        {
                            "@auid": "57204312876",
                            "ce:given-name": "Elena",
                            "ce:surname": "Marchetti",
                            "ce:indexed-name": "Marchetti E.",
                            "ce:initials": "E.",
                        }
                    ]
                },
            },
            "authors": {
                "author": [
                    {
                        "@auid": "57204312876",
                        "ce:given-name": "Elena",
                        "ce:surname": "Marchetti",
                        "ce:indexed-name": "Marchetti E.",
                        "ce:initials": "E.",
                        "orcid": "0000-0002-1825-0097",
                        "preferred-name": {
                            "ce:given-name": "Elena",
                            "ce:surname": "Marchetti",
                        },
                    }
                ]
            },
            "affiliation": [
                {
                    "@id": "60025858",
                    "affilname": "Politecnico di Milano",
                    "affiliation-city": "Milan",
                    "affiliation-country": "Italy",
                }
            ],
            "authkeywords": {"author-keyword": [{"$": "structural health monitoring"}]},
            "subject-areas": {
                "subject-area": [
                    {"@code": "2205", "@abbrev": "ENGI", "$": "Civil and Structural Engineering"}
                ]
            },
            "language": {"@xml:lang": "eng"},
        }
    }


@pytest.mark.unit
def test_sanitise_abstract__subject_areas__pass_through_byte_for_byte() -> None:
    """The codes are the entire reason the Abstract Retrieval call exists.

    A sanitiser that regenerated them would produce a cassette that pins
    nothing about the one field this feature was built to obtain -- and the
    contract test asserting ``@code``/``@abbrev``/``$`` would then be
    asserting the sanitiser's own output back to itself.
    """
    raw = _abstract_recording()

    sanitised = sanitise_abstract(raw)

    assert (
        sanitised["abstracts-retrieval-response"]["subject-areas"]
        == raw["abstracts-retrieval-response"]["subject-areas"]
    )


@pytest.mark.unit
def test_sanitise_abstract__work_identifiers__pass_through_unchanged() -> None:
    """Identifiers name a published paper, not a person (§2.5, and ``sanitise_page``)."""
    raw = _abstract_recording()

    coredata = sanitise_abstract(raw)["abstracts-retrieval-response"]["coredata"]

    assert coredata["eid"] == "2-s2.0-85021234567"
    assert coredata["dc:identifier"] == "SCOPUS_ID:85021234567"
    assert coredata["prism:doi"] == "10.1016/j.example.2021.03.014"
    assert coredata["prism:coverDate"] == "2021-03-01"
    assert coredata["citedby-count"] == "37"


@pytest.mark.unit
def test_sanitise_abstract__title_abstract_and_keywords__are_regenerated_at_the_same_length() -> (
    None
):
    raw = _abstract_recording()
    original = raw["abstracts-retrieval-response"]["coredata"]

    coredata = sanitise_abstract(raw)["abstracts-retrieval-response"]["coredata"]

    assert coredata["dc:title"] != original["dc:title"]
    assert len(coredata["dc:title"]) == len(original["dc:title"])
    assert coredata["dc:description"] != original["dc:description"]
    assert len(coredata["dc:description"]) == len(original["dc:description"])


@pytest.mark.unit
def test_sanitise_abstract__person_identifiers__are_remapped_never_passed_through() -> None:
    """A real ORCID beside a fabricated name is a pointer to a living researcher.

    Published on a PUBLIC repository, attached to someone else's identity. The
    synthetic ORCID is built check-digit-invalid so it can never resolve.
    """
    raw = _abstract_recording()

    author = sanitise_abstract(raw)["abstracts-retrieval-response"]["authors"]["author"][0]

    assert author["@auid"] != "57204312876"
    assert author["orcid"] != "0000-0002-1825-0097"
    assert author["ce:surname"] != "Marchetti"


@pytest.mark.unit
def test_sanitise_abstract__one_author_listed_twice__gets_one_synthetic_identity() -> None:
    """``coredata.dc:creator`` repeats the first author; both copies must agree.

    A response whose ``authors.author[0]`` and ``coredata["dc:creator"]``
    carry the same ``@auid`` under two different names is not a shape any real
    response has, and a cassette that had it would quietly teach a future
    reader that the two blocks may disagree.
    """
    sanitised = sanitise_abstract(_abstract_recording())["abstracts-retrieval-response"]

    listed = sanitised["authors"]["author"][0]
    creator = sanitised["coredata"]["dc:creator"]["author"][0]

    assert listed["@auid"] == creator["@auid"]
    assert listed["ce:surname"] == creator["ce:surname"]


@pytest.mark.unit
def test_sanitise_abstract__field_presence_and_absence__is_preserved() -> None:
    raw = _abstract_recording()

    sanitised = sanitise_abstract(raw)

    assert set(sanitised["abstracts-retrieval-response"]) == set(
        raw["abstracts-retrieval-response"]
    )
    assert set(sanitised["abstracts-retrieval-response"]["coredata"]) == set(
        raw["abstracts-retrieval-response"]["coredata"]
    )


@pytest.mark.unit
def test_sanitise_abstract__same_input_twice__is_byte_identical() -> None:
    """A cassette that churns on every run makes its diff unreviewable (§3.7.5)."""
    assert sanitise_abstract(_abstract_recording()) == sanitise_abstract(_abstract_recording())


@pytest.mark.unit
def test_sanitise_abstract__unknown_subtree__raises_instead_of_publishing_it() -> None:
    """The fail-closed rule, and the exact field it exists for.

    A real ``view=FULL`` recording carries ``item.bibrecord.head``, which
    repeats the abstract prose, the author list and the affiliations in an
    entirely different schema. A sanitiser that copied through what it did not
    recognise would publish licensed text to a PUBLIC repository and report
    success -- the one failure mode §2.5 says cannot be undone.
    """
    raw = _abstract_recording()
    raw["abstracts-retrieval-response"]["item"] = {
        "bibrecord": {"head": {"abstracts": "The full licensed abstract, again."}}
    }

    with pytest.raises(UnsanitisedFieldError) as excinfo:
        sanitise_abstract(raw)

    assert "item" in str(excinfo.value)


@pytest.mark.unit
def test_sanitise_abstract__unknown_container_inside_coredata__raises() -> None:
    """New *scalars* in ``coredata`` are safe to pass through; new containers are not."""
    raw = _abstract_recording()
    raw["abstracts-retrieval-response"]["coredata"]["some-future-block"] = {
        "prose": "Licensed text nobody taught the sanitiser about."
    }

    with pytest.raises(UnsanitisedFieldError) as excinfo:
        sanitise_abstract(raw)

    assert "some-future-block" in str(excinfo.value)


@pytest.mark.unit
def test_sanitise_abstract__a_new_scalar_in_coredata__passes_through() -> None:
    """The guard must not be so broad that it blocks ordinary recordings.

    A sanitiser that refuses every unfamiliar key is a sanitiser people stop
    running -- and the first thing anyone does with a guard that blocks
    legitimate work is bypass it.
    """
    raw = _abstract_recording()
    raw["abstracts-retrieval-response"]["coredata"]["pubmed-id"] = "33445566"

    sanitised = sanitise_abstract(raw)

    assert sanitised["abstracts-retrieval-response"]["coredata"]["pubmed-id"] == "33445566"
