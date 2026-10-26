"""Integration tests for the registration writer and the loader fold (ADR 0029, 0032, 0033).

Real filesystem and DuckDB, no network. Layer 0 OpenAlex runs are hand-built from the synthetic
``tests/fixtures/openalex/work_registration.json``. Every refusal test asserts the log's bytes and
checksum are unchanged (ADR 0029 section 5, "a refused attempt writes nothing").
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.capture import registrations as reg
from prismabib.capture.candidates import CandidateEvent, CandidateLog, CandidateRow
from prismabib.capture.registrations import (
    RegisterEvent,
    RegistrationRefusal,
    ReverseEvent,
    SupersedeEvent,
    load_registration_events,
    matching_works,
    register_candidate,
    registration_log,
    reverse_registration,
)
from prismabib.errors import LogError, StoreError
from prismabib.openalex_map import MAP_SHA256, MAP_VERSION, ClassificationRefusal
from prismabib.project import Project
from prismabib.store import load as load_module
from prismabib.store.checksums import table_checksum, table_checksums
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.store_helpers import make_entry, write_sealed_openalex_run, write_sealed_run

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "openalex" / "work_registration.json"
SELECT = (
    "id,doi,title,publication_year,abstract_inverted_index,authorships,topics,"
    "type,primary_location,primary_topic"
)
SEARCH_RUN = "20250101T000000Z-11111111"
OA_RUN = "20260101T000000Z-aaaaaaaa"
PAYLOAD = f"openalex/{OA_RUN}/response-0000.jsonl"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PASTED = datetime(2026, 10, 22, tzinfo=UTC)
DOI_ONE = "10.9999/synthetic.reg.one"
TITLE_ONE = "A Synthetic Registered Article"
EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret
)


class _Ids:
    """Seeded, ULID-shaped, strictly increasing ids (no wall clock, no randomness)."""

    def __init__(self) -> None:
        self._n = 0

    def __call__(self) -> str:
        self._n += 1
        return f"01TESTREG{self._n:017d}"


@dataclass
class Env:
    project: Project
    candidates: CandidateLog
    ids: _Ids = field(default_factory=_Ids)

    def candidate(self, title: str, year: int, doi: str | None = None) -> str:
        event = CandidateEvent.from_row(
            CandidateRow(title=title, year=year, doi=doi), reviewer="sh", pasted_at=PASTED
        )
        self.candidates.add(event)
        return event.candidate_id

    def register(self, candidate_id: str, **overrides: Any) -> RegisterEvent:
        kwargs: dict[str, Any] = {
            "candidate_id": candidate_id,
            "reviewer": "sh",
            "payload_file": PAYLOAD,
            "match_rule": "doi",
            "now": lambda: NOW,
            "new_id": self.ids,
        }
        kwargs.update(overrides)
        return register_candidate(self.project, self.candidates, **kwargs)

    def reverse(self, target: str, **overrides: Any) -> ReverseEvent:
        kwargs: dict[str, Any] = {
            "reverses": target,
            "reviewer": "sh",
            "reason": "wrong_doi",
            "now": lambda: NOW,
            "new_id": self.ids,
        }
        kwargs.update(overrides)
        return reverse_registration(self.project, **kwargs)

    def write_run(
        self, works: list[dict[str, Any]] | None = None, *, api_view: str = SELECT
    ) -> None:
        works = _works() if works is None else works
        dois = [w["doi"].removeprefix("https://doi.org/").lower() for w in works if w.get("doi")]
        write_sealed_openalex_run(
            self.project.raw_dir,
            OA_RUN,
            [{"lookup": "doi_batch", "identifiers": dois, "works": works}],
            api_view=api_view,
        )

    def snapshot(self) -> tuple[bytes | None, bytes | None]:
        log = registration_log(self.project.registrations_path)
        log_bytes = log.path.read_bytes() if log.path.exists() else None
        sidecar = log.checksum_path.read_bytes() if log.checksum_path.exists() else None
        return log_bytes, sidecar

    def build(self, *, rebuild: bool = True) -> None:
        build_store(self.project, rebuild=rebuild)

    def rows(self, sql: str, *params: Any) -> list[tuple[Any, ...]]:
        connection = connect(self.project, read_only=True)
        try:
            return connection.execute(sql, list(params)).fetchall()
        finally:
            connection.close()

    def registered_ids(self) -> list[str]:
        return [r[0] for r in self.rows("SELECT record_id FROM registered_records ORDER BY 1")]

    def scopus_run(self, run_id: str, *entries: dict[str, Any]) -> None:
        write_sealed_run(
            self.project.raw_dir, run_id, list(entries), started_at=datetime(2025, 1, 1, tzinfo=UTC)
        )


def _works() -> list[dict[str, Any]]:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["results"])


def _scopus(eid: str, title: str, cover: str, doi: str | None) -> dict[str, Any]:
    return make_entry(eid=eid, title=title, cover_date=cover, doi=doi)


@pytest.fixture
def env(tmp_path: Path) -> Env:
    project = Project.init("reg", title="Registrations", root=tmp_path)
    e = Env(project, CandidateLog(project.root / "decisions" / "candidates.jsonl"))
    e.scopus_run(SEARCH_RUN, _scopus("2-s2.0-800000000001", "Unrelated Study", "2018-05-01", None))
    e.write_run()
    return e


def _refused(env: Env, exc: type[Exception], match: str, call: Any) -> None:
    """Run ``call``; it must raise ``exc`` matching ``match`` and leave the log untouched."""
    before = env.snapshot()
    with pytest.raises(exc, match=match):
        call()
    assert env.snapshot() == before


# -- the ADR-named refusals --------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("name", ["", "   ", "\t \n"])
def test_registration__whitespace_venue_name__is_refused(env: Env, name: str) -> None:
    first = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    works = _works()
    works[1]["primary_location"]["source"]["display_name"] = name
    env.write_run(works)  # the same run id: rewrite before anything reads it
    cid = env.candidate("A Synthetic Conference Paper", 2020, "10.9999/synthetic.reg.two")

    _refused(
        env, RegistrationRefusal, "venue_name", lambda: env.register(cid, payload_file=PAYLOAD)
    )
    assert [e.event_id for e in load_registration_events(env.project.registrations_path)] == [
        first.event_id
    ]


@pytest.mark.integration
def test_registration__missing_venue_name__is_refused(env: Env) -> None:
    works = _works()
    del works[0]["primary_location"]["source"]["display_name"]
    env.write_run(works)
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "venue_name", lambda: env.register(cid))
    assert env.snapshot() == (None, None)  # nothing was ever written


@pytest.mark.integration
def test_reversal__reason_outside_closed_set__is_refused(env: Env) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    _refused(
        env,
        RegistrationRefusal,
        "reversal reason 'mistake' is not one of wrong_doi, wrong_title, duplicate_of_scopus",
        lambda: env.reverse(event.event_id, reason="mistake"),
    )
    assert len(load_registration_events(env.project.registrations_path)) == 1


# -- writer: success ---------------------------------------------------------------------------


@pytest.mark.integration
def test_register_candidate__doi_match__writes_one_complete_event(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    event = env.register(cid)

    (line,) = env.project.registrations_path.read_text(encoding="utf-8").splitlines()
    dumped = json.loads(line)
    assert dumped == {
        "event_id": event.event_id,
        "schema_version": 1,
        "ts": "2026-10-23T12:00:00.000Z",
        "project": "reg",
        "reviewer": "sh",
        "action": "register",
        "record_id": f"registered:{event.event_id}",
        "candidate_id": cid,
        "match_rule": "doi",
        "source_level": 4,
        "source_payload_ref": {
            "file": PAYLOAD,
            "line": 0,
            "openalex_id": "https://openalex.org/W2000000001",
        },
        "source_payload_sha256": hashlib.sha256(
            (env.project.raw_dir / PAYLOAD).read_bytes()
        ).hexdigest(),
        "doi": DOI_ONE,
        "subject_map_version": MAP_VERSION,
        "subject_map_sha256": MAP_SHA256,
    }
    sidecar = registration_log(env.project.registrations_path).checksum_path.read_text()
    assert (
        sidecar.split()[0]
        == hashlib.sha256(env.project.registrations_path.read_bytes()).hexdigest()
    )


@pytest.mark.integration
def test_register_candidate__title_year_without_doi__registers(env: Env) -> None:
    cid = env.candidate("A Synthetic Registered Article!", 2021)  # punctuation folds away

    event = env.register(cid, match_rule="title_year")

    assert event.match_rule == "title_year"
    assert event.doi == DOI_ONE


@pytest.mark.integration
def test_register_candidate__near_match_with_confirmation__registers(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2022)  # same title, different year

    event = env.register(cid, match_rule="near_confirmed", confirmed_near=True)

    assert event.match_rule == "near_confirmed"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("title", "year", "doi", "rule", "resolves"),
    [
        ("Anything", 2021, DOI_ONE, "doi", True),
        ("Anything", 2021, "10.9999/absent", "doi", False),
        (TITLE_ONE, 2021, None, "title_year", True),
        (TITLE_ONE, 2021, DOI_ONE, "title_year", False),  # a DOI'd candidate never title-matches
        (TITLE_ONE, 2022, None, "title_year", False),
        (TITLE_ONE, 2022, None, "near_confirmed", True),
        (TITLE_ONE, 2021, None, "near_confirmed", False),  # exact is not near
    ],
)
def test_matching_works__same_result_as_writer(
    env: Env, title: str, year: int, doi: str | None, rule: str, resolves: bool
) -> None:
    cid = env.candidate(title, year, doi)
    view = next(v for v in env.candidates.view() if v.event.candidate_id == cid)
    works = reg.read_response(env.project.raw_dir, PAYLOAD).works

    matched = matching_works(view, rule, works)

    assert (len(matched) == 1) is resolves
    if resolves:
        event = env.register(cid, match_rule=rule, confirmed_near=(rule == "near_confirmed"))
        assert event.doi == reg.classify_work(matched[0]).doi
    else:
        with pytest.raises(RegistrationRefusal, match="unresolved"):
            env.register(cid, match_rule=rule, confirmed_near=(rule == "near_confirmed"))


@pytest.mark.integration
def test_register_candidate__registration_after_reversal__gets_a_new_record_id(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    first = env.register(cid)
    env.reverse(first.event_id)

    second = env.register(cid)

    assert second.record_id != first.record_id
    assert second.record_id == f"registered:{second.event_id}"


# -- writer: refusals (each asserts the log is unchanged) --------------------------------------


@pytest.mark.integration
def test_register_candidate__level_one_to_three__is_refused_as_a_scopus_duplicate(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    for level in (1, 2, 3):
        _refused(
            env,
            RegistrationRefusal,
            "already a Scopus record",
            lambda level=level: env.register(cid, source_level=level),
        )


@pytest.mark.integration
def test_register_candidate__level_five_or_other__is_refused(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    for level in (5, 0, 6):
        _refused(
            env,
            RegistrationRefusal,
            f"source level {level} is not registrable",
            lambda level=level: env.register(cid, source_level=level),
        )


@pytest.mark.integration
def test_register_candidate__blank_reviewer__is_refused(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "named reviewer", lambda: env.register(cid, reviewer="  "))


@pytest.mark.integration
def test_register_candidate__unknown_candidate__is_refused(env: Env) -> None:
    _refused(env, RegistrationRefusal, "unknown candidate", lambda: env.register("0" * 32))


@pytest.mark.integration
def test_register_candidate__work_without_doi__is_refused(env: Env) -> None:
    works = _works()
    works[0]["doi"] = None
    env.write_run(works)
    cid = env.candidate(TITLE_ONE, 2021)

    _refused(env, RegistrationRefusal, "no DOI", lambda: env.register(cid, match_rule="title_year"))


@pytest.mark.integration
def test_register_candidate__no_work_satisfies_the_rule__is_refused(env: Env) -> None:
    cid = env.candidate("A Title Nobody Has", 2021, "10.9999/not.there")

    _refused(env, RegistrationRefusal, "0 works", lambda: env.register(cid))


@pytest.mark.integration
def test_register_candidate__two_works_satisfy_the_rule__is_refused(env: Env) -> None:
    works = _works()
    twin = copy.deepcopy(works[0])
    twin["id"] = "https://openalex.org/W2999999999"
    works.append(twin)
    env.write_run(works)
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "2 works", lambda: env.register(cid))


@pytest.mark.integration
def test_register_candidate__near_match_without_confirmation__is_refused(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2022)

    _refused(
        env,
        RegistrationRefusal,
        "needs the reviewer's confirmation",
        lambda: env.register(cid, match_rule="near_confirmed"),
    )


@pytest.mark.integration
def test_register_candidate__not_one_of_the_two_near_rules__is_refused(env: Env) -> None:
    cid = env.candidate("Synthetic Registered", 2019)  # prefix of the title, but a different year

    _refused(
        env,
        RegistrationRefusal,
        "0 works",
        lambda: env.register(cid, match_rule="near_confirmed", confirmed_near=True),
    )


@pytest.mark.integration
def test_register_candidate__title_year_rule_for_a_candidate_with_a_doi__is_refused(
    env: Env,
) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(
        env, RegistrationRefusal, "0 works", lambda: env.register(cid, match_rule="title_year")
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (
            lambda w: w.update(type="preprint"),
            "type 'preprint' is not in classification map version 1",
        ),
        (
            lambda w: w["primary_location"]["source"].update(type="podcast"),
            "source type 'podcast' is not in classification map version 1",
        ),
        (
            lambda w: w["primary_topic"]["subfield"].update(id="https://openalex.org/subfields/9"),
            "subfield 'https://openalex.org/subfields/9' is not in classification map version 1",
        ),
    ],
)
def test_register_candidate__value_outside_the_map__is_refused_naming_input_and_value(
    env: Env, edit: Any, message: str
) -> None:
    works = _works()
    edit(works[0])
    env.write_run(works)
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, ClassificationRefusal, message, lambda: env.register(cid))


@pytest.mark.integration
def test_register_candidate__body_captured_before_the_select_amendment__is_refused(
    env: Env,
) -> None:
    env.write_run(
        api_view="id,doi,title,publication_year,abstract_inverted_index,authorships,topics"
    )
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "before the select amendment", lambda: env.register(cid))


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad", ["openalex/../x/response-0000.jsonl", "page-0000.jsonl", f"openalex/{OA_RUN}/nope.jsonl"]
)
def test_register_candidate__unusable_payload_reference__is_refused(env: Env, bad: str) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "", lambda: env.register(cid, payload_file=bad))


@pytest.mark.integration
def test_register_candidate__second_registration_for_one_candidate__is_refused(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    env.register(cid)

    _refused(
        env, RegistrationRefusal, "already has an active registration", lambda: env.register(cid)
    )


@pytest.mark.integration
def test_register_candidate__same_doi_under_another_candidate__is_refused(env: Env) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    other = env.candidate(TITLE_ONE, 2022)

    _refused(
        env,
        RegistrationRefusal,
        "already has an active registration",
        lambda: env.register(other, match_rule="near_confirmed", confirmed_near=True),
    )


@pytest.mark.integration
def test_register_candidate__scopus_record_with_the_same_doi__is_refused(env: Env) -> None:
    env.scopus_run(
        "20250102T000000Z-22222222", _scopus("2-s2.0-800000000002", "Other", "2020-01-01", DOI_ONE)
    )
    env.build()
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(
        env,
        RegistrationRefusal,
        r"already a Scopus record \(scopus:2-s2.0-800000000002\)",
        lambda: env.register(cid),
    )


@pytest.mark.integration
def test_register_candidate__scopus_record_with_the_same_title_and_year__is_refused(
    env: Env,
) -> None:
    env.scopus_run(
        "20250102T000000Z-22222222",
        _scopus("2-s2.0-800000000002", TITLE_ONE + "?", "2021-02-01", None),
    )
    env.build()
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(env, RegistrationRefusal, "already a Scopus record", lambda: env.register(cid))


# -- reversal ----------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "reason", ["wrong_doi", "wrong_title", "duplicate_of_scopus", "not_eligible"]
)
def test_reverse_registration__each_closed_reason__is_accepted(env: Env, reason: str) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    reversal = env.reverse(event.record_id, reason=reason)  # the record id is accepted too

    assert (reversal.reason, reversal.reverses) == (reason, event.event_id)


@pytest.mark.integration
def test_reverse_registration__other_needs_a_note(env: Env) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    _refused(
        env,
        RegistrationRefusal,
        "requires a non-empty note",
        lambda: env.reverse(event.event_id, reason="other", note="  "),
    )
    assert env.reverse(event.event_id, reason="other", note="because").note == "because"


@pytest.mark.integration
def test_reverse_registration__unknown_or_already_removed__is_refused(env: Env) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    _refused(env, RegistrationRefusal, "not an active registration", lambda: env.reverse("NOPE"))
    env.reverse(event.event_id)

    _refused(
        env, RegistrationRefusal, "not an active registration", lambda: env.reverse(event.event_id)
    )


@pytest.mark.integration
def test_reverse_registration__blank_reviewer__is_refused(env: Env) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    _refused(
        env,
        RegistrationRefusal,
        "named reviewer",
        lambda: env.reverse(event.event_id, reviewer=" "),
    )


# -- no refusal leaves a partial line; Layer 0 untouched -----------------------------------------


@pytest.mark.integration
def test_refusals__a_pile_of_them__leave_a_complete_checksummed_log(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    event = env.register(cid)
    attempts = [
        lambda: env.register(cid),
        lambda: env.register(cid, reviewer=""),
        lambda: env.register(cid, source_level=2),
        lambda: env.reverse(event.event_id, reason="nope"),
        lambda: env.reverse("NOPE"),
    ]
    for attempt in attempts:
        with pytest.raises((RegistrationRefusal, LogError)):
            attempt()

    raw = env.project.registrations_path.read_bytes()
    assert raw.endswith(b"\n")
    assert len(raw.splitlines()) == 1
    assert len(registration_log(env.project.registrations_path).load()) == 1  # also verifies sha


def _tree_digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.mark.integration
def test_register_and_reverse__layer_zero__is_byte_identical(env: Env) -> None:
    before = _tree_digest(env.project.raw_dir)

    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.reverse(event.event_id)

    assert _tree_digest(env.project.raw_dir) == before


@pytest.mark.integration
def test_registration_log__hand_edit__raises_log_error_on_next_build(env: Env) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    path = env.project.registrations_path
    path.write_bytes(path.read_bytes().replace(b'"sh"', b'"xx"'))

    with pytest.raises(LogError, match="checksum mismatch"):
        env.build()
    assert not env.project.db_path.exists()


# -- concurrency -----------------------------------------------------------------------------------

_CHILD = """
import sys, time
from pathlib import Path
from prismabib.capture.candidates import CandidateLog
from prismabib.capture.registrations import register_candidate
from prismabib.errors import PrismabibError
from prismabib.project import Project

