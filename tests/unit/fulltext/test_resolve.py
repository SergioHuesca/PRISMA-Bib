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

import pytest

from prismabib.errors import EntitlementError
from prismabib.fulltext.resolve import FullTextAsset, resolve_fulltext


@dataclass
class _StubResolver:
    """A :class:`~prismabib.fulltext.resolve.FullTextResolver` test double.

    Records every ``record_id`` it was called with, in order -- the call
    count these tests assert on -- and either returns a fixed outcome or
    raises :class:`~prismabib.errors.EntitlementError`, simulating a
    ScienceDirect-style refusal without any network involved.
    """

    name: str
    outcome: FullTextAsset | None = None
    raises: bool = False
    calls: list[str] = field(default_factory=list)

    def resolve(self, *, record_id: str, doi: str | None) -> FullTextAsset | None:
        del doi
        self.calls.append(record_id)
        if self.raises:
            raise EntitlementError(f"{self.name} refused this record")
        return self.outcome


def _asset(resolver_name: str, record_id: str) -> FullTextAsset:
    return FullTextAsset(
        record_id=record_id,
        resolver_name=resolver_name,
        media_type="xml",
        path=Path(f"/tmp/{resolver_name}.xml"),
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
