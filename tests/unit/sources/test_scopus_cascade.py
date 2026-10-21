"""Unit tests for the ADR 0028 level 2/3 Scopus steps. Synthetic fixtures; no network (respx)."""

from __future__ import annotations

import copy
import json
import pickle
from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import AuthError, EntitlementError, ValidationError
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import DOI_QUERY_TERM, ScopusClient, extract_total_results

_FIX = Path(__file__).parents[2] / "fixtures" / "scopus_cascade"
_SEARCH = ScopusClient.SEARCH_ENDPOINT
_ABSTRACT = ScopusClient.ABSTRACT_ENDPOINT_TEMPLATE.format(scopus_id="2-s2.0-111")
_EMPTY = {"search-results": {"opensearch:totalResults": "0", "entry": []}}


def _fx(name: str) -> dict:
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _client() -> ScopusClient:
    settings = Settings(_env_file=None, scopus_api_key="test-api-key")  # pragma: allowlist secret
    return ScopusClient(settings, rate_limiter=RateLimiter(rate=1000.0))


def test_doi_query_template__is_pinned() -> None:
    # Unverified against live Scopus: this pins what we send, not that Scopus accepts it.
    assert DOI_QUERY_TERM.format(doi="10.1/x") == 'DOI("10.1/x")'


def test_search_standard__batches_and_sends_standard_view() -> None:
    dois = [f"10.1/{i}" for i in range(5)]
    with respx.mock:
        route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_EMPTY))
        _client().search_standard_by_dois(dois, batch_size=2)
    params = [r.request.url.params for r in route.calls]
    assert len(params) == 3  # 2 + 2 + 1
    assert {p["view"] for p in params} == {"STANDARD"}
    assert params[0]["query"] == 'DOI("10.1/0") OR DOI("10.1/1")'
    assert params[2]["query"] == 'DOI("10.1/4")'


def test_search_standard__default_batch_is_25() -> None:
    dois = [f"10.1/{i}" for i in range(26)]
    with respx.mock:
        route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_EMPTY))
        _client().search_standard_by_dois(dois)
    assert route.call_count == 2


def test_search_standard__rejects_batch_larger_than_a_page() -> None:
    with pytest.raises(ValidationError):
        _client().search_standard_by_dois(["10.1/a"], batch_size=26)


def test_search_standard__maps_value_absent_not_found() -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_fx("search_standard.json")))
        out = _client().search_standard_by_dois(["10.1000/a", "10.1000/B", "10.1000/zzz"])
    value = out["10.1000/a"]  # matched case-insensitively
    assert value.outcome == "value"
    assert value.first_author == "Doe J."
    assert (value.affiliations[0].city, value.affiliations[0].country) == ("Madrid", "Spain")
    assert value.identifiers is not None and value.identifiers["eid"] == "2-s2.0-111"
    assert out["10.1000/B"].outcome == "absent"  # entry exists, no affiliation or author
    assert out["10.1000/zzz"].outcome == "not_found"


def test_abstract_meta__value_and_view_meta() -> None:
    with respx.mock:
        route = respx.get(_ABSTRACT).mock(
            return_value=httpx.Response(200, json=_fx("abstract_meta.json"))
        )
        result = _client().abstract_meta("scopus:2-s2.0-111")
    assert route.calls[0].request.url.params["view"] == "META"
    assert result.outcome == "value"
    assert result.first_author == "Doe J."
    assert result.affiliations[0].country == "Spain"  # single affiliation arrives as a bare object


def test_search_standard__reads_verified_affiliation_keys() -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_fx("search_standard.json")))
        aff = _client().search_standard_by_dois(["10.1000/a"])["10.1000/a"].affiliations[0]
    assert (aff.name, aff.city, aff.country) == ("Univ Example", "Madrid", "Spain")


def test_extract_total_results__reads_exact_opensearch_key() -> None:
    page = _fx("search_standard.json")
    assert extract_total_results(page) == 2
    page["search-results"]["totalResults"] = page["search-results"].pop("opensearch:totalResults")
    with pytest.raises(ValidationError):
        extract_total_results(page)


@pytest.mark.parametrize(
    "creator",
    [
        "Doe J.",
        {"ce:indexed-name": "Doe J."},
        {"author": [{"ce:indexed-name": "Doe J."}, {"ce:indexed-name": "Roe R."}]},
    ],
)
def test_search_standard__dc_creator_string_or_dict(creator: object) -> None:
    page = _fx("search_standard.json")
    page["search-results"]["entry"][0]["dc:creator"] = creator
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=page))
        out = _client().search_standard_by_dois(["10.1000/a"])
    assert out["10.1000/a"].first_author == "Doe J."


@pytest.mark.parametrize("location", ["coredata", "body", "root"])
def test_abstract_meta__dc_creator_in_any_location(location: str) -> None:
    page = _fx("abstract_meta.json")
    body = page["abstracts-retrieval-response"]
    del body["coredata"]["dc:creator"]
    {"coredata": body["coredata"], "body": body, "root": page}[location]["dc:creator"] = "Doe J."
    with respx.mock:
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(200, json=page))
        assert _client().abstract_meta("2-s2.0-111").first_author == "Doe J."


def test_abstract_meta__404_is_not_found() -> None:
    with respx.mock:
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(404, json={}))
        assert _client().abstract_meta("2-s2.0-111").outcome == "not_found"


