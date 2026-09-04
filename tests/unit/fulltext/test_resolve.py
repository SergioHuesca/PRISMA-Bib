"""Unit tests for the Stage 6 resolver chain (BUILD_PLAN Tests table, ADR 0019).

Every resolver here is a stub conforming structurally to
:class:`~prismabib.fulltext.resolve.FullTextResolver` -- a test double at
the resolver-Protocol boundary, not a monkeypatch of any ``prismabib.*``
internal (§3.7.3 rule 1: injecting a double at a declared seam is exactly
what a ``Protocol`` is for).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from prismabib.config import FullTextSettings
from prismabib.errors import EntitlementError, UpstreamError
from prismabib.fulltext.resolve import (
    FullTextAsset,
    FullTextResolutionError,
    _refusal_entitled,
    manual_drop_path,
    resolve_fulltext,
)


@dataclass
class _StubResolver:
    """A :class:`~prismabib.fulltext.resolve.FullTextResolver` test double.

    Records every ``record_id`` it was called with, in order -- the call
    count these tests assert on -- and either returns a fixed outcome or
    raises the exception given by ``raises``, simulating an upstream failure
    without any network involved.
    """

    name: str
    outcome: FullTextAsset | None = None
    raises: BaseException | None = None
    calls: list[str] = field(default_factory=list)

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        del doi
        self.calls.append(record_id)
        if self.raises is not None:
            raise self.raises
        return self.outcome


def _asset(resolver_name: str, record_id: str) -> FullTextAsset:
    return FullTextAsset(
        record_id=record_id,
        resolver_name=resolver_name,
        media_type="xml",
        content=f"<xml>{resolver_name}</xml>".encode(),
        retrieved_at=datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
    )


@pytest.mark.unit
def test_chain__first_resolver_hits__later_resolvers_not_called() -> None:
    record_id = "scopus:2-s2.0-85100000001"
    first = _StubResolver(name="r1", outcome=_asset("r1", record_id))
    second = _StubResolver(name="r2", outcome=_asset("r2", record_id))
    third = _StubResolver(name="r3", outcome=_asset("r3", record_id))

    asset, attempts = resolve_fulltext(
        record_id=record_id, doi="10.1016/x", resolvers=[first, second, third]
    )

    assert asset is not None
    assert asset.resolver_name == "r1"
    assert first.calls == [record_id]
    assert second.calls == []
    assert third.calls == []
    assert [attempt.resolver_name for attempt in attempts] == ["r1"]
    assert attempts[0].entitled is True


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC1")
@pytest.mark.parametrize("succeeding_position", [1, 2, 3])
def test_chain__each_resolver_fails_in_turn__next_is_tried(succeeding_position: int) -> None:
    """Every resolver before ``succeeding_position`` fails (returns ``None``).

    Parameterised over which position finally succeeds -- 1, 2, and 3 are
    three genuinely different chain shapes (nothing fails; one fails; two
    fail), not the same assertion restated with a different label.
    """
    record_id = "scopus:2-s2.0-85100000002"
    resolvers = [
        _StubResolver(
            name=f"r{position}",
            outcome=_asset(f"r{position}", record_id) if position == succeeding_position else None,
        )
        for position in (1, 2, 3)
    ]

    asset, attempts = resolve_fulltext(record_id=record_id, doi="10.1109/y", resolvers=resolvers)

    assert asset is not None
    assert asset.resolver_name == f"r{succeeding_position}"

    for position, resolver in enumerate(resolvers, start=1):
        expected_calls = [record_id] if position <= succeeding_position else []
        assert resolver.calls == expected_calls

    assert [attempt.resolver_name for attempt in attempts] == [
        f"r{position}" for position in range(1, succeeding_position + 1)
    ]
    assert [attempt.entitled for attempt in attempts[:-1]] == [None] * (succeeding_position - 1)
    assert attempts[-1].entitled is True


@pytest.mark.unit
def test_chain__empty_resolver_list__returns_none_and_no_attempts() -> None:
    """The degenerate zero-resolver case: exhaustion with nothing to exhaust."""
    asset, attempts = resolve_fulltext(
        record_id="scopus:2-s2.0-85100000003", doi=None, resolvers=[]
    )

    assert asset is None
    assert attempts == ()


@pytest.mark.unit
def test_chain__entitlement_refusal_then_success__records_refusal_and_resolves() -> None:
    """A refusal from resolver 1 does not stop resolver 2 from succeeding."""
    record_id = "scopus:2-s2.0-85100000004"
    refused = _StubResolver(name="sciencedirect", raises=EntitlementError("no entitlement"))
    resolved = _StubResolver(name="manual", outcome=_asset("manual", record_id))

    asset, attempts = resolve_fulltext(
        record_id=record_id, doi="10.1016/z", resolvers=[refused, resolved]
    )

    assert asset is not None
    assert asset.resolver_name == "manual"
    by_resolver = {attempt.resolver_name: attempt for attempt in attempts}
    assert by_resolver["sciencedirect"].entitled is False
    assert by_resolver["sciencedirect"].content is None
    assert by_resolver["manual"].entitled is True


@pytest.mark.unit
@pytest.mark.parametrize(
    ("doi", "expected"),
    [
        pytest.param("10.1016/j.example.2026.100001", False, id="elsevier-genuine-gap"),
        pytest.param("10.1109/tpami.2026.100001", None, id="ieee-never-held-by-sciencedirect"),
        pytest.param(None, None, id="no-doi-cannot-substantiate"),
        pytest.param("10.9999/unmapped.example", None, id="unmapped-prefix-cannot-substantiate"),
    ],
)
def test_refusal_entitled__constrained_resolver__attributes_only_its_own_publisher(
    doi: str | None, expected: bool | None
) -> None:
    """ADR 0021 Decisions 1 and 2, on the function that decides it.

    Asserted here rather than through `resolve_fulltext` because that is no
    longer where the decision is made. Layer 0 records the raw refusal; Layer 1
    derives what it counts against (Decision 1b), which is what lets a rebuild
    repair runs sealed under the old unconditional rule.

    A ScienceDirect 403 for an IEEE DOI is not an entitlement question about
    IEEE -- ScienceDirect never held it and IEEE was never asked. An
    unidentifiable publisher records `None` for the asymmetric reason
    Decision 2 gives: over-reporting is an active false statement in a methods
    section, under-reporting only an omission.
    """
    assert _refusal_entitled(resolver_name="sciencedirect", doi=doi) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "resolver_name",
    [
        pytest.param("crossref_tdm", id="crossref-tdm"),
        pytest.param("openaccess", id="open-access"),
        pytest.param("manual", id="manual-drop"),
    ],
)
def test_refusal_entitled__unconstrained_resolver__always_a_genuine_gap(
    resolver_name: str,
) -> None:
    """An unconstrained resolver's refusal is an entitlement question about any record.

    A Crossref text-mining link, an open-access location and a local file can
    each belong to any publisher, so ADR 0019's original unconditional rule
    still holds for these three -- including for a record whose publisher
    cannot be identified at all.
    """
    assert _refusal_entitled(resolver_name=resolver_name, doi=None) is False
    assert _refusal_entitled(resolver_name=resolver_name, doi="10.1109/x") is False


@pytest.mark.unit
def test_resolve_fulltext__refusal__records_the_raw_fact_in_layer_0() -> None:
    """Layer 0 records "this resolver was refused", never the interpretation.

    An IEEE record refused by ScienceDirect is `entitled=False` *here* --
    deliberately, because that is the fact that happened. Deriving at capture
    time was the first attempt and repaired nothing: the corpus that exposed
    the defect already held its refusals in sealed runs.
    """
    refused = _StubResolver(name="sciencedirect", raises=EntitlementError("no entitlement"))

    _asset_unused, attempts = resolve_fulltext(
        record_id="scopus:2-s2.0-85100000021",
        doi="10.1109/tpami.2026.100001",
        resolvers=[refused],
    )

    assert attempts[0].entitled is False


@pytest.mark.unit
def test_chain__crossref_tdm_refusal__unconstrained__always_records_false() -> None:
    """ADR 0021 Decision 1's unconstrained row: a Crossref TDM 403 records ``entitled=False``
    regardless of the record's publisher, because a TDM link is a publisher-declared link for
    *this* record -- unlike ScienceDirect, which can only ever be Elsevier's own API.
    """
    record_id = "scopus:2-s2.0-85100000024"
    refused = _StubResolver(name="crossref_tdm", raises=EntitlementError("no entitlement"))

    _asset_unused, attempts = resolve_fulltext(
        record_id=record_id, doi="10.1109/tpami.2026.100001", resolvers=[refused]
    )

    assert attempts[0].resolver_name == "crossref_tdm"
    assert attempts[0].entitled is False


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC2")
def test_chain__mid_chain_upstream_failure__raises_carrying_prior_attempts() -> None:
    """A non-entitlement failure from resolver 2 does not discard resolver 1's refusal.

    The regression this pins: before ``resolve_fulltext`` caught anything
    beyond ``EntitlementError``, an ``UpstreamError`` from resolver 2 here
    propagated bare, and the ``sciencedirect`` refusal collected just before
    it was lost with the exception -- along with silently aborting whatever
    called this function next, for every other record in the run.
    """
    record_id = "scopus:2-s2.0-85100000005"
    refused = _StubResolver(name="sciencedirect", raises=EntitlementError("no entitlement"))
    broken = _StubResolver(name="openaccess", raises=UpstreamError("HTTP 503"))
    never_reached = _StubResolver(name="manual", outcome=_asset("manual", record_id))

    with pytest.raises(FullTextResolutionError) as excinfo:
        resolve_fulltext(
            record_id=record_id, doi="10.1016/z", resolvers=[refused, broken, never_reached]
        )

    error = excinfo.value
    assert error.record_id == record_id
    assert error.resolver_name == "openaccess"
    assert [attempt.resolver_name for attempt in error.attempts] == ["sciencedirect"]
    assert error.attempts[0].entitled is False
    assert never_reached.calls == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "transport_error",
    [
        pytest.param(httpx.ConnectError("connection refused"), id="connect-error"),
        pytest.param(httpx.TimeoutException("timed out"), id="timeout-exception"),
    ],
)
def test_chain__httpx_transport_failure__raises_full_text_resolution_error(
    transport_error: httpx.TransportError,
) -> None:
    """A connection failure (never mapped to a prismabib exception by any client) is also caught.

    ``UnpaywallClient``/``ScienceDirectClient`` never translate
    ``httpx.ConnectError``/``httpx.TimeoutException`` into a
    :class:`~prismabib.errors.PrismabibError` -- they propagate bare from
    ``httpx``. This is the second half of the catch clause
    (``PrismabibError, httpx.TransportError``) that keeps a network blip from
    escaping :func:`resolve_fulltext` uncaught. Parameterised over both named
    subclasses, since neither is a subclass of the other.
    """
    record_id = "scopus:2-s2.0-85100000006"
    broken = _StubResolver(name="sciencedirect", raises=transport_error)

    with pytest.raises(FullTextResolutionError) as excinfo:
        resolve_fulltext(record_id=record_id, doi="10.1016/z", resolvers=[broken])

    assert excinfo.value.attempts == ()
    assert excinfo.value.__cause__ is transport_error


@pytest.mark.unit
def test_manual_drop_path__record_id_with_colon__sanitises_for_windows() -> None:
    """The BLOCKING regression this pins: NTFS forbids ``:`` in a filename.

    Every record id on-disk today is namespaced ``scopus:<eid>``, and the
    working copy of this repository is itself on an NTFS mount -- a literal
    ``fulltext/manual/scopus:2-s2.0-....pdf`` path would fail to create at
    all on that filesystem, not merely look unusual.
    """
    path = manual_drop_path(Path("/tmp/project/fulltext"), "scopus:2-s2.0-85100000001")

    assert ":" not in path.name
    assert path.name == "scopus_2-s2.0-85100000001.pdf"
    # Recoverable by eye: the sanitised name still unambiguously names the
    # original record id.
    assert path.name.replace("_", ":", 1) == "scopus:2-s2.0-85100000001.pdf"


@pytest.mark.unit
def test_manual_drop_path__record_id_with_no_special_characters__is_unchanged() -> None:
    """The negative case, so the sanitisation above means something."""
    path = manual_drop_path(Path("/tmp/project/fulltext"), "plainrecordid123")

    assert path.name == "plainrecordid123.pdf"


@pytest.mark.unit
def test_fulltext_settings__no_credentials_in_the_environment__constructs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-text resolution must not require a Scopus key it never uses.

    `Settings` requires `SCOPUS_API_KEY` unconditionally, and `default_chain`
    used it -- so `prismabib fulltext` failed outright for a reviewer holding
    PDFs in `fulltext/manual/` and no Scopus subscription. The failure was
    invisible to every developer, because this repository's working copy has a
    `.env` that supplies the key; it appeared only in CI, where there is none.
    Reproduced locally by moving `.env` aside: two CLI tests failed exactly as
    CI reported.

    `FullTextSettings` declares no required secret, because every resolver it
    configures is individually optional and the chain degrades to the manual
    drop when both are absent.
    """
    for name in ("SCOPUS_API_KEY", "SCOPUS_INSTTOKEN", "ELSEVIER_SD_API_KEY", "UNPAYWALL_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    # `_env_file=None` so the repository's own `.env` cannot supply what CI
    # will not have -- the very asymmetry that hid this defect.
    settings = FullTextSettings(_env_file=None)  # type: ignore[call-arg]

    assert settings.elsevier_sd_api_key is None
    assert settings.unpaywall_email is None
