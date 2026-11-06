"""Hermetic proof of the Scopus live test's CI-only 401 skip rule (issue #302)."""

from __future__ import annotations

import pytest

from prismabib.errors import AuthError, UpstreamError
from tests.scopus_live_helpers import skip_on_ci_auth_error


def test_skip_rule__401_on_github_actions__skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with (
        pytest.raises(pytest.skip.Exception, match=r"401.*dev\.elsevier\.com"),
        skip_on_ci_auth_error(),
    ):
        raise AuthError("Scopus rejected the API key")


def test_skip_rule__401_outside_github_actions__fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(AuthError), skip_on_ci_auth_error():
        raise AuthError("Scopus rejected the API key")


def test_skip_rule__non_401_on_github_actions__fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(UpstreamError), skip_on_ci_auth_error():
        raise UpstreamError("Scopus answered 503")