root, go, candidate_id, rule, payload = sys.argv[1:6]
project = Project.open("reg", root=Path(root))
candidates = CandidateLog(project.root / "decisions" / "candidates.jsonl")
while not Path(go).exists():
    time.sleep(0.001)
try:
    register_candidate(
        project, candidates, candidate_id=candidate_id, reviewer="sh",
        payload_file=payload, match_rule=rule, confirmed_near=(rule == "near_confirmed"),
    )
except PrismabibError as exc:
    print("refused")
else:
    print("registered")
"""


@pytest.mark.integration
def test_register_candidate__two_processes_racing_one_doi__exactly_one_registers(
    env: Env, tmp_path: Path
) -> None:
    by_doi = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    by_near = env.candidate(TITLE_ONE, 2022)  # a different candidate resolving to the same work
    go = tmp_path / "go"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(tmp_path), str(go), cid, rule, PAYLOAD],
            stdout=subprocess.PIPE,
            text=True,
        )
        for cid, rule in ((by_doi, "doi"), (by_near, "near_confirmed"))
    ]
    go.touch()
    outputs = [child.communicate(timeout=120)[0].strip() for child in children]

    assert (sorted(outputs), [c.returncode for c in children]) == (
        ["refused", "registered"],
        [0, 0],
    )
    events = load_registration_events(env.project.registrations_path)
    assert [type(e) for e in events] == [RegisterEvent]


# -- the loader fold ---------------------------------------------------------------------------------


@pytest.mark.integration
def test_build_store__registration__row_appears_with_every_derived_column(env: Env) -> None:
    works = _works()
    works[0]["primary_location"]["source"]["display_name"] = (
        "  Journal of Synthetic Registrations \n"
    )
    env.write_run(works)
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    env.build()

    (row,) = env.rows("SELECT * FROM registered_records")
    assert row == (
        event.record_id,
        event.event_id,
        DOI_ONE,
        TITLE_ONE,
        "A synthetic abstract",
        2021,
        "article",
        None,
        "journal",
        "Journal of Synthetic Registrations",  # stored stripped (ADR 0032 Decision 5)
        "https://openalex.org/W2000000001",
        PAYLOAD,
        0,
        "doi",
        "sh",
        datetime(2026, 10, 23, 12, 0),  # noqa: DTZ001  (a naive TIMESTAMP column reads back naive)
    )
    assert env.rows("SELECT area_code FROM subject_areas WHERE record_id = ?", event.record_id) == [
        ("1702",)
    ]
    assert env.rows("SELECT origin FROM records_all WHERE record_id = ?", event.record_id) == [
        ("registered",)
    ]


@pytest.mark.integration
def test_build_store__reversal__row_is_gone(env: Env) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.build()
    assert env.registered_ids() == [event.record_id]

    env.reverse(event.event_id)
    env.build()

    assert env.registered_ids() == []
    assert env.rows("SELECT count(*) FROM subject_areas WHERE record_id LIKE 'registered:%'") == [
        (0,)
    ]


@pytest.mark.integration
def test_build_store__reregistration_after_reversal__only_the_new_record_loads(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    first = env.register(cid)
    env.reverse(first.event_id)
    second = env.register(cid)

    env.build()

    assert env.registered_ids() == [second.record_id]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("scopus_title", "scopus_cover", "scopus_doi", "rule"),
    [
        ("Some Other Title", "2020-01-01", DOI_ONE.upper(), "doi"),
        (TITLE_ONE.lower() + "!", "2021-06-01", None, "title_year"),
    ],
)
def test_build_store__later_scopus_match__supersedes_visibly_and_stably(
    env: Env, scopus_title: str, scopus_cover: str, scopus_doi: str | None, rule: str
) -> None:
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.build()
    assert env.registered_ids() == [event.record_id]
    env.scopus_run(
        "20250102T000000Z-22222222",
        _scopus("2-s2.0-800000000002", scopus_title, scopus_cover, scopus_doi),
    )

    env.build()

    assert env.registered_ids() == []
    events = load_registration_events(env.project.registrations_path)
    (superseded,) = [e for e in events if isinstance(e, SupersedeEvent)]
    assert (superseded.reverses, superseded.scopus_record_id, superseded.match_rule) == (
        event.event_id,
        "scopus:2-s2.0-800000000002",
        rule,
    )
    assert superseded.actor == "system"
    # A rebuild is stable: the registration is already inactive, so nothing more is written.
    written = env.snapshot()
    env.build()
    assert env.snapshot() == written
    assert env.registered_ids() == []


@pytest.mark.integration
def test_build_store__no_registration__is_scopus_only_and_creates_no_log(env: Env) -> None:
    env.build()

    assert env.registered_ids() == []
    assert not env.project.registrations_path.exists()
    connection = connect(env.project, read_only=True)
    try:
        assert table_checksum(connection, "registered_records") == EMPTY_SHA256
    finally:
        connection.close()


@pytest.mark.integration
def test_build_store__registrations__rebuild_twice_gives_identical_checksums(env: Env) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.register(env.candidate("A Synthetic Conference Paper", 2020, "10.9999/synthetic.reg.two"))

    def checksums() -> dict[str, str]:
        env.build()
        connection = connect(env.project, read_only=True)
        try:
            return dict(table_checksums(connection))
        finally:
            connection.close()

    first = checksums()
    assert checksums() == first
    assert first["registered_records"] != EMPTY_SHA256


@pytest.mark.integration
def test_build_store__registrations__are_inserted_after_records_and_venues(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    order: list[str] = []
    real = load_module._insert_rows

    def spy(connection: Any, table: str, rows: Any) -> None:
        if rows:
            order.append(table)
        real(connection, table, rows)

    monkeypatch.setattr(load_module, "_insert_rows", spy)

    env.build()

    assert order.index("registered_records") > order.index("records")
    assert order.index("registered_records") > order.index("venues")


def _good_event(env: Env) -> RegisterEvent:
    return env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))


@pytest.mark.integration
@pytest.mark.parametrize("problem", ["missing_body", "old_select", "unmappable", "digest"])
def test_build_store__registration_whose_body_cannot_yield_the_inputs__is_refused(
    env: Env, problem: str
) -> None:
    event = _good_event(env)
    works = _works()
    if problem == "missing_body":
        shutil.rmtree(env.project.raw_dir / "openalex")  # no OpenAlex run left at all
    elif problem == "old_select":
        env.write_run(api_view="id,doi,title")
    elif problem == "unmappable":
        works[0]["type"] = "preprint"
        env.write_run(works)
    else:  # the body changed after registration
        works[0]["title"] = "Edited"
        env.write_run(works)

    with pytest.raises(StoreError, match=event.record_id):
        env.build()
    assert not env.project.db_path.exists()  # a refused build leaves no store


@pytest.mark.integration
def test_build_store__registration_naming_an_unknown_map_version__raises_log_error(
    env: Env,
) -> None:
    _good_event(env)
    path = env.project.registrations_path
    raw = path.read_bytes()
    forged = raw.replace(b'"subject_map_version":"1"', b'"subject_map_version":"99"')
    assert forged != raw
    path.write_bytes(forged)
    path.with_name(path.name + ".sha256").write_text(
        f"{hashlib.sha256(forged).hexdigest()}  {path.name}\n"
    )

    with pytest.raises(LogError, match="classification map version '99'"):
        env.build()
    assert not env.project.db_path.exists()


@pytest.mark.integration
def test_build_store__reverse_naming_an_unknown_event__raises_log_error(env: Env) -> None:
    _good_event(env)
    registration_log(env.project.registrations_path).write_event(
        reg.RegistrationRecord(
            ReverseEvent(
                event_id="01FORGED0000000000000000001",
                ts=NOW,
                project="reg",
                reviewer="sh",
                reverses="NOPE",
                reason="wrong_doi",
            )
        )
    )

    with pytest.raises(LogError, match="not a register event"):
        env.build()


@pytest.mark.integration
def test_reverse_registration__refused_on_a_project_without_a_log__creates_no_file(
    env: Env,
) -> None:
    with pytest.raises(RegistrationRefusal, match="not an active registration"):
        env.reverse("NOPE")
    with pytest.raises(RegistrationRefusal, match="not an active registration"):
        reg.supersede_registration(
            env.project, reverses="NOPE", scopus_record_id="scopus:1", match_rule="doi"
        )

    assert not env.project.registrations_path.exists()
    assert not env.project.registrations_path.with_name("registrations.jsonl.sha256").exists()


@pytest.mark.integration
def test_build_store__failed_build_after_a_pending_supersede__leaves_the_log_untouched(
    env: Env,
) -> None:
    run_two = "20260102T000000Z-bbbbbbbb"
    second_work = _works()[1]
    second_doi = "10.9999/synthetic.reg.two"

    def write_second() -> None:
        write_sealed_openalex_run(
            env.project.raw_dir,
            run_two,
            [{"lookup": "doi_batch", "identifiers": [second_doi], "works": [second_work]}],
            api_view=SELECT,
        )

    write_second()
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))  # will match a Scopus DOI below
    env.register(
        env.candidate("A Synthetic Conference Paper", 2020, second_doi),
        payload_file=f"openalex/{run_two}/response-0000.jsonl",
    )
    env.scopus_run(
        "20250102T000000Z-22222222", _scopus("2-s2.0-800000000002", "X", "2020-01-01", DOI_ONE)
    )
    second_work["title"] = "Edited after registration"  # the body no longer matches its digest
    write_second()
    before = env.snapshot()

    with pytest.raises(StoreError, match="Reversing the registration"):
        env.build()

    assert env.snapshot() == before


@pytest.mark.integration
def test_build_store__store_write_fails_after_a_pending_supersede__log_unchanged_and_no_store(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.scopus_run(
        "20250102T000000Z-22222222", _scopus("2-s2.0-800000000002", "X", "2020-01-01", DOI_ONE)
    )
    before = env.snapshot()

    def boom(*_: Any, **__: Any) -> None:
        raise StoreError("simulated write failure")

    monkeypatch.setattr(load_module, "_write_accumulator", boom)

    with pytest.raises(StoreError, match="simulated"):
        env.build()

    assert env.snapshot() == before  # the supersede is appended only after the store is complete
    assert not env.project.db_path.exists()


@pytest.mark.integration
def test_build_store__scopus_record_id_beginning_registered__is_refused(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(load_module, "_record_id_from_entry", lambda _entry: "registered:X")

    with pytest.raises(StoreError, match="reserved for registered records"):
        env.build()

    assert not env.project.db_path.exists()


@pytest.mark.integration
def test_register_candidate__level_four_with_a_non_openalex_payload__is_refused(env: Env) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    _refused(
        env,
        RegistrationRefusal,
        "is not openalex/",
        lambda: env.register(cid, source_level=4, payload_file=f"{SEARCH_RUN}/page-0001.jsonl"),
    )


@pytest.mark.integration
def test_build_store__registration_reversed_during_the_supersede_step__is_skipped(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    env.scopus_run(
        "20250102T000000Z-22222222", _scopus("2-s2.0-800000000002", "X", "2020-01-01", DOI_ONE)
    )
    real = load_module.supersede_registration

    def reverse_first(project: Project, **kwargs: Any) -> SupersedeEvent:
        env.reverse(kwargs["reverses"])  # a reviewer wins the race
        return real(project, **kwargs)

    monkeypatch.setattr(load_module, "supersede_registration", reverse_first)

    env.build()

    assert env.project.db_path.exists()
    assert env.registered_ids() == []
    kinds = [type(e) for e in load_registration_events(env.project.registrations_path)]
    assert kinds == [RegisterEvent, ReverseEvent]


@pytest.mark.integration
def test_read_response__pre_amendment_select__says_to_recapture(env: Env) -> None:
    env.write_run(api_view="id,doi,title")

    with pytest.raises(RegistrationRefusal, match="Re-capture the OpenAlex lookup"):
        reg.read_response(env.project.raw_dir, PAYLOAD)
