"""Tests for ``src/prismabib/sources/openalex.py`` (ADR 0028, level 4).

No live network: HTTP is mocked at the boundary with ``respx`` and the bodies
are the hand-written, synthetic cassettes in ``tests/fixtures/openalex/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import RateLimitError
from prismabib.sources.cache import HttpCache
from prismabib.sources.openalex import (
    FieldOutcome,
    NotFound,
    OpenAlexClient,
    OpenAlexWork,
    Refused,
    TopicKeyword,
    authorships_to_fields,
    field_outcomes,
    rebuild_abstract,
    topics_to_keywords,
)
from prismabib.sources.ratelimit import RateLimiter

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "openalex"
_DOI_ONE = "10.9999/synthetic.one"
_DOI_TWO = "10.9999/synthetic.two"
_DOI_MISSING = "10.9999/nobody.home"


def _cassette(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _client(key: str | None = None, **kwargs: Any) -> OpenAlexClient:
    settings = Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        openalex_api_key=key,
    )
    return OpenAlexClient(settings, rate_limiter=RateLimiter(rate=1000.0), **kwargs)


def _work(name: str, index: int) -> OpenAlexWork:
    with respx.mock:
        respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette(name))
        )
        found = _client().lookup_dois([_DOI_ONE, _DOI_TWO])
    work = list(found.values())[index]
    assert isinstance(work, OpenAlexWork)
    return work


# --- pure functions -------------------------------------------------------


@pytest.mark.unit
def test_rebuild_abstract__repeated_word__places_each_position() -> None:
    index = {"We": [0], "track": [1, 4], "pitches": [2], "and": [3], "batters": [5]}
    assert rebuild_abstract(index) == "We track pitches and track batters"


@pytest.mark.unit
@pytest.mark.parametrize("index", [None, {}])
def test_rebuild_abstract__missing_or_empty__is_none(index: dict[str, list[int]] | None) -> None:
    assert rebuild_abstract(index) is None


@pytest.mark.unit
def test_authorships__with_and_without_institutions() -> None:
    fields = authorships_to_fields(_work("works_batch.json", 0))

    assert fields.authors == ("Ada Synthetic", "Bo Placeholder", "Cy Unaffiliated")
    # De-duplicated, in order; the author with no institutions adds nothing.
    assert fields.affiliations == ("University of Nowhere", "Institute of Examples")


@pytest.mark.unit
def test_authorships__none__is_empty() -> None:
    fields = authorships_to_fields(OpenAlexWork(None, None, None, None))
    assert fields.authors == () and fields.affiliations == ()


@pytest.mark.unit
def test_topics_to_keywords__returns_openalex_topic_kind() -> None:
    keywords = topics_to_keywords(_work("works_batch.json", 0))

    assert keywords == (
        TopicKeyword("Sports Analytics", 0.98, "openalex-topic"),
        TopicKeyword("Object Tracking", 0.91, "openalex-topic"),
    )


@pytest.mark.unit
def test_field_outcomes__work_with_and_without_abstract() -> None:
    full = field_outcomes(_work("works_batch.json", 0))
    bare = field_outcomes(_work("works_batch.json", 1))

    assert full["abstract"] == FieldOutcome("value", "We track pitches and track batters")
    assert bare["abstract"] == FieldOutcome("absent")
    assert bare["authors"] == FieldOutcome("absent")


@pytest.mark.unit
def test_field_outcomes__refused_and_not_found() -> None:
    assert field_outcomes(Refused(401))["abstract"] == FieldOutcome("refused", http_status=401)
    assert field_outcomes(NotFound())["keywords"] == FieldOutcome("not_found")


# --- lookup_dois ----------------------------------------------------------


@pytest.mark.integration
def test_lookup_dois__batch__maps_found_and_missing_and_sends_pipe_filter() -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("works_batch.json"))
        )
        result = _client().lookup_dois([_DOI_ONE, _DOI_TWO, _DOI_MISSING])

    request = route.calls.last.request
    assert request.url.params["filter"] == f"doi:{_DOI_ONE}|{_DOI_TWO}|{_DOI_MISSING}"
    assert "abstract_inverted_index" in request.url.params["select"]
    work = result[_DOI_ONE]
    assert isinstance(work, OpenAlexWork) and work.title == "Pitch Tracking with Vision Models"
    assert isinstance(result[_DOI_TWO], OpenAlexWork)
    assert result[_DOI_MISSING] == NotFound()


@pytest.mark.integration
def test_lookup_dois__over_fifty__splits_into_batches() -> None:
    dois = [f"10.9999/d{i}" for i in range(120)]
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        result = _client().lookup_dois(dois)

    sizes = [len(c.request.url.params["filter"][4:].split("|")) for c in route.calls]
    assert sizes == [50, 50, 20]
    assert set(result) == set(dois)


@pytest.mark.integration
@pytest.mark.parametrize("status", [401, 403])
def test_lookup_dois__401_or_403__is_refused_with_status_not_raised(status: int) -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(status))
        result = _client().lookup_dois([_DOI_ONE])

    assert result == {_DOI_ONE: Refused(status)}
    assert route.call_count == 1  # a refusal is not retried


def _single_work_body(doi: str) -> dict[str, Any]:
    return {**_cassette("works_batch.json")["results"][0], "doi": f"https://doi.org/{doi}"}


@pytest.mark.integration
def test_lookup_dois__batch_404__retries_each_doi_and_only_a_per_doi_404_is_not_found() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/works":  # the batch call
            return httpx.Response(404)
        if request.url.path == f"/works/doi:{_DOI_ONE}":
            return httpx.Response(200, json=_single_work_body(_DOI_ONE))
        return httpx.Response(404)

    with respx.mock:
        route = respx.route(host="api.openalex.org").mock(side_effect=respond)
        result = _client().lookup_dois([_DOI_ONE, _DOI_MISSING])

    assert route.call_count == 3  # batch + one per DOI
    assert isinstance(result[_DOI_ONE], OpenAlexWork)
    assert result[_DOI_MISSING] == NotFound()


@pytest.mark.integration
@pytest.mark.parametrize("doi", ["10.9999/a|b", "10.9999/a,b"])
def test_lookup_dois__doi_with_filter_syntax__is_looked_up_by_encoded_path(doi: str) -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_single_work_body(doi))

    with respx.mock:
        respx.route(host="api.openalex.org").mock(side_effect=respond)
        result = _client().lookup_dois([doi])

    assert len(seen) == 1  # never inside a batch filter
    assert "filter" not in seen[0].url.params
    assert seen[0].url.raw_path.split(b"?")[0].decode() == f"/works/doi:{quote(doi, safe='')}"
    assert isinstance(result[doi], OpenAlexWork)


@pytest.mark.integration
def test_lookup_dois__special_doi_mixed_with_normal__only_normal_is_batched() -> None:
    special = "10.9999/a|b"

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/works":
            assert request.url.params["filter"] == f"doi:{_DOI_ONE}"
            return httpx.Response(200, json=_cassette("works_batch.json"))
        return httpx.Response(404)

    with respx.mock:
        respx.route(host="api.openalex.org").mock(side_effect=respond)
        result = _client().lookup_dois([_DOI_ONE, special])

    assert isinstance(result[_DOI_ONE], OpenAlexWork)
    assert result[special] == NotFound()


@pytest.mark.integration
def test_lookup_dois__429_then_200__retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            side_effect=[
                httpx.Response(429),
                httpx.Response(200, json=_cassette("works_batch.json")),
            ]
        )
        result = _client().lookup_dois([_DOI_ONE])

    assert route.call_count == 2
    assert isinstance(result[_DOI_ONE], OpenAlexWork)


@pytest.mark.integration
def test_lookup_dois__429_forever__raises_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(429))
        with pytest.raises(RateLimitError):
            _client().lookup_dois([_DOI_ONE])

    assert route.call_count == OpenAlexClient.MAX_ATTEMPTS


# --- API key and cache ----------------------------------------------------


@pytest.mark.integration
def test_api_key__sent_on_the_wire_but_absent_from_cache_files(tmp_path: Path) -> None:
    secret = "sekrit-openalex-key"  # pragma: allowlist secret
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("works_batch.json"))
        )
        _client(secret, cache=HttpCache(tmp_path)).lookup_dois([_DOI_ONE])

    assert route.calls.last.request.url.params["api_key"] == secret
    for path in tmp_path.rglob("*"):
        assert secret.encode() not in path.name.encode()
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


@pytest.mark.integration
def test_cache__second_lookup__does_not_hit_the_network_even_with_another_key(
    tmp_path: Path,
) -> None:
    cache = HttpCache(tmp_path)
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("works_batch.json"))
        )
        _client("key-a", cache=cache).lookup_dois([_DOI_ONE])
        again = _client("key-b", cache=cache).lookup_dois([_DOI_ONE])

    assert route.call_count == 1
    assert isinstance(again[_DOI_ONE], OpenAlexWork)


@pytest.mark.integration
def test_no_api_key__sends_no_api_key_param() -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        _client().lookup_dois([_DOI_ONE])

    assert "api_key" not in route.calls.last.request.url.params


# --- search_title ---------------------------------------------------------


@pytest.mark.integration
def test_search_title__exact_normalised_title_and_year__matches() -> None:
    with respx.mock:
        respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("title_search.json"))
        )
        match = _client().search_title("pitch tracking with vision models", 2021)

    # Skips the longer title (2021) and the right title in the wrong year (2019).
    assert isinstance(match, OpenAlexWork)
    assert match.openalex_id == "https://openalex.org/W2000000003"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("title", "year"),
    [
        ("Pitch Tracking with Vision Models", 2018),  # right title, no work that year
        ("Pitch Tracking with Vision", 2021),  # prefix only
        ("Totally Different Paper", 2021),
    ],
)
def test_search_title__near_miss__is_rejected(title: str, year: int) -> None:
    with respx.mock:
        respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("title_search.json"))
        )
        assert _client().search_title(title, year) is None


@pytest.mark.integration
def test_search_title__401__is_refused() -> None:
    with respx.mock:
        respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(401))
        assert _client().search_title("Anything", 2021) == Refused(401)
