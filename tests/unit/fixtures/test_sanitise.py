"""Unit tests for ``tests/fixtures/sanitise.py`` (BUILD_PLAN §3.7.5, line 535).

"The sanitiser is itself tested" -- these are pure-function tests (no I/O,
no network) over the redaction and content-substitution helpers, so they
belong under ``unit``, same as any other pure-logic module in this repo.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest

from tests.fixtures.sanitise import REDACTED, sanitise_headers, sanitise_page, sanitise_query_string


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
