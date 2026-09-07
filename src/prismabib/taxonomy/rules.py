"""The taxonomy rule DSL and its load-time validation (ADR 0005; ADR 0023 Decisions 6-7).

A rule file (``taxonomy/rules/<dimension>.yaml``) is versioned YAML, not
code (ADR 0005 Constraints: "Rules are configuration, not code"). Each
category declares ``any`` (OR-ed field patterns -- a match on any one is
enough), an optional ``none`` (a negative lookaside: a match here suppresses
an otherwise-fired category), and a ``confidence`` score::

    version: 1.3.0
    dimension: architecture
    counting_unit: papers
    categories:
      - id: transformer
        any:
          - {field: author_keywords, pattern: '\\b(transformer|vision transformer|vit)\\b'}
          - {field: title,           pattern: '\\btransformer\\b'}
        none:
          - {field: abstract, pattern: '\\btransformer (?:oil|winding|substation)\\b'}
        confidence: 0.9

**ADR 0023 Decision 6: everything a rule file can get wrong fails at load,
never at match time.** A malformed regex, a ``field:`` outside the closed
vocabulary (``title``, ``abstract``, ``author_keywords``, ``index_keywords``),
a category absent from the dimension's declared category set, a missing
``version``/``counting_unit``/``dimension``, or a ``dimension:`` no schema
declares -- all raise here, in :func:`load_rule_file`, never partway through
:func:`prismabib.taxonomy.coder.code`. A rule that failed on record 900 would
already have written 899 assignments under a file that was never valid; this
module exists so that cannot happen.

**ADR 0023 Decision 7: rule *content* is validated by the audit sample, not
by unit tests.** This module's own test suite therefore asserts load-time
*validation* -- a malformed regex raises, an unknown field raises, an
undeclared category raises -- never that a particular pattern matches a
particular string. Whether ``architecture.yaml`` is a *good* rule file is a
question for :mod:`prismabib.taxonomy.review`'s audit-sample agreement rate,
not for this module's tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ConfigError
from prismabib.taxonomy.schema import CountingUnit, TaxonomySchema

#: The closed field vocabulary ADR 0023 Decision 6 requires: a rule may match
#: against exactly these four fields, and nothing else -- a misspelling like
#: ``abstrct`` is refused rather than silently treated as a field that
#: matches nothing, because a silently-never-matching rule is
#: indistinguishable from a category that genuinely does not occur.
RuleField = Literal["title", "abstract", "author_keywords", "index_keywords"]

#: Mirrors :data:`prismabib.project._SEMVER_RE`. Duplicated rather than
#: imported: that name is private to ``project.py``'s own ``Criteria.version``
#: validator, and a rule file's ``version`` is a materially different concept
#: (a taxonomy rule version, not a review's eligibility-protocol version) that
#: happens to share the same syntactic shape.
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-][0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$")


class FieldPattern(BaseModel):
    """One ``{field, pattern}`` clause inside a category's ``any``/``none`` list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: RuleField
    pattern: str


class CategoryRule(BaseModel):
    """One category's matching rule: what fires it, what suppresses it, how confidently."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    any: list[FieldPattern]
    none: list[FieldPattern] = []
    confidence: float = 1.0

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, value: str) -> str:
        """Reject a blank category id.

        Args:
            value: The raw ``id`` value.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty or all whitespace.
        """
        if not value.strip():
            raise ValueError("category id must not be empty")
        return value

    @field_validator("any")
    @classmethod
    def _any_nonempty(cls, value: list[FieldPattern]) -> list[FieldPattern]:
        """Reject a category with no ``any`` clause at all.

        Args:
            value: The raw ``any`` list.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty -- a category that can never
                fire is not a rule, it is a category the rule file forgot.
        """
        if not value:
            raise ValueError("category rule must declare at least one 'any' clause")
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_in_unit_interval(cls, value: float) -> float:
        """Reject a confidence outside ``[0, 1]``.

        Args:
            value: The raw ``confidence`` value.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is not within ``[0.0, 1.0]``.
        """
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"confidence must be within [0, 1], got {value!r}")
        return value


class RuleFile(BaseModel):
    """The parsed, but not yet compiled or cross-checked, shape of one rule file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    dimension: str
    counting_unit: CountingUnit
    categories: list[CategoryRule]

    @field_validator("version")
    @classmethod
    def _require_semantic_version(cls, value: str) -> str:
        """Reject a ``version`` that is not ``MAJOR.MINOR.PATCH``-shaped.

        Args:
            value: The raw ``version`` string.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is not a syntactically valid semantic
                version (ADR 0005: "versioned rules"; a rule file with no
                usable version cannot be cited in an override-survives-a-
                rule-bump audit trail).
        """
        if _SEMVER_RE.match(value) is None:
            raise ValueError(
                f"version {value!r} is not a semantic version (expected MAJOR.MINOR.PATCH)"
            )
        return value

    @field_validator("dimension")
    @classmethod
    def _dimension_nonempty(cls, value: str) -> str:
        """Reject a blank ``dimension`` value.

        Args:
            value: The raw ``dimension`` value.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty or all whitespace.
        """
        if not value.strip():
            raise ValueError("dimension must not be empty")
        return value

    @field_validator("categories")
    @classmethod
    def _categories_nonempty(cls, value: list[CategoryRule]) -> list[CategoryRule]:
        """Reject a rule file with no categories at all.

        Args:
            value: The raw ``categories`` list.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is empty.
        """
        if not value:
            raise ValueError("categories must declare at least one category rule")
        return value


