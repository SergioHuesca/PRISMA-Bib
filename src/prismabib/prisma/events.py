"""The Layer 2 decision-event schema (BUILD_PLAN §Stage 4, lines 952-967).

BUILD_PLAN pins the event schema to exactly ten fields, one JSON object per
line of ``decisions.jsonl``:

```json
{
  "event_id": "01HV7...",
  "schema_version": 1,
  "ts": "2026-01-18T14:22:07.412Z",
  "project": "vad-2026",
  "stage": "title_abstract",
  "record_id": "scopus:2-s2.0-85101234567",
  "reviewer": "kp",
  "decision": "exclude",
  "reason_code": "REVIEW_OR_SURVEY",
  "note": "",
  "criteria_version": "1.0.0"
}
```

:class:`DecisionEvent` is a pure schema module: it enforces the shape of one
event (field types, that ``stage`` is one of the two screening stages
decisions are actually logged against, that ``ts`` is unambiguous) but not
the *log-level* policy rules BUILD_PLAN attributes to ``log.py`` --
``reason_code`` being mandatory for ``exclude`` and constrained to a
project's ``criteria.yaml`` is cross-file context this module has no access
to, and duplicate-``event_id``/checksum/schema-version-drift detection is
about the log as a whole, not one event. :mod:`prismabib.prisma.log` owns
all of that.

**Why a domain object, not a dict.** ``DecisionEvent`` is a frozen Pydantic
model, matching the convention :mod:`prismabib.models` already established
for internal domain objects (``Record``, ``Author``, ...): validators raise
``ValueError`` and Pydantic wraps that into ``pydantic.ValidationError``.
Direct construction of a ``DecisionEvent`` is not itself a "public
prismabib API boundary" in the sense BUILD_PLAN's error-taxonomy module
means (:mod:`prismabib.errors`) -- it is object construction, like
``Record(...)`` elsewhere -- so it is the *caller's* job to translate a
``pydantic.ValidationError`` at whatever boundary is doing the crossing.
``DecisionLog.append`` (in ``log.py``) is that boundary for events entering
the log, and translates there.

**ULID, stdlib only.** BUILD_PLAN §2.4's dependency list has no ULID
library, so :class:`MonotonicUlidFactory` implements the format directly:
a 48-bit millisecond Unix timestamp followed by 80 bits of randomness,
Crockford base32-encoded into 26 characters so that byte order, integer
order, and lexicographic string order all agree (this is what lets
``log.py``'s fold break ``ts`` ties by comparing ``event_id`` strings).

Monotonicity within one millisecond is the property the fold's tie-break
depends on: two events appended in the same millisecond must still compare
in append order. A naive generator that redraws 80 random bits every call
cannot guarantee that -- two calls in the same millisecond would be
ordered by chance. :class:`MonotonicUlidFactory` instead *increments* the
random component when the clock has not visibly advanced (or has gone
backwards -- see the class docstring), so within a single instance's
lifetime the sequence is strictly non-decreasing regardless of how many
events land in the same millisecond.

**Why this satisfies ``tests/conftest.py``'s ``IdFactory`` protocol without
importing it.** That fixture module defines its own structural
``Protocol`` (one nullary ``__call__`` returning ``str``) specifically so
production code never has to import from ``tests/``. This module re-states
the identical shape as :class:`IdFactory` on the production side; a
``SeededIdFactory`` built by the test fixture satisfies it structurally,
with no import relationship between the two definitions required or
wanted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_serializer, field_validator

from prismabib.ids import IdFactory, MonotonicUlidFactory
from prismabib.stage import PrismaStage

#: The only ``schema_version`` this codebase currently knows how to read or
#: write. ``log.py`` raises ``LogError`` on any other value it finds in a
#: file (BUILD_PLAN: forward compatibility fails loudly, never silently).
CURRENT_SCHEMA_VERSION: Final[int] = 1

#: ``decision`` is a closed, lowercase three-value enum (BUILD_PLAN line
#: 973). A ``Literal`` alias rather than a Python ``Enum`` because the wire
#: representation *is* the value -- there is no separate name/value split to
#: manage, and Pydantic validates it exactly as strictly either way.
Decision = Literal["include", "exclude", "unsure"]

#: The only ``PrismaStage`` members a decision event may be logged against.
#: ``RAW``/``AUTOMATED``/``LANGUAGE``/``INCLUDED`` are computed sets
#: (BUILD_PLAN line 950: "Why A and L are computed, not logged") and never
#: appear as an event's ``stage``.
_LOGGABLE_STAGES: Final[frozenset[PrismaStage]] = frozenset(
    {PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT}
)


# ---------------------------------------------------------------------------
# DecisionEvent
# ---------------------------------------------------------------------------


class DecisionEvent(BaseModel):
    """One immutable Layer 2 screening decision (BUILD_PLAN lines 954-967).

    Field order matches the BUILD_PLAN example exactly, which is also the
    order :meth:`model_dump_json` serialises in -- so a freshly-appended
    event's JSON line is byte-for-byte comparable to the example.

    Attributes:
        event_id: A ULID (see :class:`MonotonicUlidFactory`), or any other
            string produced by an :class:`IdFactory` -- format is not
            validated here so a test's seeded, non-ULID-shaped double
            remains a valid ``event_id``. Sort order over this field is
            what breaks a ``ts`` tie during folding.
        schema_version: The event schema version this object was built
            under. Defaults to :data:`CURRENT_SCHEMA_VERSION`; ``log.py``
            rejects any other value found while reading a file, rather
            than trying to interpret it.
        ts: The instant the decision was recorded. Must be timezone-aware;
            stored (and serialised to JSON) normalised to UTC.
        project: The owning project's slug.
        stage: Which screening stage this decision belongs to. Restricted
            to :attr:`~prismabib.stage.PrismaStage.TITLE_ABSTRACT` and
            :attr:`~prismabib.stage.PrismaStage.FULLTEXT` -- the two stages
            BUILD_PLAN's Layer 2 actually logs decisions against.
        record_id: The bibliographic record this decision applies to.
        reviewer: The reviewer's identifier.
        decision: One of ``"include"``, ``"exclude"``, ``"unsure"``.
        reason_code: Required when ``decision == "exclude"`` -- enforced by
            ``log.py``, not here, since validating it against
            ``criteria.yaml`` needs a :class:`~prismabib.project.Project`
            this model has no reference to. ``None`` otherwise.
        note: A free-text annotation. Defaults to ``""``.
        criteria_version: The ``criteria.yaml`` ``version`` in force when
            this decision was made.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    schema_version: int = CURRENT_SCHEMA_VERSION
    ts: datetime
    project: str
    stage: PrismaStage
    record_id: str
    reviewer: str
    decision: Decision
    reason_code: str | None = None
    note: str = ""
    criteria_version: str

    @field_validator("event_id", "project", "record_id", "reviewer", "criteria_version")
    @classmethod
    def _must_be_nonempty(cls, value: str, info: ValidationInfo) -> str:
        """Reject a blank identifier field.

        Args:
            value: The raw field value.
            info: Pydantic's validation context, used only for its
                ``field_name`` in the error message.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty or all whitespace. A blank
                ``record_id``/``reviewer``/``project`` would silently
                corrupt the ``(stage, record_id, reviewer)`` fold key.
        """
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        return value

    @field_validator("stage")
    @classmethod
    def _stage_must_be_loggable(cls, value: PrismaStage) -> PrismaStage:
        """Reject a computed-set stage that Layer 2 never logs against.

        Args:
            value: The already-coerced ``PrismaStage``.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is not one of :data:`_LOGGABLE_STAGES`.
        """
        if value not in _LOGGABLE_STAGES:
            allowed = sorted(stage.value for stage in _LOGGABLE_STAGES)
            raise ValueError(
                f"stage {value.value!r} is not a screening stage decisions are logged "
                f"against; expected one of {allowed}"
            )
        return value

    @field_validator("ts")
    @classmethod
    def _ts_must_be_timezone_aware(cls, value: datetime) -> datetime:
        """Require an unambiguous instant and normalise it to UTC.

        Args:
            value: The already-parsed ``datetime``.

        Returns:
            ``value`` converted to UTC.

        Raises:
            ValueError: If ``value`` is timezone-naive -- the fold orders
                events by ``ts``, and a naive datetime cannot be compared
                against an aware one without silently assuming a timezone.
        """
        if value.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return value.astimezone(UTC)

    @field_serializer("ts", when_used="json")
    def _serialize_ts(self, value: datetime) -> str:
        """Render ``ts`` as millisecond-precision UTC with a ``Z`` suffix.

        Args:
            value: This event's (already UTC-normalised) ``ts``.

        Returns:
            E.g. ``"2026-01-18T14:22:07.412Z"``, matching the BUILD_PLAN
            example exactly.
        """
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "CURRENT_SCHEMA_VERSION",
    "Decision",
    "DecisionEvent",
    "IdFactory",
    "MonotonicUlidFactory",
]
