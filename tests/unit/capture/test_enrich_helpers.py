"""Unit tests for the pure helpers of ``src/prismabib/capture/enrich.py``.

Every function here is total -- given any input it returns rather than raises --
and each of those "rather than raises" is a decision worth pinning separately
from the run-level tests in ``tests/integration/capture/test_enrich.py``.

The recurring reason is the same one throughout Layer 0: a response prismabib
cannot read is still a response Scopus sent, and the payload has already been
persisted verbatim by the time these helpers see it. Crashing on an unexpected
shape would abandon a run mid-corpus over a field the run does not need in
order to *store* the response -- and the shape a later, better reader wants is
already safely on disk. So an unreadable subject-areas block becomes
``"no_subject_areas"``, an unreadable ``progress.json`` simply stops being a
resume candidate, and neither takes the run down.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.capture.enrich import (
    ABSTRACT_VIEW,
    PROGRESS_FILENAME,
    _find_resumable_run,
    _has_subject_areas,
    _record_ids_from_layer0,
    _records_digest,
    _subject_area_entries,
)
from prismabib.capture.layout import RUN_MANIFEST_FILENAME
from prismabib.capture.manifest import RunManifest

# ---------------------------------------------------------------------------
# subject-area extraction
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("response", "expected"),
    [
        pytest.param({}, [], id="no-envelope"),
        pytest.param({"abstracts-retrieval-response": "not-a-mapping"}, [], id="envelope-scalar"),
        pytest.param({"abstracts-retrieval-response": {}}, [], id="no-subject-areas-key"),
        pytest.param(
            {"abstracts-retrieval-response": {"subject-areas": None}}, [], id="subject-areas-null"
        ),
        pytest.param(
            {"abstracts-retrieval-response": {"subject-areas": {}}}, [], id="no-subject-area-key"
        ),
        pytest.param(
            {"abstracts-retrieval-response": {"subject-areas": {"subject-area": "x"}}},
            [],
            id="subject-area-scalar-string",
        ),
        pytest.param(
            {
                "abstracts-retrieval-response": {
                    "subject-areas": {"subject-area": {"@code": "1702"}}
                }
            },
            [{"@code": "1702"}],
            id="lone-mapping-becomes-a-one-element-list",
        ),
        pytest.param(
            {
                "abstracts-retrieval-response": {
                    "subject-areas": {"subject-area": [{"@code": "1702"}, {"@code": "2205"}]}
                }
            },
            [{"@code": "1702"}, {"@code": "2205"}],
            id="list-passes-through",
        ),
    ],
)
def test_enrich__subject_area_entries__normalises_every_shape_without_raising(
    response: dict[str, Any], expected: list[Any]
) -> None:
    assert _subject_area_entries(response) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        pytest.param([], False, id="none-at-all"),
        pytest.param([{"$": "Artificial Intelligence"}], False, id="label-but-no-code"),
        pytest.param([{"@code": ""}], False, id="empty-code"),
        pytest.param(["not-a-mapping"], False, id="non-mapping-entry"),
        pytest.param([{"@code": "1702"}], True, id="coded"),
        pytest.param([{"$": "x"}, {"@code": "1702"}], True, id="one-of-several-coded"),
    ],
)
def test_enrich__has_subject_areas__requires_a_usable_code(
    entries: list[Any], expected: bool
) -> None:
    """A label without a code is not evidence the record has codes.

    ``criteria.yaml``'s ``subject_areas`` list is matched against ``@code``.
    Counting a code-less entry as "has subject areas" would mark the record
    evaluable and then match nothing -- which reads downstream as a deliberate
    exclusion rather than as missing data.
    """
    response = {"abstracts-retrieval-response": {"subject-areas": {"subject-area": entries}}}

    assert _has_subject_areas(response) is expected


# ---------------------------------------------------------------------------
# the resume key
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_enrich__records_digest__depends_on_the_exact_record_set() -> None:
    """The digest is what refuses to resume a run whose record set has changed.

    If it collided across different sets, a resumed run would keep writing into
    payload files laid out for the *previous* corpus, at offsets that no longer
    mean anything -- and would still seal.
    """
    base = ["scopus:a", "scopus:b"]

    assert _records_digest(base) == _records_digest(["scopus:a", "scopus:b"])
    assert _records_digest(base) != _records_digest(["scopus:a", "scopus:c"])
    assert _records_digest(base) != _records_digest(["scopus:a"])
    # Not a plain concatenation: "ab" and "a","b" must not collide.
    assert _records_digest(["scopus:ab"]) != _records_digest(["scopus:a", "scopus:b"])


# ---------------------------------------------------------------------------
# resolving the record set from Layer 0
# ---------------------------------------------------------------------------


def _seal_search_run(raw_dir: Path, run_id: str, pages: dict[str, list[dict[str, Any]]]) -> None:
    """Write a minimal sealed search run: page files plus a real ``RunManifest``."""
    run_dir = raw_dir / run_id
    run_dir.mkdir(parents=True)
    for filename, entries in pages.items():
        run_dir.joinpath(filename).write_text(
            "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
        )
    manifest = RunManifest(
        run_id=run_id,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        finished_at=datetime(2026, 1, 1, tzinfo=UTC),
        endpoint="https://api.elsevier.com/content/search/scopus",
        query="q",
        view="COMPLETE",
        total_results=sum(len(entries) for entries in pages.values()),
        pages_fetched=len(pages),
        payload_files=list(pages),
        payload_sha256="0" * 64,
        client_version="0.0.0",
        criteria_version="1.0.0",
    )
    (run_dir / RUN_MANIFEST_FILENAME).write_text(
        manifest.model_dump_json(indent=2), encoding="utf-8"
    )


@pytest.mark.unit
def test_enrich__record_ids_from_layer0__missing_raw_dir__returns_nothing(tmp_path: Path) -> None:
    assert _record_ids_from_layer0(tmp_path / "absent") == ([], [])


@pytest.mark.unit
def test_enrich__record_ids_from_layer0__unsealed_run__is_ignored(tmp_path: Path) -> None:
    """An interrupted capture is not a corpus.

    Enriching it would spend quota on a record set that changes the moment the
    capture is resumed -- and the resumed run would then not match the digest,
    so the enrichment run could never be resumed either.
    """
    raw_dir = tmp_path / "raw"
    (raw_dir / "20260101T000000Z-unsealed").mkdir(parents=True)
    (raw_dir / "20260101T000000Z-unsealed" / "page-0000.jsonl").write_text(
        json.dumps({"eid": "2-s2.0-1"}) + "\n", encoding="utf-8"
    )

    assert _record_ids_from_layer0(raw_dir) == ([], [])


@pytest.mark.unit
def test_enrich__record_ids_from_layer0__page_named_by_the_seal_but_absent__is_skipped(
    tmp_path: Path,
) -> None:
    """A manifest naming a file that is not there must not take the run down.

    The seal is the authority on which files belong to the run; a missing one
    is a damaged capture. Refusing to enrich the other 1,799 records because of
    it trades a partial result for none.
    """
    raw_dir = tmp_path / "raw"
    _seal_search_run(
        raw_dir, "20260101T000000Z-aaaaaaaa", {"page-0000.jsonl": [{"eid": "2-s2.0-1"}]}
    )
    (raw_dir / "20260101T000000Z-aaaaaaaa" / "page-0001.jsonl").write_text("", encoding="utf-8")
    manifest_path = raw_dir / "20260101T000000Z-aaaaaaaa" / RUN_MANIFEST_FILENAME
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["payload_files"] = ["page-0000.jsonl", "page-0002.jsonl"]
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    assert _record_ids_from_layer0(raw_dir) == (
        ["20260101T000000Z-aaaaaaaa"],
        ["scopus:2-s2.0-1"],
    )


@pytest.mark.unit
def test_enrich__record_ids_from_layer0__blank_lines_and_entries_without_an_eid__are_skipped(
    tmp_path: Path,
) -> None:
    """Scopus's empty-result-set placeholder carries an ``error`` key, not an ``eid``.

    It is not a record and cannot be keyed, so it must not become a request --
    ``_record_id_from_entry`` makes the same call on the Layer 1 side.
    """
    raw_dir = tmp_path / "raw"
    _seal_search_run(
        raw_dir,
        "20260101T000000Z-aaaaaaaa",
        {
            "page-0000.jsonl": [
                {"eid": "2-s2.0-1"},
                {"error": "Result set was empty"},
                {"eid": ""},
                {"eid": 12345},
            ]
        },
    )
    page = raw_dir / "20260101T000000Z-aaaaaaaa" / "page-0000.jsonl"
    page.write_text(page.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")

    assert _record_ids_from_layer0(raw_dir) == (
        ["20260101T000000Z-aaaaaaaa"],
        ["scopus:2-s2.0-1"],
    )


@pytest.mark.unit
def test_enrich__record_ids_from_layer0__record_in_two_runs__is_requested_once(
    tmp_path: Path,
) -> None:
    """One paper is one Abstract Retrieval call, however many searches matched it."""
    raw_dir = tmp_path / "raw"
    _seal_search_run(
        raw_dir,
        "20260101T000000Z-bbbbbbbb",
        {"page-0000.jsonl": [{"eid": "2-s2.0-2"}, {"eid": "2-s2.0-1"}]},
    )
    _seal_search_run(
        raw_dir,
        "20260101T000000Z-aaaaaaaa",
        {"page-0000.jsonl": [{"eid": "2-s2.0-1"}]},
    )

    run_ids, record_ids = _record_ids_from_layer0(raw_dir)

    assert run_ids == ["20260101T000000Z-aaaaaaaa", "20260101T000000Z-bbbbbbbb"]
    assert record_ids == ["scopus:2-s2.0-1", "scopus:2-s2.0-2"]


# ---------------------------------------------------------------------------
# finding a resumable abstract run
# ---------------------------------------------------------------------------


_ENDPOINT = "https://api.elsevier.com/content/abstract/scopus_id/{scopus_id}"


def _progress_json(digest: str) -> str:
    return json.dumps(
        {
            "endpoint": _ENDPOINT,
            "view": ABSTRACT_VIEW,
            "started_at": "2026-01-01T00:00:00Z",
            "source_run_ids": [],
            "records_digest": digest,
            "records_requested": 3,
            "records_done": 0,
            "records_fetched": 0,
            "payload_files": [],
            "unavailable": [],
        }
    )


def _find(root: Path, digest: str = "d") -> Path | None:
    return _find_resumable_run(root, endpoint=_ENDPOINT, view=ABSTRACT_VIEW, records_digest=digest)


@pytest.mark.unit
def test_enrich__find_resumable_run__missing_root__is_not_an_error(tmp_path: Path) -> None:
    assert _find(tmp_path / "abstracts") is None


@pytest.mark.unit
def test_enrich__find_resumable_run__loose_file_beside_the_runs__is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "abstracts"
    root.mkdir()
    (root / "README.txt").write_text("not a run", encoding="utf-8")

    assert _find(root) is None


@pytest.mark.unit
def test_enrich__find_resumable_run__sealed_run__is_never_resumed(tmp_path: Path) -> None:
    """Sealing is final. Resuming a sealed run is the one thing §2.2 forbids outright."""
    root = tmp_path / "abstracts"
    run_dir = root / "20260101T000000Z-aaaaaaaa"
    run_dir.mkdir(parents=True)
    (run_dir / PROGRESS_FILENAME).write_text(_progress_json("d"), encoding="utf-8")
    (run_dir / RUN_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    assert _find(root) is None


@pytest.mark.unit
def test_enrich__find_resumable_run__no_sidecar__is_not_a_candidate(tmp_path: Path) -> None:
    root = tmp_path / "abstracts"
    (root / "20260101T000000Z-aaaaaaaa").mkdir(parents=True)

    assert _find(root) is None


@pytest.mark.unit
def test_enrich__find_resumable_run__corrupt_sidecar__is_skipped_and_left_alone(
    tmp_path: Path,
) -> None:
    """A truncated ``progress.json`` costs a fresh run, never a deletion.

    This module never truncates or deletes a run's contents -- the same rule
    ``capture/writer.py`` follows -- because the payload files beside a corrupt
    sidecar are real captured data that nothing else can reproduce without
    spending quota again.
    """
    root = tmp_path / "abstracts"
    run_dir = root / "20260101T000000Z-aaaaaaaa"
    run_dir.mkdir(parents=True)
    (run_dir / PROGRESS_FILENAME).write_text('{"endpoint": "trunc', encoding="utf-8")

    assert _find(root) is None
    assert (run_dir / PROGRESS_FILENAME).is_file()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("endpoint", "https://example.invalid/{scopus_id}", id="different-endpoint"),
        pytest.param("view", "META", id="different-view"),
        pytest.param("records_digest", "other", id="different-record-set"),
    ],
)
def test_enrich__find_resumable_run__any_field_differs__is_not_resumed(
    tmp_path: Path, field: str, value: str
) -> None:
    root = tmp_path / "abstracts"
    run_dir = root / "20260101T000000Z-aaaaaaaa"
    run_dir.mkdir(parents=True)
    state = json.loads(_progress_json("d"))
    state[field] = value
    (run_dir / PROGRESS_FILENAME).write_text(json.dumps(state), encoding="utf-8")

    assert _find(root) is None


@pytest.mark.unit
def test_enrich__find_resumable_run__several_matches__picks_the_most_recent(
    tmp_path: Path,
) -> None:
    """Run ids sort chronologically by construction, so ``max`` is "most recent"."""
    root = tmp_path / "abstracts"
    for run_id in ("20260101T000000Z-aaaaaaaa", "20260303T000000Z-cccccccc"):
        run_dir = root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / PROGRESS_FILENAME).write_text(_progress_json("d"), encoding="utf-8")

    found = _find(root)

    assert found is not None
    assert found.name == "20260303T000000Z-cccccccc"
