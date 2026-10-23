"""Reviewer registration of a candidate as a record (ADR 0029, ADR 0032, ADR 0033 Decision 4).

A registration is a Layer 2 event in ``decisions/registrations.jsonl`` written through
:class:`~prismabib.log.AppendOnlyLog`. Three kinds exist: ``register`` (a named reviewer),
``reverse`` (a named reviewer, with a reason from a closed set) and ``supersede`` (written only
by the loader, when a Scopus record now holds the same paper, ADR 0029 section 6).

Every business rule that ADR 0029 section 5 assigns to the writer is enforced here, and the
checks that read the log run inside :meth:`~prismabib.log.AppendOnlyLog.append_if` so the check
and the append share one exclusive lock. :func:`derive_registration` is the single derivation of
the ``registered_records`` columns from the stored OpenAlex body; the writer uses it to refuse and
the loader uses it as its backstop, so the two cannot drift.

This module never imports the screening-decision log class, never writes Layer 0, and never
touches ``records``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Final, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_serializer,
    field_validator,
    model_validator,
)

from prismabib.capture.candidates import (
    CandidateLog,
    CandidateView,
    near_matches,
    normalise_doi,
    normalise_title,
    title_match,
)
from prismabib.capture.layout import OPENALEX_DIRNAME, RUN_MANIFEST_FILENAME, is_sealed
from prismabib.capture.manifest import OpenAlexRequest, OpenAlexRunManifest
from prismabib.errors import LogError, PrismabibError
from prismabib.log import AppendOnlyLog
from prismabib.openalex_map import (
    MAP_SHA256,
    MAP_VERSION,
    ClassificationRefusal,
    primary_topic_to_subject_area,
    source_type_to_venue_type,
    work_type_to_doc_type,
)
from prismabib.prisma.events import IdFactory, MonotonicUlidFactory
from prismabib.project import Project
from prismabib.sources.openalex import parse_work, rebuild_abstract

CURRENT_REGISTRATION_SCHEMA_VERSION: Final = 1
REGISTRATIONS_FILENAME: Final = "registrations.jsonl"
RECORD_ID_PREFIX: Final = "registered:"

MatchRule = Literal["doi", "title_year", "near_confirmed"]
ReversalReason = Literal["wrong_doi", "wrong_title", "duplicate_of_scopus", "not_eligible", "other"]
#: ADR 0033 Decision 4: the closed set. ``other`` requires a non-empty ``note``.
REVERSAL_REASONS: Final[tuple[str, ...]] = (
    "wrong_doi",
    "wrong_title",
    "duplicate_of_scopus",
    "not_eligible",
    "other",
)
#: The select fields a body must have been requested with (ADR 0032 deviation vi).
REQUIRED_SELECT_FIELDS: Final = frozenset({"type", "primary_location", "primary_topic"})


class RegistrationRefusal(PrismabibError):
    """The writer (or the loader backstop) refused a registration; nothing was written."""


# -- events ------------------------------------------------------------------------------------


class PayloadAddress(BaseModel):
    """Where the work sits in Layer 0 (ADR 0031 ``PayloadRef`` form: file, line 0, OpenAlex id).

    ``file`` is POSIX and relative to ``raw/``, so the event is the same on every machine.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    line: Literal[0] = 0
    openalex_id: str


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    schema_version: int = CURRENT_REGISTRATION_SCHEMA_VERSION
    ts: datetime
    project: str

    @field_validator("schema_version")
    @classmethod
    def _current_version(cls, value: int) -> int:
        if value != CURRENT_REGISTRATION_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}")
        return value

    @field_validator("event_id", "project")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("ts")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return value.astimezone(UTC)

    @field_serializer("ts", when_used="json")
    def _serialize_ts(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _Reviewed(_Base):
    """An event a named reviewer wrote."""

    reviewer: str

    @field_validator("reviewer")
    @classmethod
    def _reviewer_named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reviewer must not be blank")
        return value


class RegisterEvent(_Reviewed):
    """A reviewer registers one resolved candidate as a record (ADR 0029 section 2)."""

    action: Literal["register"] = "register"
    record_id: str
    candidate_id: str
    match_rule: MatchRule
    source_level: Literal[4]
    source_payload_ref: PayloadAddress
    source_payload_sha256: str
    doi: str
    subject_map_version: str
    subject_map_sha256: str

    @field_validator("doi")
    @classmethod
    def _doi_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("a registration requires a DOI")
        return value

    @model_validator(mode="after")
    def _record_id_is_the_event_id(self) -> RegisterEvent:
        if self.record_id != RECORD_ID_PREFIX + self.event_id:
            raise ValueError(f"record_id must be exactly {RECORD_ID_PREFIX!r} + event_id")
        return self


class ReverseEvent(_Reviewed):
    """A reviewer removes a registration it names (ADR 0029 section 7)."""

    action: Literal["reverse"] = "reverse"
    reverses: str
    reason: ReversalReason
    note: str | None = None

    @model_validator(mode="after")
    def _other_needs_a_note(self) -> ReverseEvent:
        if self.reason == "other" and not (self.note or "").strip():
            raise ValueError("reason 'other' requires a non-empty note")
        return self


class SupersedeEvent(_Base):
    """The loader removes a registration because a Scopus record now holds the paper (section 6)."""

    action: Literal["supersede"] = "supersede"
    reverses: str
    scopus_record_id: str
    match_rule: Literal["doi", "title_year"]
    actor: Literal["system"] = "system"

    @field_validator("scopus_record_id")
    @classmethod
    def _scopus_prefix(cls, value: str) -> str:
        if not value.startswith("scopus:"):
            raise ValueError("scopus_record_id must begin 'scopus:'")
        return value


_AnyEvent = Annotated[RegisterEvent | ReverseEvent | SupersedeEvent, Field(discriminator="action")]


class RegistrationRecord(RootModel[_AnyEvent]):
    """One line of ``registrations.jsonl``: a ``register``, ``reverse`` or ``supersede`` event."""


def registration_log(path: Path) -> AppendOnlyLog[RegistrationRecord]:
    """The registration log over ``path``, on the shared append-only machinery (ADR 0024)."""
    return AppendOnlyLog(
        path,
        model=RegistrationRecord,
        current_schema_version=CURRENT_REGISTRATION_SCHEMA_VERSION,
        event_id=lambda record: record.root.event_id,
        noun="registration",
        event_description="registration",
        recovery_hint="register_candidate",
        writer_name="the registration writer",
        labour="human registration",
    )


def load_registration_events(path: Path) -> list[RegisterEvent | ReverseEvent | SupersedeEvent]:
    """Every event in ``path`` (empty when the file does not exist; the file is not created)."""
    if not path.is_file():
        return []
    return [record.root for record in registration_log(path).load()]


def active_registrations(
    events: Sequence[RegisterEvent | ReverseEvent | SupersedeEvent],
) -> list[RegisterEvent]:
    """The ``register`` events no ``reverse`` or ``supersede`` names, sorted by ``event_id``.

    Raises:
        LogError: If a ``reverse``/``supersede`` names something that is not a ``register``
            event (a hand edit; the writer cannot produce it).
    """
    registers = {e.event_id: e for e in events if isinstance(e, RegisterEvent)}
    removed: set[str] = set()
    for event in events:
        if isinstance(event, ReverseEvent | SupersedeEvent):
            if event.reverses not in registers:
                raise LogError(
                    f"{event.action} {event.event_id!r} names {event.reverses!r}, "
                    "which is not a register event in the log"
                )
            removed.add(event.reverses)
    return [registers[i] for i in sorted(registers) if i not in removed]


# -- reading the stored body (shared by writer and loader) ---------------------------------------


@dataclass(frozen=True)
class StoredResponse:
    """One sealed OpenAlex response, read back from Layer 0."""

    file: str
    sha256: str
    works: list[dict[str, Any]]


def read_response(raw_dir: Path, file: str) -> StoredResponse:
    """Read ``raw/<file>`` (``openalex/<run>/response-NNNN.jsonl``) and check it can back a record.

    Raises:
        RegistrationRefusal: If the body is missing, is not in a sealed OpenAlex run, was captured
            before the select amendment, is not a 200, or does not match its manifest digest.
    """
    parts = PurePosixPath(file).parts
    if len(parts) != 3 or parts[0] != OPENALEX_DIRNAME or ".." in parts or "\\" in file:
        raise RegistrationRefusal(f"payload file {file!r} is not openalex/<run>/response-N.jsonl")
    run_dir = raw_dir / parts[0] / parts[1]
    path = run_dir / parts[2]
    if not path.is_file():
        raise RegistrationRefusal(f"the referenced OpenAlex body {file!r} is missing from Layer 0")
    if not is_sealed(run_dir):
        raise RegistrationRefusal(f"{file!r} is not in a sealed OpenAlex run")
    try:
        manifest = OpenAlexRunManifest.model_validate_json(
            (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise RegistrationRefusal(f"the manifest of {parts[1]} is unreadable: {exc}") from exc
    request: OpenAlexRequest | None = next(
        (r for r in manifest.requests if r.file == parts[2]), None
    )
    if request is None:
        raise RegistrationRefusal(f"{file!r} is not named by its run's manifest")
    if request.http_status != 200:
        raise RegistrationRefusal(f"{file!r} is an HTTP {request.http_status} response, not a body")
    if not set(request.api_view.split(",")) >= REQUIRED_SELECT_FIELDS:
        raise RegistrationRefusal(
            f"{file!r} was captured before the select amendment: its select {request.api_view!r} "
            f"lacks {sorted(REQUIRED_SELECT_FIELDS - set(request.api_view.split(',')))} "
            "(ADR 0032 Decision 2)"
        )
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != request.sha256:
        raise RegistrationRefusal(f"{file!r} does not match its manifest digest")
    text = data.decode("utf-8").strip()
    try:
        body: Any = json.loads(text) if text else {}
    except (ValueError, RecursionError) as exc:
        raise RegistrationRefusal(f"{file!r} is not valid JSON: {exc}") from exc
    if isinstance(body, dict) and isinstance(body.get("results"), list):
        works = [w for w in body["results"] if isinstance(w, dict)]
    elif isinstance(body, dict) and "id" in body:
        works = [body]
    else:
        works = []
    return StoredResponse(file=file, sha256=digest, works=works)


@dataclass(frozen=True)
class DerivedRegistration:
    """The ``registered_records`` inputs, derived from the stored body (ADR 0032 Decision 2)."""

    openalex_id: str
    doi: str
    title: str
    abstract: str | None
    year: int
    doc_type: str
    venue_type: str
    venue_name: str
    subject_code: str


def classify_work(work: Mapping[str, Any]) -> DerivedRegistration:
    """Derive every input from one raw OpenAlex work; anything the map cannot cover is a refusal.

    Raises:
        ClassificationRefusal: For a ``type``, source type or subfield outside the map (the
            message names the input and value exactly as the map does).
        RegistrationRefusal: For a missing DOI, title, year or venue name.
    """
    parsed = parse_work(work)
    if not parsed.doi:
        raise RegistrationRefusal("the source work has no DOI; a registration requires one")
    if not isinstance(parsed.openalex_id, str) or not parsed.openalex_id:
        raise RegistrationRefusal("the source work has no OpenAlex id")
    if not isinstance(parsed.title, str) or not parsed.title.strip():
        raise RegistrationRefusal("the source work has no title")
    if parsed.publication_year is None:
        raise RegistrationRefusal("the source work has no publication_year")
    # The map refuses any value outside it, a missing type included, naming input and value
    # (`type None is not in classification map version 1`, ADR 0032 Decision 3).
    doc_type = work_type_to_doc_type(cast(str, work.get("type")))
    location = work.get("primary_location")
    source = location.get("source") if isinstance(location, Mapping) else None
    source = source if isinstance(source, Mapping) else {}
    venue_type = source_type_to_venue_type(source.get("type"))
    name = source.get("display_name")
    venue_name = name.strip() if isinstance(name, str) else ""
    if not venue_name:
        raise RegistrationRefusal(
            f"venue_name (primary_location.source.display_name) is {name!r}: missing or empty "
            "after strip() (ADR 0032 Decision 5)"
        )
    topic = work.get("primary_topic")
    subject = primary_topic_to_subject_area(topic if isinstance(topic, Mapping) else None)
    return DerivedRegistration(
        openalex_id=parsed.openalex_id,
        doi=parsed.doi,
        title=parsed.title,
        abstract=rebuild_abstract(parsed.abstract_inverted_index),
        year=parsed.publication_year,
        doc_type=doc_type,
        venue_type=venue_type,
        venue_name=venue_name,
        subject_code=subject.code,
    )


def derive_registration(raw_dir: Path, event: RegisterEvent) -> DerivedRegistration:
    """Derive a ``register`` event's columns from the body it references (the loader backstop).

    Raises:
        LogError: If the event names a classification-map version or digest this code does not
            hold (ADR 0032 Decision 3).
        RegistrationRefusal, ClassificationRefusal: As :func:`read_response` and
            :func:`classify_work`, plus a digest, work-id or DOI mismatch.
    """
    if (event.subject_map_version, event.subject_map_sha256) != (MAP_VERSION, MAP_SHA256):
        raise LogError(
            f"register {event.event_id!r} names classification map version "
            f"{event.subject_map_version!r} / {event.subject_map_sha256!r}, which this code "
            f"does not hold (it holds version {MAP_VERSION})"
        )
    ref = event.source_payload_ref
    response = read_response(raw_dir, ref.file)
    if response.sha256 != event.source_payload_sha256:
        raise RegistrationRefusal(f"{ref.file!r} no longer matches the registered digest")
    work = next((w for w in response.works if w.get("id") == ref.openalex_id), None)
    if work is None:
        raise RegistrationRefusal(f"{ref.file!r} holds no work {ref.openalex_id!r}")
    derived = classify_work(work)
    if derived.doi != event.doi:
        raise RegistrationRefusal(f"the stored work's DOI {derived.doi!r} is not {event.doi!r}")
    return derived


# -- the writer ----------------------------------------------------------------------------------

_default_ids: IdFactory = MonotonicUlidFactory()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _satisfying(
    candidate: CandidateView, rule: str, works: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The works that satisfy ``rule`` for ``candidate`` (ADR 0029 section 1)."""
    found: list[dict[str, Any]] = []
    for work in works:
        parsed = parse_work(work)
        title, year = parsed.title or "", parsed.publication_year
        if rule == "doi":
            ok = candidate.doi is not None and parsed.doi == candidate.doi
        elif rule == "title_year":
            ok = candidate.doi is None and title_match(candidate.event, title, year)
        else:
            ok = bool(near_matches(candidate.event, [(title, year)]))
        if ok:
            found.append(work)
    return found


def _scopus_duplicate(project: Project, derived: DerivedRegistration) -> str | None:
    """A ``records`` row (Layer 1 as last built) holding the same DOI or title and year."""
    if not project.db_path.is_file():
        return None
    from prismabib.store.db import connect

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute("SELECT record_id, doi, title, year FROM records").fetchall()
    finally:
        connection.close()
    key = normalise_title(derived.title)
    for record_id, doi, title, year in sorted(rows):
        if (doi and normalise_doi(doi) == derived.doi) or (
            title and year == derived.year and normalise_title(title) == key
        ):
            return str(record_id)
    return None


def _readable(raw_dir: Path, event: RegisterEvent) -> DerivedRegistration | None:
    """``event``'s derived columns, or ``None`` when its body cannot be read (DOI test still ran)."""
    try:
        return derive_registration(raw_dir, event)
    except (PrismabibError, OSError):
        return None


def register_candidate(
    project: Project,
    candidates: CandidateLog,
    *,
    candidate_id: str,
    reviewer: str,
    payload_file: str,
    match_rule: MatchRule,
    source_level: int = 4,
    confirmed_near: bool = False,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> RegisterEvent:
    """Register one resolved candidate as a record, or refuse and write nothing.

    Args:
        project: The project (``raw/`` holds the body, ``decisions/`` the log).
        candidates: The candidate log the candidate was pasted into.
        candidate_id: The candidate to register.
        reviewer: The named reviewer. Required.
        payload_file: The OpenAlex response, ``openalex/<run>/response-NNNN.jsonl`` under ``raw/``.
        match_rule: ``doi``, ``title_year`` or ``near_confirmed``.
        source_level: The cascade level that resolved it; only 4 is registrable.
        confirmed_near: The reviewer's confirmation; ``near_confirmed`` is refused without it.
        now: Clock, injected.
        new_id: ULID source, injected.

    Returns:
        The appended event.

    Raises:
        RegistrationRefusal: For every refusal of ADR 0029 section 5 and ADR 0032 Decision 2-5.
        ClassificationRefusal: For a value the classification map does not cover.
        LogError: If the log fails its own checks.
    """
    if source_level in (1, 2, 3):
        raise RegistrationRefusal(
            f"level {source_level} resolves against a Scopus payload: the paper is already a "
            "Scopus record (duplicate); attach it under ADR 0028 Decision 4 instead"
        )
    if source_level != 4:
        raise RegistrationRefusal(f"source level {source_level} is not registrable; only level 4")
    if not reviewer.strip():
        raise RegistrationRefusal("a registration requires a named reviewer")
    if match_rule not in ("doi", "title_year", "near_confirmed"):
        raise RegistrationRefusal(f"unknown match rule {match_rule!r}")
    if match_rule == "near_confirmed" and not confirmed_near:
        raise RegistrationRefusal("a near match needs the reviewer's confirmation")
    candidate = next((v for v in candidates.view() if v.event.candidate_id == candidate_id), None)
    if candidate is None:
        raise RegistrationRefusal(f"unknown candidate {candidate_id!r}")

    response = read_response(project.raw_dir, payload_file)
    matched = _satisfying(candidate, match_rule, response.works)
    if len(matched) != 1:
        raise RegistrationRefusal(
            f"candidate {candidate_id!r} is unresolved under {match_rule!r}: "
            f"{len(matched)} works in {payload_file!r} satisfy it (exactly one is required)"
        )
    derived = classify_work(matched[0])
    duplicate = _scopus_duplicate(project, derived)
    if duplicate is not None:
        raise RegistrationRefusal(
            f"the paper is already a Scopus record ({duplicate}); a registration would duplicate it"
        )

    event_id = new_id()
    event = RegisterEvent(
        event_id=event_id,
        ts=now(),
        project=project.slug,
        record_id=RECORD_ID_PREFIX + event_id,
        candidate_id=candidate_id,
        reviewer=reviewer,
        match_rule=match_rule,
        source_level=4,
        source_payload_ref=PayloadAddress(file=payload_file, openalex_id=derived.openalex_id),
        source_payload_sha256=response.sha256,
        doi=derived.doi,
        subject_map_version=MAP_VERSION,
        subject_map_sha256=MAP_SHA256,
    )
    key = normalise_title(derived.title)

    def check(existing: Sequence[RegistrationRecord]) -> None:
        for active in active_registrations([r.root for r in existing]):
            if active.candidate_id == candidate_id:
                raise RegistrationRefusal(
                    f"candidate {candidate_id!r} already has an active registration "
                    f"({active.record_id})"
                )
            other = None if active.doi == derived.doi else _readable(project.raw_dir, active)
            if active.doi == derived.doi or (
                other is not None
                and other.year == derived.year
                and normalise_title(other.title) == key
            ):
                raise RegistrationRefusal(
                    f"the paper already has an active registration ({active.record_id})"
                )

    registration_log(project.registrations_path).append_if(RegistrationRecord(event), check)
    return event


def reverse_registration(
    project: Project,
    *,
    reverses: str,
    reviewer: str,
    reason: str,
    note: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> ReverseEvent:
    """Reverse an active registration, or refuse and write nothing (ADR 0029 section 7).

    Args:
        project: The project.
        reverses: The ``event_id`` of the ``register`` event (or its ``registered:`` record id).
        reviewer: The named reviewer. Required.
        reason: One of :data:`REVERSAL_REASONS`.
        note: Required (non-empty) when ``reason`` is ``other``.
        now: Clock, injected.
        new_id: ULID source, injected.

    Raises:
        RegistrationRefusal: For a reason outside the closed set, a missing reviewer or note, or a
            target that is unknown or already removed.
    """
    if reason not in REVERSAL_REASONS:
        raise RegistrationRefusal(
            f"reversal reason {reason!r} is not one of {', '.join(REVERSAL_REASONS)}"
        )
    if not reviewer.strip():
        raise RegistrationRefusal("a reversal requires a named reviewer")
    if reason == "other" and not (note or "").strip():
        raise RegistrationRefusal("reversal reason 'other' requires a non-empty note")
    target = reverses.removeprefix(RECORD_ID_PREFIX)
    event = ReverseEvent(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reviewer=reviewer,
        reverses=target,
        reason=cast(ReversalReason, reason),  # checked against REVERSAL_REASONS above
        note=note,
    )
    registration_log(project.registrations_path).append_if(
        RegistrationRecord(event), _require_active(target)
    )
    return event


def supersede_registration(
    project: Project,
    *,
    reverses: str,
    scopus_record_id: str,
    match_rule: Literal["doi", "title_year"],
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> SupersedeEvent:
    """Append a ``supersede`` event for an active registration (the loader's step, section 6)."""
    event = SupersedeEvent(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reverses=reverses,
        scopus_record_id=scopus_record_id,
        match_rule=match_rule,
    )
    registration_log(project.registrations_path).append_if(
        RegistrationRecord(event), _require_active(reverses)
    )
    return event


def _require_active(target: str) -> Callable[[Sequence[RegistrationRecord]], None]:
    def check(existing: Sequence[RegistrationRecord]) -> None:
        if target not in {a.event_id for a in active_registrations([r.root for r in existing])}:
            raise RegistrationRefusal(f"{target!r} is not an active registration")

    return check


__all__ = [
    "CURRENT_REGISTRATION_SCHEMA_VERSION",
    "RECORD_ID_PREFIX",
    "REGISTRATIONS_FILENAME",
    "REQUIRED_SELECT_FIELDS",
    "REVERSAL_REASONS",
    "ClassificationRefusal",
    "DerivedRegistration",
    "PayloadAddress",
    "RegisterEvent",
    "RegistrationRecord",
    "RegistrationRefusal",
    "ReverseEvent",
    "StoredResponse",
    "SupersedeEvent",
    "active_registrations",
    "classify_work",
    "derive_registration",
    "load_registration_events",
    "read_response",
    "register_candidate",
    "registration_log",
    "reverse_registration",
    "supersede_registration",
]
