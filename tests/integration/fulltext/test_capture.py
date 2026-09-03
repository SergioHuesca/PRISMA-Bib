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

from prismabib.errors import UpstreamError
from prismabib.fulltext.capture import (
    ATTEMPTS_FILENAME,
    RUNS_DIRNAME,
    already_resolved_record_ids,
    capture_fulltext,
    sealed_fulltext_run_dirs,
)
from prismabib.project import Project
from tests.unit.fulltext.test_resolve import _asset, _StubResolver

_RECORD_A = "scopus:2-s2.0-85100000101"
_RECORD_B = "scopus:2-s2.0-85100000102"


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
    """
    from prismabib.errors import EntitlementError

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
        doi_by_record_id={_RECORD_A: None, _RECORD_B: None},
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
