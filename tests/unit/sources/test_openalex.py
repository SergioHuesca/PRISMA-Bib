"""Tests for ``src/prismabib/sources/openalex.py`` (ADR 0028, level 4).

No live network: HTTP is mocked at the boundary with ``respx`` and the bodies
are the hand-written, synthetic cassettes in ``tests/fixtures/openalex/``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import AuthError, EntitlementError, RateLimitError, ValidationError
from prismabib.sources.cache import HttpCache
from prismabib.sources.openalex import (
    FieldOutcome,
    NotFound,
    OpenAlexClient,
    OpenAlexWork,
    Refused,
    TopicKeyword,
    _HttpxKeyRedactionFilter,
    authorships_to_fields,
    field_outcomes,
    lookup_outcome,
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
def test_lookup_dois__401_or_403_in_opt_in_run__is_refused_with_status(status: int) -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(status))
        result = _client().lookup_dois([_DOI_ONE], fallback_opt_in=True)

    assert result == {_DOI_ONE: Refused(status)}
    assert route.call_count == 1  # a refusal is not retried


@pytest.mark.integration
@pytest.mark.parametrize(("status", "error"), [(401, AuthError), (403, EntitlementError)])
def test_lookup_dois__401_or_403_outside_opt_in__raises(
    status: int, error: type[Exception]
) -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(status))
        with pytest.raises(error):
            _client().lookup_dois([_DOI_ONE])

    assert route.call_count == 1


@pytest.mark.integration
def test_lookup_dois__case_variant_dois__one_request_entry_and_result_per_input() -> None:
    upper, lower = "10.9999/SYNTHETIC.ONE", "10.9999/Synthetic.One"
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json=_cassette("works_batch.json"))
        )
        result = _client().lookup_dois([upper, lower, _DOI_ONE])

    assert route.call_count == 1
    assert route.calls.last.request.url.params["filter"] == f"doi:{_DOI_ONE}"
    assert set(result) == {upper, lower, _DOI_ONE}
    assert all(isinstance(w, OpenAlexWork) for w in result.values())


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

    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {secret}"
    assert "api_key" not in request.url.params
    assert secret not in str(request.url)
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
def test_cache__bad_200_then_good__nothing_cached_and_retry_hits_the_network(
    tmp_path: Path,
) -> None:
    cache = HttpCache(tmp_path)
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, content=b"<html>captive portal</html>"),
                httpx.Response(200, json=_cassette("works_batch.json")),
            ]
        )
        client = _client(cache=cache)
        with pytest.raises(ValidationError):
            client.lookup_dois([_DOI_ONE])
        assert not [p for p in tmp_path.rglob("*") if p.is_file()]

        assert isinstance(client.lookup_dois([_DOI_ONE])[_DOI_ONE], OpenAlexWork)
        assert route.call_count == 2
        client.lookup_dois([_DOI_ONE])
    assert route.call_count == 2


@pytest.mark.integration
def test_no_api_key__sends_no_authorization_header() -> None:
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        _client().lookup_dois([_DOI_ONE])

    assert "Authorization" not in route.calls.last.request.headers


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
def test_search_title__401__refused_only_in_opt_in_run_else_raises() -> None:
    with respx.mock:
        respx.get(OpenAlexClient.ENDPOINT).mock(return_value=httpx.Response(401))
        assert _client().search_title("Anything", 2021, fallback_opt_in=True) == Refused(401)
        with pytest.raises(AuthError):
            _client().search_title("Anything", 2021)


@pytest.mark.integration
def test_lookup_one__401_by_default__raises_auth_error() -> None:
    doi = "10.9999/a|b"  # filter syntax forces the single-lookup path
    with respx.mock:
        route = respx.get(f"{OpenAlexClient.ENDPOINT}/doi:{quote(doi, safe='')}").mock(
            return_value=httpx.Response(401)
        )
        with pytest.raises(AuthError):
            _client()._lookup_one(doi, fallback_opt_in=False)

    assert route.call_count == 1


@pytest.mark.integration
def test_lookup_one__401_in_opt_in_run__is_refused() -> None:
    with respx.mock:
        respx.route(host="api.openalex.org").mock(return_value=httpx.Response(401))
        assert _client()._lookup_one(_DOI_ONE, fallback_opt_in=True) == Refused(401)


# --- the key never reaches a log, a refusal never reaches the cache -------

_SENTINEL = "sekrit key/with+chars"  # pragma: allowlist secret


def _httpx_log_text(caplog: pytest.LogCaptureFixture, *, key_in_query: bool) -> str:
    """Run a lookup through a real httpx.Client on a MockTransport; return the INFO log."""

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = _client(_SENTINEL, http_client=http)
        if key_in_query:
            # Simulate a future regression that moves the key back into the URL.
            real_get = http.get
            http.get = lambda url, **kw: real_get(  # type: ignore[method-assign]
                url, params={**kw["params"], "api_key": _SENTINEL}
            )
        with caplog.at_level(logging.INFO, logger="httpx"):
            client.lookup_dois([_DOI_ONE])
    return caplog.text


@pytest.mark.unit
def test_httpx_info_log__header_form__never_contains_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _httpx_log_text(caplog, key_in_query=False)
    assert "HTTP Request" in text
    assert _SENTINEL not in text
    assert quote_plus(_SENTINEL, safe="") not in text


@pytest.mark.unit
def test_httpx_info_log__query_form__regression__never_contains_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = _httpx_log_text(caplog, key_in_query=True)
    assert "HTTP Request" in text
    assert "api_key=***" in text
    assert _SENTINEL not in text
    assert quote_plus(_SENTINEL, safe="") not in text


@pytest.mark.unit
def test_httpx_info_log__two_clients_with_different_keys__neither_key_leaks() -> None:
    _client("first-key-aaa")
    _client("second-key-bbb")
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "GET %s", ("/x?k=first-key-aaa&j=second-key-bbb",), None
    )
    for f in logging.getLogger("httpx").filters:
        f.filter(record)
    assert record.getMessage() == "GET /x?k=***&j=***"


@pytest.mark.integration
def test_refusal__in_opt_in_run__is_not_written_to_or_served_from_the_cache(
    tmp_path: Path,
) -> None:
    cache = HttpCache(tmp_path)
    with respx.mock:
        route = respx.get(OpenAlexClient.ENDPOINT).mock(
            side_effect=[
                httpx.Response(401, json={"results": [{"id": "x"}]}),
                httpx.Response(200, json=_cassette("works_batch.json")),
            ]
        )
        first = _client(cache=cache).lookup_dois([_DOI_ONE], fallback_opt_in=True)
        assert not [p for p in tmp_path.rglob("*") if p.is_file()]
        second = _client(cache=cache).lookup_dois([_DOI_ONE], fallback_opt_in=True)

    assert first == {_DOI_ONE: Refused(401)}
    assert route.call_count == 2  # the refusal was not replayed from the cache
    assert isinstance(second[_DOI_ONE], OpenAlexWork)


def _scrub(keys: set[str], message: str) -> str:
    """Run one record through a fresh (not process-wide) redaction filter."""
    redaction = _HttpxKeyRedactionFilter()
    redaction.keys = keys
    record = logging.LogRecord("httpx", logging.INFO, __file__, 1, message, None, None)
    redaction.filter(record)
    return record.getMessage()


@pytest.mark.unit
def test_httpx_filter__key_that_contains_another__longest_is_redacted_first() -> None:
    # Shortest-first would turn "abcdef" into "***def", leaking its tail.
    assert _scrub({"abc", "abcdef"}, "GET /x?k=abcdef&j=abc") == "GET /x?k=***&j=***"


@pytest.mark.unit
def test_lookup_outcome__doi_and_title__match_exactly_and_report_absent_fields() -> None:
    bare = OpenAlexWork("W1", "10.1/a", "Deep Learning!", 2021)
    other = OpenAlexWork("W2", "10.1/b", "Deep Learning Again", 2021)
    assert lookup_outcome([other, bare], doi="HTTPS://doi.org/10.1/A") == (
        bare,
        ["abstract", "authors", "affiliations", "keywords"],
    )
    assert lookup_outcome([other, bare], title="deep learning", year=2021)[0] is bare
    assert lookup_outcome([other, bare], title="deep learning", year=2020) == (None, [])
    assert lookup_outcome([other], title="deep learning") == (None, [])


@pytest.mark.unit
def test_lookup_outcome__title_with_accents_and_hyphen__matches_the_plain_form() -> None:
    work = OpenAlexWork("W1", None, "Well-being: Études", 2020)
    assert lookup_outcome([work], title="Well being: Etudes", year=2020)[0] is work


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "work_title", "year"),
    [
        ("wellbeing", "Well being", 2020),  # a fused word is not the split one
        ("Well being", "Well being", 2019),  # year mismatch
        ("Well being stud", "Well being study", 2020),  # prefix near miss
    ],
)
def test_lookup_outcome__title_near_misses__stay_not_found(
    title: str, work_title: str, year: int
) -> None:
    work = OpenAlexWork("W1", None, work_title, 2020)
    assert lookup_outcome([work], title=title, year=year) == (None, [])


@pytest.mark.unit
def test_httpx_filter__empty_key__leaves_the_message_intact() -> None:
    # replace("", "***") would insert *** between every character.
    assert _scrub({"", "secret"}, "HTTP Request: GET /x?k=secret") == "HTTP Request: GET /x?k=***"
    assert _scrub({""}, "HTTP Request: GET /x") == "HTTP Request: GET /x"
