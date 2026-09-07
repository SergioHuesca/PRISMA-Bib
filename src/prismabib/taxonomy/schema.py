"""Per-project taxonomy dimension declarations (ADR 0005; ADR 0023 Decision 2).

Dimensions -- ``learning_paradigm``, ``architecture``, and so on -- are
declared per project in ``taxonomy/dimensions.yaml``, never hardcoded and
never folded into ``criteria.yaml``. ADR 0023 Decision 2 gives the reason a
second file is worth having: ``criteria.yaml`` answers *eligibility* (every
screening decision records the ``criteria.version`` in force when it was
made, and ``engine.replay()`` resolves that version out of git history), a
taxonomy dimension answers *classification*, and the two change on different
cadences. Folding them together would mean a taxonomy tweak bumps the
eligibility version, and a reader auditing an eligibility amendment finds a
changed category list instead.

The reference dimension set (BUILD_PLAN §Stage 8)::

    dimensions:
      - id: learning_paradigm
        multi_label: false
        categories: [unsupervised, weakly_supervised, self_supervised,
                      semi_supervised, fully_supervised, foundation_zero_shot]
      - id: architecture
        multi_label: true
        categories: [cnn, autoencoder, gan, rnn_lstm, transformer, gnn,
                      diffusion, vlm_foundation]

:class:`CountingUnit` is the mandatory, explicit unit every taxonomy rule
file declares (ADR 0005): the source manuscript's taxonomy figure summed to
substantially more than the corpus size because it counted keyword mentions
without declaring it, which is the exact failure mode this architecture
exists to prevent.

This module follows the same load/validate/error-naming convention
:mod:`prismabib.project` established for ``criteria.yaml`` --
``model_config = ConfigDict(extra="forbid")`` throughout, and a missing or
malformed file raises :class:`~prismabib.errors.ConfigError` naming the path
and the offending key, never a bare ``pydantic.ValidationError`` crossing
this module's boundary.
"""

from __future__ import annotations

from enum import StrEnum

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ConfigError
from prismabib.project import Project


class CountingUnit(StrEnum):
    """The unit of count a taxonomy rule file's assignments are in (ADR 0005).

    Mandatory on every rule file (:class:`prismabib.taxonomy.rules.RuleFile`).
    A figure or a coverage report built from an assignment set must state
    which of these it is in its caption -- the unit is exactly what tells a
    reader whether a distribution can be compared against the corpus size.
    """

    PAPERS = "papers"
    """Each record contributes at most one count per category. For a
    ``multi_label: false`` dimension, a distribution in this unit is
    asserted to sum to exactly ``|C|`` (:func:`prismabib.taxonomy.coder.distribution`)."""

    ASSIGNMENTS = "assignments"
    """One count per (record, category) assignment. Legitimate for a
    ``multi_label: true`` dimension for a record to contribute more than one
    count, so the sum may exceed ``|C|`` without that being an error --
    the difference from :attr:`PAPERS` is declared, not accidental."""

    KEYWORD_MENTIONS = "mentions"
    """Raw term frequency: a category may be counted more than once per
    record if its pattern matches more than one field or more than once.
    **Not** a paper distribution -- a caption built from this unit says so
    explicitly (:meth:`prismabib.taxonomy.coder.Distribution.caption`)."""


#: Bucket names a distribution adds for records no category covers, and which
#: a dimension therefore may not declare as a category (ADR 0023 Decision 4:
#: "a rule file cannot name it").
#:
#: Unenforced, that sentence was just prose: a ``dimensions.yaml`` declaring
#: ``categories: [uncoded]`` loaded fine, after which a record the rules
#: genuinely coded ``uncoded`` and a record nobody had looked at landed in the
#: same integer -- and because the total still equalled ``|C|``, the counting
#: guard had nothing to catch.
RESERVED_BUCKET_NAMES: frozenset[str] = frozenset({"uncoded", "reviewed_none"})


