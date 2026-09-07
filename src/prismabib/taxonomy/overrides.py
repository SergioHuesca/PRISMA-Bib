"""The append-only human taxonomy-override event log (ADR 0023 Decision 3; ADR 0002 reused).

**This amends ADR 0005's event sketch.** ADR 0005 carried a single
``"category": "transformer"`` field; ADR 0023 Decision 3 replaces it with a
``categories: [...]`` list that **replaces the rules' entire output for that
(record, dimension) pair**, never merges with it. A reviewer who overrides
has read the paper; the rules have matched a string, and leaving a
rule-assigned ``cnn`` beside a human ``transformer`` dilutes the judgement
with exactly the noise the reviewer was asked to correct. ``categories: []``
is how a human says "I looked, and none of these categories apply" -- which
is materially different from "nobody looked" (:mod:`prismabib.taxonomy.coder`
keeps the two distinct: see :attr:`~prismabib.taxonomy.coder.CodingResult.human_reviewed`).

**Reuses ADR 0002's append-only machinery, not a second implementation of
it.** :class:`OverrideLog` composes :class:`prismabib.prisma.log.AppendOnlyLog`
-- the exact same locking, per-write ``fsync``, checksum-sidecar and
crash-safety guarantees ``decisions.jsonl`` has -- because ADR 0023
Consequence 2 makes this the one taxonomy artefact this stage produces that
cannot be recomputed: a rule-coded assignment is free to regenerate by
re-running the coder, a human override is not. The project's own
``.gitignore`` (:func:`prismabib.project._default_gitignore`) allowlists
``taxonomy/taxonomy_overrides.jsonl`` and its ``.sha256`` sidecar for exactly
this reason.

**The fold key is ``(record_id, dimension)``, not ``record_id`` alone** --
mirroring :func:`prismabib.prisma.log.fold_events`'s own precedence rule
(latest ``(ts, event_id)`` wins), restated here rather than reused directly
because :class:`OverrideEvent` has no ``stage``/``reviewer``-shaped fold key
to share the same generic implementation with.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationInfo, field_serializer, field_validator
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import LogError, ValidationError
from prismabib.prisma.events import IdFactory, MonotonicUlidFactory
from prismabib.prisma.log import AppendOnlyLog
from prismabib.project import Project
from prismabib.taxonomy.schema import TaxonomySchema

#: The only ``schema_version`` this module currently knows how to read or
#: write, mirroring :data:`prismabib.prisma.events.CURRENT_SCHEMA_VERSION`'s
#: role for the decision log. An override event is a distinct schema from a
#: decision event (different fields entirely), so it gets its own version
#: counter rather than sharing one that would drift for unrelated reasons.
CURRENT_OVERRIDE_SCHEMA_VERSION: Final[int] = 1

#: The fold key: ``(record_id, dimension)``.
OverrideFoldKey = tuple[str, str]


class OverrideEvent(BaseModel):
    """One immutable human taxonomy-override event (ADR 0023 Decision 3).

    Attributes:
        event_id: A ULID (see :class:`~prismabib.prisma.events.MonotonicUlidFactory`),
            or any other string an :class:`~prismabib.prisma.events.IdFactory`
            produces. Sort order over this field is what breaks a ``ts`` tie
            during folding, exactly as for :class:`~prismabib.prisma.events.DecisionEvent`.
        schema_version: Defaults to :data:`CURRENT_OVERRIDE_SCHEMA_VERSION`;
            :class:`~prismabib.prisma.log.AppendOnlyLog` rejects any other
            value found while reading a file.
        ts: The instant the override was recorded. Must be timezone-aware;
            stored (and serialised to JSON) normalised to UTC.
        project: The owning project's slug.
        record_id: The bibliographic record this override applies to.
        dimension: Which taxonomy dimension this override replaces the rule
            output for.
        categories: The **complete** category set this override asserts for
            ``(record_id, dimension)`` -- replaces every rule-assigned
            category for that pair, not just the ones named here (ADR 0023
            Decision 3). ``()`` means "a human looked and nothing applies".
        reviewer: The reviewer's identifier.
        reason: A free-text justification. Defaults to ``""``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    schema_version: int = CURRENT_OVERRIDE_SCHEMA_VERSION
    ts: datetime
    project: str
    record_id: str
    dimension: str
    categories: tuple[str, ...]
    reviewer: str
    reason: str = ""

    @field_validator("event_id", "project", "record_id", "dimension", "reviewer")
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
            ValueError: If ``value`` is empty or all whitespace -- a blank
                ``record_id``/``dimension`` would silently corrupt the
                ``(record_id, dimension)`` fold key.
        """
        if not value.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        return value

    @field_validator("categories")
    @classmethod
    def _categories_no_duplicates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a ``categories`` list naming the same category twice.

        Args:
            value: The raw ``categories`` tuple.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If any category is repeated.
        """
        seen: set[str] = set()
        duplicates: set[str] = set()
        for category in value:
            (duplicates if category in seen else seen).add(category)
        if duplicates:
            raise ValueError(f"categories must not repeat a category: {sorted(duplicates)}")
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
            ValueError: If ``value`` is timezone-naive.
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
            E.g. ``"2026-01-18T14:22:07.412Z"``, matching
            :class:`~prismabib.prisma.events.DecisionEvent`'s own convention.
        """
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fold_override_events(events: Iterable[OverrideEvent]) -> dict[OverrideFoldKey, OverrideEvent]:
    """Fold a sequence of override events into current membership by ``(record_id, dimension)``.

    Same precedence rule as :func:`prismabib.prisma.log.fold_events`: for
    each key, keeps the event with the greatest ``(ts, event_id)`` pair.

    Args:
        events: Override events in any order. The result does not depend on
            iteration order: permuting ``events`` yields an identical mapping.

    Returns:
        A mapping from ``(record_id, dimension)`` to that key's winning
        event. Presence of a key -- regardless of whether its
        ``categories`` is empty -- means a human has reviewed that
        (record, dimension) pair at least once.
    """
    latest: dict[OverrideFoldKey, OverrideEvent] = {}
    for event in events:
        key: OverrideFoldKey = (event.record_id, event.dimension)
        current = latest.get(key)
        if current is None or (event.ts, event.event_id) > (current.ts, current.event_id):
            latest[key] = event
    return latest


class OverrideLog:
    """The append-only, checksum-guarded taxonomy override log for one project.

    Reuses :class:`prismabib.prisma.log.AppendOnlyLog` for every append-only
    guarantee (see the module docstring). What is specific to an override
    lives here: the dimension/category cross-check against a
    :class:`~prismabib.taxonomy.schema.TaxonomySchema`, and the
    ``(record_id, dimension)`` fold.
    """

    def __init__(self, project: Project, *, id_factory: IdFactory | None = None) -> None:
        """Open an override log bound to ``project``.

        Args:
            project: The owning project. Supplies both the log's path
                (:attr:`~prismabib.project.Project.taxonomy_overrides_path`)
                and its slug, stamped onto every event.
            id_factory: Generates each new event's ``event_id``. Defaults to
                a fresh :class:`~prismabib.prisma.events.MonotonicUlidFactory`.
                Tests substitute a seeded, deterministic
                :class:`~prismabib.prisma.events.IdFactory` here.
        """
        self._project = project
        self._id_factory: IdFactory = (
            id_factory if id_factory is not None else MonotonicUlidFactory()
        )
        self._store: AppendOnlyLog[OverrideEvent] = AppendOnlyLog(
            project.taxonomy_overrides_path,
            model=OverrideEvent,
            current_schema_version=CURRENT_OVERRIDE_SCHEMA_VERSION,
            event_id=lambda event: event.event_id,
            noun="override",
            event_description="taxonomy override",
            recovery_hint="OverrideLog.append",
            writer_name="OverrideLog",
            labour="human taxonomy",
        )

    @property
    def path(self) -> Path:
        """The ``taxonomy_overrides.jsonl`` path this log reads and appends to."""
        return self._store.path

    @property
    def checksum_path(self) -> Path:
        """The ``taxonomy_overrides.jsonl.sha256`` sidecar path this log maintains."""
        return self._store.checksum_path

    def load(self) -> list[OverrideEvent]:
        """Read and validate every override event currently in the log.

        Returns:
            Every event, in file order (oldest first).

        Raises:
            LogError: If the checksum sidecar does not match the file's
                confirmed content, the final line is truncated, any line
                declares an unknown ``schema_version``, any line fails to
                parse as a well-formed :class:`OverrideEvent`, or the same
                ``event_id`` appears twice.
        """
        return self._store.load()

    def fold(self) -> dict[OverrideFoldKey, OverrideEvent]:
        """Load the log and fold it into current per-(record, dimension) membership.

        Returns:
            :func:`fold_override_events` applied to :meth:`load`'s result.
        """
        return fold_override_events(self.load())

    def append(
        self,
        *,
        record_id: str,
        dimension: str,
        categories: Sequence[str],
        reviewer: str,
        reason: str = "",
        schema: TaxonomySchema,
    ) -> OverrideEvent:
        """Construct and append a new override event.

        Args:
            record_id: The bibliographic record this override applies to.
            dimension: Which dimension this override replaces the rule
                output for. Must be declared in ``schema``.
            categories: The complete category set to assert -- replaces
                every rule-assigned category for ``(record_id, dimension)``.
                Every entry must be declared for ``dimension`` in ``schema``;
                an empty sequence is valid and means "nothing applies".
            reviewer: The reviewer's identifier.
            reason: A free-text justification. Defaults to ``""``.
            schema: The project's declared taxonomy schema, used to validate
                ``dimension`` and ``categories`` before anything is written.

        Returns:
            The appended :class:`OverrideEvent`, including its generated
            ``event_id`` and ``ts``.

        Raises:
            LogError: If ``dimension`` is not declared in ``schema``, if any
                entry of ``categories`` is not declared for ``dimension``, if
                the resulting ``event_id`` already exists in the log, or if
                the existing log fails any of :meth:`load`'s checks.
            ValidationError: If the constructed event otherwise fails schema
                validation (a blank ``record_id``/``reviewer``, a repeated
                category).
        """
        try:
            declared = schema.dimension(dimension)
        except KeyError:
            known = sorted(d.id for d in schema.dimensions)
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"{self.path}: dimension {dimension!r} is not declared in this project's "
                f"taxonomy/dimensions.yaml (known dimensions: {known})"
            ) from None
            # pragma: no mutate end

        unknown = sorted(set(categories) - set(declared.categories))
        if unknown:
            noun = "category" if len(unknown) == 1 else "categories"
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"{self.path}: override for dimension {dimension!r} names undeclared "
                f"{noun} {unknown} (declared: {sorted(declared.categories)})"
            )
            # pragma: no mutate end

        try:
            event = OverrideEvent(
                event_id=self._id_factory(),
                schema_version=CURRENT_OVERRIDE_SCHEMA_VERSION,
                ts=datetime.now(UTC),
                project=self._project.slug,
                record_id=record_id,
                dimension=dimension,
                categories=tuple(categories),
                reviewer=reviewer,
                reason=reason,
            )
        except PydanticValidationError as exc:
            raise ValidationError(f"invalid taxonomy override event: {exc}") from exc

        self._store.write_event(event)
        return event


__all__ = [
    "CURRENT_OVERRIDE_SCHEMA_VERSION",
    "OverrideEvent",
    "OverrideFoldKey",
    "OverrideLog",
    "fold_override_events",
]