def test_abstract_meta__no_fields_is_absent() -> None:
    body = {"abstracts-retrieval-response": {"coredata": {"eid": "2-s2.0-111"}}}
    with respx.mock:
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(200, json=body))
        assert _client().abstract_meta("2-s2.0-111").outcome == "absent"


@pytest.mark.parametrize(("status", "error"), [(401, AuthError), (403, EntitlementError)])
def test_default__refusal_still_raises(status: int, error: type[Exception]) -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(status, json=_fx("refusal_401.json")))
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(status, json=_fx("refusal_401.json")))
        with pytest.raises(error):
            _client().search_standard_by_dois(["10.1/a"])
        with pytest.raises(error):
            _client().abstract_meta("2-s2.0-111")


@pytest.mark.parametrize("status", [401, 403])
def test_opt_in__refusal_returns_refused_with_status_and_message(status: int) -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(status, json=_fx("refusal_401.json")))
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(status, json=_fx("refusal_401.json")))
        batch = _client().search_standard_by_dois(["10.1/a", "10.1/b"], fallback_opt_in=True)
        meta = _client().abstract_meta("2-s2.0-111", fallback_opt_in=True)
    for result in [*batch.values(), meta]:
        assert result.outcome == "refused"
        assert result.http_status == status
        assert result.message == "Synthetic: view not available to this key"
    assert set(batch) == {"10.1/a", "10.1/b"}


def test_opt_in__continues_after_a_refused_batch() -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(
            side_effect=[
                httpx.Response(401, json=_fx("refusal_401.json")),
                httpx.Response(200, json=_fx("search_standard.json")),
            ]
        )
        out = _client().search_standard_by_dois(
            ["10.1/x", "10.1000/a"], batch_size=1, fallback_opt_in=True
        )
    assert out["10.1/x"].outcome == "refused"
    assert out["10.1000/a"].outcome == "value"


def test_acquisition__stays_complete_and_never_reaches_cascade_steps() -> None:
    page = {"search-results": {"opensearch:totalResults": "0", "entry": []}}
    with respx.mock:
        route = respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=page))
        list(_client().search('TITLE-ABS-KEY("x")'))
    assert [r.request.url.params["view"] for r in route.calls] == ["COMPLETE"]

    # A refused COMPLETE search raises and issues no second, lesser-view request.
    with respx.mock:
        route = respx.get(_SEARCH).mock(
            return_value=httpx.Response(401, json=_fx("refusal_401.json"))
        )
        with pytest.raises(AuthError):
            list(_client().search('TITLE-ABS-KEY("x")'))
    assert [r.request.url.params["view"] for r in route.calls] == ["COMPLETE"]


def test_acquisition__capture_code_does_not_reference_cascade_steps() -> None:
    capture = Path(__file__).parents[3] / "src" / "prismabib" / "capture"
    for path in capture.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "search_standard_by_dois" not in text, path
        assert "abstract_meta" not in text, path


_REFUSAL_TEXT = "Synthetic: view not available to this key"


@pytest.mark.parametrize(("status", "name"), [(401, "AuthError"), (403, "EntitlementError")])
def test_default__raises_public_class_that_pickles_and_copies(status: int, name: str) -> None:
    with respx.mock:
        respx.get(_ABSTRACT).mock(return_value=httpx.Response(status, json=_fx("refusal_401.json")))
        with pytest.raises((AuthError, EntitlementError)) as info:
            _client().abstract_meta("2-s2.0-111")
    exc = info.value
    assert type(exc).__name__ == name  # what cli.py prints
    for clone in (pickle.loads(pickle.dumps(exc)), copy.copy(exc), copy.deepcopy(exc)):
        assert type(clone) is type(exc)
        assert str(clone) == str(exc)
        assert clone.http_status == status  # type: ignore[attr-defined]
        assert clone.response_message == _REFUSAL_TEXT  # type: ignore[attr-defined]


def test_search_standard__batch_size_zero_raises() -> None:
    with pytest.raises(ValidationError):
        _client().search_standard_by_dois(["10.1/a"], batch_size=0)


def test_search_standard__200_service_error_body_raises() -> None:
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=_fx("refusal_401.json")))
        with pytest.raises(ValidationError, match="service-error"):
            _client().search_standard_by_dois(["10.1/a"])


def test_search_standard__total_exceeds_entries_raises() -> None:
    page = _fx("search_standard.json")
    page["search-results"]["opensearch:totalResults"] = "30"  # more than one page of matches
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=page))
        with pytest.raises(ValidationError, match="30"):
            _client().search_standard_by_dois(["10.1000/a"])


def test_search_standard__duplicate_doi_entries_raise() -> None:
    page = _fx("search_standard.json")
    page["search-results"]["entry"][1]["prism:doi"] = "10.1000/a"
    with respx.mock:
        respx.get(_SEARCH).mock(return_value=httpx.Response(200, json=page))
        with pytest.raises(ValidationError, match="two entries"):
            _client().search_standard_by_dois(["10.1000/a"])


@pytest.mark.parametrize("bad", ["SCOPUS_ID:111", "111", "scopus:SCOPUS_ID:111", "2-s2.0-"])
def test_abstract_meta__non_eid_raises_before_any_request(bad: str) -> None:
    with respx.mock:
        route = respx.get(url__startswith=ScopusClient.ABSTRACT_URL_PREFIX).mock(
            return_value=httpx.Response(404, json={})
        )
        with pytest.raises(ValidationError, match="EID"):
            _client().abstract_meta(bad)
    assert route.call_count == 0