class Dimension(BaseModel):
    """One taxonomy dimension: an id, its cardinality, and its closed category set.

    Attributes:
        id: The dimension's identifier, e.g. ``"architecture"``. Referenced
            by a rule file's ``dimension:`` key and by an override event's
            ``dimension`` field.
        multi_label: Whether a record may carry more than one category in
            this dimension at once. ``False`` for e.g. ``learning_paradigm``
            (a record is either unsupervised or not); ``True`` for e.g.
            ``architecture`` (a record may use both a CNN and a transformer).
        categories: The closed set of category ids this dimension accepts.
            A rule file or an override event naming a category outside this
            list is refused (ADR 0023 Decision 6: "rule files cannot invent
            categories").
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    multi_label: bool
    categories: list[str]

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, value: str) -> str:
        """Reject a blank dimension id.

        Args:
            value: The raw ``id`` value.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty or all whitespace.
        """
        if not value.strip():
            raise ValueError("dimension id must not be empty")
        return value

    @field_validator("categories")
    @classmethod
    def _categories_nonempty_and_unique(cls, value: list[str]) -> list[str]:
        """Reject an empty or self-duplicating category list.

        Args:
            value: The raw ``categories`` list.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty (a dimension with nothing to
                classify into is not a dimension), or if any category id is
                repeated (a repeated id is indistinguishable from a typo that
                silently halves a dimension's discriminating power).
        """
        if not value:
            raise ValueError("categories must declare at least one category")
        seen: set[str] = set()
        duplicates: set[str] = set()
        for category in value:
            (duplicates if category in seen else seen).add(category)
        if duplicates:
            raise ValueError(f"duplicate categories: {sorted(duplicates)}")
        reserved = sorted(set(value) & RESERVED_BUCKET_NAMES)
        if reserved:
            raise ValueError(
                f"categories may not use the reserved distribution bucket name(s) {reserved}. "
                "These name the buckets a distribution adds for records no category covers "
                "(ADR 0023 Decisions 4 and 4b); a dimension declaring one makes a genuinely "
                "coded record and an unexamined one indistinguishable in the same integer, "
                "while the sum-to-|C| guard still passes."
            )
        return value


class TaxonomySchema(BaseModel):
    """A project's full set of declared taxonomy dimensions (``taxonomy/dimensions.yaml``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimensions: list[Dimension]

    @model_validator(mode="after")
    def _dimension_ids_unique(self) -> TaxonomySchema:
        """Reject two dimensions declaring the same id.

        Returns:
            ``self`` unchanged, once validated.

        Raises:
            ValueError: If any dimension id is repeated -- a rule file's
                ``dimension:`` key would then be ambiguous about which
                dimension's category set it must respect.
        """
        ids = [dimension.id for dimension in self.dimensions]
        seen: set[str] = set()
        duplicates: set[str] = set()
        for dimension_id in ids:
            (duplicates if dimension_id in seen else seen).add(dimension_id)
        if duplicates:
            raise ValueError(f"duplicate dimension id(s): {sorted(duplicates)}")
        return self

    def dimension(self, dimension_id: str) -> Dimension:
        """Look up one declared dimension by id.

        Args:
            dimension_id: The dimension to find.

        Returns:
            The matching :class:`Dimension`.

        Raises:
            KeyError: If no dimension named ``dimension_id`` is declared.
        """
        for dimension in self.dimensions:
            if dimension.id == dimension_id:
                return dimension
        raise KeyError(dimension_id)

    def __contains__(self, dimension_id: object) -> bool:
        """Whether ``dimension_id`` names a declared dimension."""
        return isinstance(dimension_id, str) and any(
            dimension.id == dimension_id for dimension in self.dimensions
        )


def load_dimensions(project: Project) -> TaxonomySchema:
    """Load and validate a project's ``taxonomy/dimensions.yaml``.

    Mirrors :attr:`prismabib.project.Project.criteria`'s own load/validate
    convention: re-read on every call rather than cached, so an edit to
    ``dimensions.yaml`` is reflected immediately, and every failure raises a
    :class:`~prismabib.errors.ConfigError` naming the path -- never a bare
    :class:`pydantic.ValidationError`.

    Args:
        project: The project whose ``taxonomy/dimensions.yaml`` to load.

    Returns:
        The parsed :class:`TaxonomySchema`.

    Raises:
        ConfigError: If ``taxonomy/dimensions.yaml`` does not exist, is not
            valid YAML, or does not validate as :class:`TaxonomySchema`
            (including an unknown top-level key, a dimension with no
            categories, or two dimensions sharing an id).
    """
    path = project.taxonomy_dimensions_path
    if not path.is_file():
        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise ConfigError(
            f"taxonomy/dimensions.yaml not found at {path}; declare at least one "
            "dimension (BUILD_PLAN §Stage 8's reference set, e.g. 'learning_paradigm' "
            "and 'architecture') before running the taxonomy coder."
        )
        # pragma: no mutate end
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    try:
        return TaxonomySchema.model_validate(raw or {})
    except PydanticValidationError as exc:
        raise ConfigError(f"{path} does not satisfy the dimensions.yaml schema: {exc}") from exc


__all__ = ["CountingUnit", "Dimension", "TaxonomySchema", "load_dimensions"]
