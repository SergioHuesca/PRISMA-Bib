"""The download cap shared by the Unpaywall and Crossref TDM ``fetch_bytes`` (audit finding 5)."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import UpstreamError
from prismabib.sources import crossref, unpaywall
from prismabib.sources.crossref import CrossrefTdmClient
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.unpaywall import UnpaywallClient

_URL = "https://host.example.org/paper.pdf?token=SECRET"
_LIMIT = 1024
_CHUNK = 256

_CLIENTS = [
    pytest.param(UnpaywallClient, unpaywall, id="unpaywall"),
    pytest.param(CrossrefTdmClient, crossref, id="crossref"),
]


def _make(
    cls: type[UnpaywallClient | CrossrefTdmClient],
) -> UnpaywallClient | CrossrefTdmClient:
    settings = Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        unpaywall_email="reviewer@example.org",
    )
    return cls(settings, rate_limiter=RateLimiter(rate=1000.0))


def _lazy_body(total: int, served: list[int]) -> Iterator[bytes]:
    for _ in range(total // _CHUNK):
        served[0] += _CHUNK
        yield b"x" * _CHUNK


@pytest.mark.integration
@pytest.mark.parametrize(("cls", "module"), _CLIENTS)
def test_fetch_bytes__body_over_limit__rejected_and_stream_not_drained(
    cls: Callable[..., UnpaywallClient | CrossrefTdmClient],
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_DOWNLOAD_BYTES", _LIMIT)
    served = [0]
    with respx.mock:
        respx.get(_URL).mock(
            return_value=httpx.Response(200, content=_lazy_body(_LIMIT * 100, served))
        )
        with pytest.raises(UpstreamError, match="download limit") as excinfo:
            _make(cls).fetch_bytes(_URL)  # type: ignore[arg-type]

    assert _LIMIT < served[0] <= _LIMIT + 2 * _CHUNK
    assert "SECRET" not in str(excinfo.value)


@pytest.mark.integration
@pytest.mark.parametrize(("cls", "module"), _CLIENTS)
def test_fetch_bytes__content_length_over_limit__rejected_before_reading(
    cls: Callable[..., UnpaywallClient | CrossrefTdmClient],
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_DOWNLOAD_BYTES", _LIMIT)
    served = [0]
    with respx.mock:
        respx.get(_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"content-length": str(_LIMIT + 1)},
                content=_lazy_body(_LIMIT * 10, served),
            )
        )
        with pytest.raises(UpstreamError, match="download limit"):
            _make(cls).fetch_bytes(_URL)  # type: ignore[arg-type]

    assert served[0] == 0


@pytest.mark.integration
@pytest.mark.parametrize(("cls", "module"), _CLIENTS)
def test_fetch_bytes__body_exactly_at_limit__accepted(
    cls: Callable[..., UnpaywallClient | CrossrefTdmClient],
    module: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "MAX_DOWNLOAD_BYTES", _LIMIT)
    body = b"%PDF" + b"y" * (_LIMIT - 4)
    with respx.mock:
        respx.get(_URL).mock(return_value=httpx.Response(200, content=body))
        content, _ = _make(cls).fetch_bytes(_URL)  # type: ignore[arg-type]

    assert content == body


@pytest.mark.integration
@pytest.mark.parametrize(("cls", "module"), _CLIENTS)
def test_fetch_bytes__small_pdf__bytes_and_content_type_unchanged(
    cls: Callable[..., UnpaywallClient | CrossrefTdmClient],
    module: object,
) -> None:
    with respx.mock:
        respx.get(_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4\n%%EOF"
            )
        )
        content, content_type = _make(cls).fetch_bytes(_URL)  # type: ignore[arg-type]

    assert content == b"%PDF-1.4\n%%EOF"
    assert content_type == "application/pdf"
