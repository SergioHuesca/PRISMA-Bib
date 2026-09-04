"""Integration tests for the Stage 6 Layer 0 writer (ADR 0019 Decision 0).

Real filesystem, stub resolvers (no network) -- exercises
:func:`prismabib.fulltext.capture.capture_fulltext` and
:func:`~prismabib.fulltext.capture.already_resolved_record_ids` end to end:
sealing, resumption, content-addressed assets, and the mid-chain-failure
persistence :mod:`prismabib.fulltext.resolve` hands up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.errors import EntitlementError, UpstreamError
from prismabib.fulltext.capture import (
    ATTEMPTS_FILENAME,
    CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT,
    RUNS_DIRNAME,
    already_resolved_record_ids,
    capture_fulltext,
    sealed_fulltext_run_dirs,
)
from prismabib.project import Project
from tests.unit.fulltext.test_resolve import _asset, _StubResolver

_RECORD_A = "scopus:2-s2.0-85100000101"
_RECORD_B = "scopus:2-s2.0-85100000102"


def _record_ids(count: int, *, prefix: str = "scopus:2-s2.0-8520000") -> list[str]:
    """``count`` record ids that sort in the order generated -- lowest index first."""
    return [f"{prefix}{index:04d}" for index in range(count)]


def _elsevier_dois(record_ids: list[str]) -> dict[str, str | None]:
    """An Elsevier DOI per record id, so a ScienceDirect refusal is genuinely ``entitled=False``
    (ADR 0021 Decision 1) -- these breaker tests are about the *count*, not the attribution,
    so the DOI is fixed to keep only one thing varying.
    """
    return {record_id: f"10.1016/j.example.{index}" for index, record_id in enumerate(record_ids)}


class _AlwaysRefusesScienceDirect:
    """A ``FullTextResolver`` stub that refuses every record unconditionally."""

    name = "sciencedirect"

    def resolve(self, *, record_id: str, doi: str | None) -> object:
        del record_id, doi
        raise EntitlementError("no entitlement")


class _ResolvesOneThenRefuses:
    """Resolves exactly one record id and refuses every other one it is asked about."""

    name = "sciencedirect"

    def __init__(self, resolved_record_id: str) -> None:
        self._resolved_record_id = resolved_record_id

    def resolve(self, *, record_id: str, doi: str | None) -> object:
        del doi
        if record_id == self._resolved_record_id:
            return _asset("sciencedirect", record_id)
        raise EntitlementError("no entitlement")


def _project(tmp_path: Path, slug: str = "capture-demo") -> Project:
    return Project.init(slug, title="Capture Demo", root=tmp_path)


@pytest.mark.integration
def test_capture__resolved_record__seals_a_run_with_a_content_addressed_asset(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    resolver = _StubResolver(name="manual", outcome=_asset("manual", _RECORD_A))

    outcome = capture_fulltext(
        project,
        pending_ids=[_RECORD_A],
        doi_by_record_id={_RECORD_A: "10.1016/x"},
        resolvers=[resolver],
    )

    assert outcome.sealed is True
    assert outcome.attempted == 1
    assert outcome.resolved == 1
    assert outcome.resolved_by_resolver == {"manual": 1}

    (run_dir,) = sealed_fulltext_run_dirs(project.fulltext_dir)
    assert (run_dir / "manifest.json").is_file()

    lines = (run_dir / ATTEMPTS_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["record_id"] == _RECORD_A
    assert row["entitled"] is True
    assert row["asset_file"] is not None

    asset_path = run_dir / row["asset_file"]
    assert asset_path.is_file()
    assert asset_path.read_bytes() == _asset("manual", _RECORD_A).content
    # Content-addressed: the filename is the content's own SHA-256 digest.
    import hashlib

    digest = hashlib.sha256(asset_path.read_bytes()).hexdigest()
    assert digest in asset_path.name


@pytest.mark.integration
def test_capture__already_resolved__is_excluded_from_a_later_call(tmp_path: Path) -> None:
    project = _project(tmp_path)
    resolver = _StubResolver(name="manual", outcome=_asset("manual", _RECORD_A))
    capture_fulltext(
        project,
        pending_ids=[_RECORD_A],
        doi_by_record_id={_RECORD_A: None},
        resolvers=[resolver],
    )

    resolved = already_resolved_record_ids(project.fulltext_dir)

    assert resolved == {_RECORD_A}


@pytest.mark.integration
def test_capture__budget_of_one__leaves_the_run_unsealed_and_resumes(tmp_path: Path) -> None:
    project = _project(tmp_path)

    # A resolver whose outcome depends on which record it is asked about, so one
    # instance can serve both calls below correctly regardless of which record
    # `capture_fulltext` attempts first.
    class _PerRecordResolver:
        name = "manual"

        def resolve(self, *, record_id: str, doi: str | None) -> object:
            del doi
            return _asset("manual", record_id)

    first = capture_fulltext(
        project,
        pending_ids=[_RECORD_A, _RECORD_B],
        doi_by_record_id={_RECORD_A: None, _RECORD_B: None},
        resolvers=[_PerRecordResolver()],
        budget=1,
    )
    assert first.sealed is False
    assert first.attempted == 1
    assert first.resolved == 1

    (run_dir,) = [entry for entry in (project.fulltext_dir / "runs").iterdir() if entry.is_dir()]
    assert not (run_dir / "manifest.json").is_file()

    second = capture_fulltext(
        project,
        pending_ids=[_RECORD_A, _RECORD_B],
        doi_by_record_id={_RECORD_A: None, _RECORD_B: None},
        resolvers=[_PerRecordResolver()],
        budget=1,
    )
    assert second.sealed is True
    assert second.attempted == 1
    assert (run_dir / "manifest.json").is_file()

    resolved = already_resolved_record_ids(project.fulltext_dir)
    assert resolved == {_RECORD_A, _RECORD_B}


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_capture__mid_chain_failure__persists_prior_attempts_and_continues_to_next_record(
    tmp_path: Path,
) -> None:
    """The BLOCKING fix: a resolver failure on one record must not abort the whole run.

    Record A: resolver 1 refuses (entitled=False), resolver 2 raises
    ``UpstreamError`` -- the whole chain for A aborts, but the refusal is
    still durably recorded and record B is still attempted.

    Both records carry an Elsevier DOI, so the resolver named
    ``"sciencedirect"`` refusing them is a genuine entitlement gap
    (ADR 0021 Decision 1) and ``entitled=False`` is the correct recording --
    this test is about mid-chain-failure persistence, not about publisher
    attribution, so the DOI is chosen to keep that the only thing that
    varies.
    """
    project = _project(tmp_path)

    class _ChainByRecord:
        """Two resolvers whose second one only breaks for `_RECORD_A`."""

        def __init__(self) -> None:
            self.refuser = _StubResolver(name="sciencedirect", raises=EntitlementError("refused"))
            self.breaker_calls: list[str] = []

        def resolvers(self) -> list[object]:
            outer = self

            class _Breaker:
                name = "openaccess"

                def resolve(self, *, record_id: str, doi: str | None) -> object:
                    del doi
                    outer.breaker_calls.append(record_id)
                    if record_id == _RECORD_A:
                        raise UpstreamError("HTTP 503")
                    return _asset("openaccess", record_id)

            return [outer.refuser, _Breaker()]

    chain = _ChainByRecord()

    outcome = capture_fulltext(
        project,
        pending_ids=[_RECORD_A, _RECORD_B],
        doi_by_record_id={
            _RECORD_A: "10.1016/j.example.2026.100101",
            _RECORD_B: "10.1016/j.example.2026.100102",
        },
        resolvers=chain.resolvers(),
    )

    # Both records were attempted -- record A's failure did not stop record B.
    # `sciencedirect` refuses every record (both A and B reach it before the
    # break), so it is 2, not 1 -- the number that would look suspiciously
    # tidy if this fixture only ever refused the one record that also failed.
    assert outcome.attempted == 2
    assert outcome.failed_record_ids == (_RECORD_A,)
    assert outcome.resolved == 1
    assert outcome.resolved_by_resolver == {"openaccess": 1}
    assert outcome.refused_by_resolver == {"sciencedirect": 2}
    assert chain.breaker_calls == [_RECORD_A, _RECORD_B]

    # And what was learned about record A before the failure -- the
    # ScienceDirect refusal -- is durably on disk, not discarded.
    (run_dir,) = sealed_fulltext_run_dirs(project.fulltext_dir)
    rows = [
        json.loads(line)
        for line in (run_dir / ATTEMPTS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    record_a_rows = [row for row in rows if row["record_id"] == _RECORD_A]
    assert len(record_a_rows) == 1
    assert record_a_rows[0]["resolver_name"] == "sciencedirect"
    assert record_a_rows[0]["entitled"] is False

    record_b_rows = [row for row in rows if row["record_id"] == _RECORD_B]
    assert any(row["entitled"] is True for row in record_b_rows)


@pytest.mark.integration
def test_capture__consecutive_refusals_reach_the_limit__raises_and_leaves_the_run_unsealed(
    tmp_path: Path,
) -> None:
    """ADR 0021 Decision 4: an unentitled resolver is detected, not run to exhaustion.

    `CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT` consecutive refusals from one
    resolver that has resolved nothing trips the breaker. The triggering
    record's attempt is not durably written and the run is not sealed, so a
    later, differently-credentialed call resumes at that same record.
    """
    project = _project(tmp_path)
    record_ids = _record_ids(CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT + 2)
    doi_by_record_id = _elsevier_dois(record_ids)

    with pytest.raises(EntitlementError, match="sciencedirect"):
        capture_fulltext(
            project,
            pending_ids=record_ids,
            doi_by_record_id=doi_by_record_id,
            resolvers=[_AlwaysRefusesScienceDirect()],
        )

    run_dirs = [
        entry for entry in (project.fulltext_dir / RUNS_DIRNAME).iterdir() if entry.is_dir()
    ]
    (run_dir,) = run_dirs
    assert not (run_dir / "manifest.json").is_file(), "the run must not seal on a tripped breaker"

    rows = [
        json.loads(line)
        for line in (run_dir / ATTEMPTS_FILENAME).read_text(encoding="utf-8").splitlines()
    ]
    # The record that tripped the breaker is discarded, not merely
    # unsealed: only the LIMIT-1 records processed *before* it are on disk.
    assert len(rows) == CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT - 1
    assert all(row["entitled"] is False for row in rows)


@pytest.mark.integration
def test_capture__a_single_refusal__does_not_trip_the_breaker(tmp_path: Path) -> None:
    """The negative case a threshold of one would fail: one embargoed record is not a symptom."""
    project = _project(tmp_path)

    outcome = capture_fulltext(
        project,
        pending_ids=[_RECORD_A],
        doi_by_record_id={_RECORD_A: "10.1016/j.example.0"},
        resolvers=[_AlwaysRefusesScienceDirect()],
    )

    assert outcome.sealed is True
    assert outcome.refused_by_resolver == {"sciencedirect": 1}


@pytest.mark.integration
def test_capture__resolver_has_resolved_something__breaker_never_trips(tmp_path: Path) -> None:
    """A resolver that has resolved even one record cannot be the unentitled-key symptom."""
    project = _project(tmp_path)
    record_ids = _record_ids(CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT + 5)
    doi_by_record_id = _elsevier_dois(record_ids)
    resolver = _ResolvesOneThenRefuses(resolved_record_id=record_ids[0])

    outcome = capture_fulltext(
        project,
        pending_ids=record_ids,
        doi_by_record_id=doi_by_record_id,
        resolvers=[resolver],
    )

    assert outcome.sealed is True
    assert outcome.resolved_by_resolver == {"sciencedirect": 1}
    assert outcome.refused_by_resolver == {"sciencedirect": len(record_ids) - 1}


@pytest.mark.integration
def test_capture__resumed_run_with_prior_work__does_not_re_arm_the_breaker(tmp_path: Path) -> None:
    """ADR 0021 Decision 4's own resumption guard.

    The first call resolves one record and stops (budget-bounded); the
    second call resumes the *same* run and refuses more than
    `CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT` records in a row from the same
    resolver. It must not trip: the guard is `state.resolved_by_resolver`,
    which is persisted in `progress.json` and survives the resume -- not a
    same-call-only counter that would forget the first call's success.
    """
    project = _project(tmp_path)
    record_ids = _record_ids(CONSECUTIVE_ENTITLEMENT_REFUSAL_LIMIT + 4)
    doi_by_record_id = _elsevier_dois(record_ids)
    # `capture_fulltext` sorts `pending_ids`, so `record_ids[0]` (already the
    # lexicographically smallest, by construction) is what a `budget=1` first
    # call actually attempts.
    resolver = _ResolvesOneThenRefuses(resolved_record_id=record_ids[0])

    first = capture_fulltext(
        project,
        pending_ids=record_ids,
        doi_by_record_id=doi_by_record_id,
        resolvers=[resolver],
        budget=1,
    )
    assert first.sealed is False
    assert first.resolved_by_resolver == {"sciencedirect": 1}

    second = capture_fulltext(
        project,
        pending_ids=record_ids,
        doi_by_record_id=doi_by_record_id,
        resolvers=[resolver],
    )

    assert second.sealed is True
    assert second.refused_by_resolver == {"sciencedirect": len(record_ids) - 1}


def _write_run(fulltext_dir: Path, run_id: str, record_id: str, *, sealed: bool) -> None:
    """Hand-write one Layer 0 full-text run, sealed or not."""
    run_dir = fulltext_dir / RUNS_DIRNAME / run_id
    run_dir.mkdir(parents=True)
    (run_dir / ATTEMPTS_FILENAME).write_text(
        json.dumps(
            {
                "record_id": record_id,
                "resolver_name": "manual",
                "media_type": "pdf",
                "asset_file": "assets/x.pdf",
                "retrieved_at": "2026-01-01T00:00:00+00:00",
                "entitled": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if sealed:
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("sealed_ids", "unsealed_ids", "expected_default", "expected_including_unsealed"),
    [
        pytest.param([], [], set(), set(), id="nothing-run"),
        pytest.param([], ["b"], set(), {"b"}, id="unsealed-only"),
        pytest.param(["a"], [], {"a"}, {"a"}, id="sealed-only"),
        pytest.param(["a"], ["b"], {"a"}, {"a", "b"}, id="sealed-and-unsealed"),
    ],
)
def test_already_resolved__include_unsealed__changes_only_the_unsealed_runs(
    tmp_path: Path,
    sealed_ids: list[str],
    unsealed_ids: list[str],
    expected_default: set[str],
    expected_including_unsealed: set[str],
) -> None:
    """The default stays sealed-only; the flag adds in-progress runs and nothing else.

    The default is what protects quota discipline -- resumption must never
    treat an unsealed run's work as a committed fact. The flag exists for a
    different question ("what must a reviewer still fetch by hand?"), where an
    unsealed run's assets are as real on disk as a sealed run's.

    Both the helper that sweeps every run directory and the branch that selects
    it were reachable only through an untested script; a covered ternary line
    proved nothing about the branch never being taken.
    """
    fulltext_dir = tmp_path / "fulltext"
    for index, record_id in enumerate(sealed_ids):
        _write_run(fulltext_dir, f"20260101T00000{index}Z-aaaaaaaa", record_id, sealed=True)
    for index, record_id in enumerate(unsealed_ids):
        _write_run(fulltext_dir, f"20260202T00000{index}Z-bbbbbbbb", record_id, sealed=False)

    assert already_resolved_record_ids(fulltext_dir) == expected_default
    assert (
        already_resolved_record_ids(fulltext_dir, include_unsealed=True)
        == expected_including_unsealed
    )


@pytest.mark.integration
def test_already_resolved__run_directory_without_attempts__is_skipped(tmp_path: Path) -> None:
    """A run directory mid-creation, or emptied, must not break the sweep.

    `include_unsealed=True` reads directories the sealed-only path never
    touched, including one a run has just created and not yet written to.
    """
    fulltext_dir = tmp_path / "fulltext"
    (fulltext_dir / RUNS_DIRNAME / "20260303T000000Z-cccccccc").mkdir(parents=True)
    _write_run(fulltext_dir, "20260304T000000Z-dddddddd", "a", sealed=False)

    assert already_resolved_record_ids(fulltext_dir, include_unsealed=True) == {"a"}