@dataclass(frozen=True)
class CompiledFieldPattern:
    """A :class:`FieldPattern` whose regex has already been compiled and validated."""

    field: RuleField
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class CompiledCategoryRule:
    """A :class:`CategoryRule` with every pattern compiled, ready for the coder."""

    id: str
    any: tuple[CompiledFieldPattern, ...]
    none: tuple[CompiledFieldPattern, ...]
    confidence: float


@dataclass(frozen=True)
class CompiledRuleFile:
    """A fully load-time-validated rule file: cross-checked against a :class:`TaxonomySchema`.

    Everything :func:`load_rule_file` can catch about ``path`` -- a malformed
    regex, an unknown field, an undeclared category, a dimension the schema
    does not declare -- has already been caught by the time one of these
    exists. :func:`prismabib.taxonomy.coder.code` therefore never needs to
    validate a rule file it is handed; it only ever applies one.
    """

    path: Path
    version: str
    dimension: str
    counting_unit: CountingUnit
    categories: tuple[CompiledCategoryRule, ...]

    def category_ids(self) -> frozenset[str]:
        """The set of category ids this rule file declares."""
        return frozenset(category.id for category in self.categories)


def _compile_pattern(
    path: Path, category_id: str, field_pattern: FieldPattern
) -> CompiledFieldPattern:
    """Compile one field pattern's regex, raising a load-time error if it is malformed.

    Args:
        path: The rule file being loaded, for the error message.
        category_id: The category this pattern belongs to, for the error message.
        field_pattern: The uncompiled ``{field, pattern}`` pair.

    Returns:
        The compiled pattern.

    Raises:
        ConfigError: If ``field_pattern.pattern`` is not a valid regular
            expression (ADR 0023 Decision 6: fails at load, not at match).
    """
    try:
        compiled = re.compile(field_pattern.pattern)
    except re.error as exc:
        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise ConfigError(
            f"{path}: category {category_id!r} field {field_pattern.field!r} pattern "
            f"{field_pattern.pattern!r} is not a valid regular expression: {exc}"
        ) from exc
        # pragma: no mutate end
    return CompiledFieldPattern(field=field_pattern.field, pattern=compiled)


def load_rule_file(path: Path, *, schema: TaxonomySchema) -> CompiledRuleFile:
    """Load, validate, and compile one taxonomy rule file.

    Args:
        path: The ``taxonomy/rules/<dimension>.yaml`` file to load.
        schema: The project's declared :class:`~prismabib.taxonomy.schema.TaxonomySchema`
            -- what this rule file's ``dimension`` and every category id are
            cross-checked against.

    Returns:
        The compiled, fully load-time-validated rule file.

    Raises:
        ConfigError: If ``path`` does not exist, is not valid YAML, does not
            satisfy the rule-file schema (a missing ``version``/``dimension``/
            ``counting_unit``, an unknown top-level or nested key, a field
            outside the closed vocabulary, a malformed regex), names a
            ``dimension`` the schema does not declare, or names a category
            the dimension does not declare.
    """
    if not path.is_file():
        raise ConfigError(f"taxonomy rule file not found at {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    try:
        parsed = RuleFile.model_validate(raw or {})
    except PydanticValidationError as exc:
        raise ConfigError(f"{path} does not satisfy the taxonomy rule-file schema: {exc}") from exc

    try:
        dimension = schema.dimension(parsed.dimension)
    except KeyError:
        known = sorted(declared.id for declared in schema.dimensions)
        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise ConfigError(
            f"{path}: dimension {parsed.dimension!r} is not declared in this project's "
            f"taxonomy/dimensions.yaml (known dimensions: {known})"
        ) from None
        # pragma: no mutate end

    known_categories = set(dimension.categories)
    declared_categories = {category.id for category in parsed.categories}
    unknown = sorted(declared_categories - known_categories)
    if unknown:
        noun = "category" if len(unknown) == 1 else "categories"
        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise ConfigError(
            f"{path}: {noun} {unknown} not declared for dimension {dimension.id!r} in "
            f"taxonomy/dimensions.yaml (declared: {sorted(known_categories)})"
        )
        # pragma: no mutate end

    compiled_categories = tuple(
        CompiledCategoryRule(
            id=category.id,
            any=tuple(_compile_pattern(path, category.id, clause) for clause in category.any),
            none=tuple(_compile_pattern(path, category.id, clause) for clause in category.none),
            confidence=category.confidence,
        )
        for category in parsed.categories
    )

    return CompiledRuleFile(
        path=path,
        version=parsed.version,
        dimension=parsed.dimension,
        counting_unit=parsed.counting_unit,
        categories=compiled_categories,
    )


__all__ = [
    "CategoryRule",
    "CompiledCategoryRule",
    "CompiledFieldPattern",
    "CompiledRuleFile",
    "FieldPattern",
    "RuleField",
    "RuleFile",
    "load_rule_file",
]
