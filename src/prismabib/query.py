"""The Scopus Boolean query builder (BUILD_PLAN §Stage 2, lines 775-776, 813-814).

Renders the ``[query]`` table of a project's ``project.toml`` (§3.1, lines
327-335) into the Boolean string the Scopus Search API expects. For the
§3.1 example --

```toml
[query]
terms = [
  "video anomaly detection",
  "surveillance anomaly detection",
]
compound_terms = [
  { all = ["abnormal event detection", "video"] },
]
fields = ["TITLE-ABS-KEY"]
```

-- :func:`build_query_for_project` (equivalently, :func:`build_query` called
with the same arguments) renders exactly:

```
TITLE-ABS-KEY("video anomaly detection") OR TITLE-ABS-KEY("surveillance anomaly detection") OR (TITLE-ABS-KEY("abnormal event detection") AND TITLE-ABS-KEY("video"))
```

**On the ``compound_terms`` input shape.** ``tomllib`` parses the §3.1
``compound_terms`` array into ``list[dict[str, list[str]]]`` -- each entry
is the mapping ``{"all": [...]}``, *not* a bare list of strings. A dict is
itself iterable (over its keys), so a version of :func:`build_query` typed
to accept only ``Sequence[Sequence[str]]`` would, if handed that mapping
directly (as any caller reading ``project.toml`` with plain ``tomllib`` and
not going through :class:`_QuerySpec` would), iterate the dict's *keys*
instead of its ``"all"`` value -- silently rendering
``TITLE-ABS-KEY("all")`` instead of the intended AND-group, with no
exception raised anywhere. That is a silently wrong query producing a
silently wrong corpus (the defect class §1.4 exists to prevent, and worse
than a crash because nothing signals it). :func:`build_query` therefore
accepts the §3.1 mapping shape *natively* -- see :func:`_coerce_compound_group`
-- and raises :class:`~prismabib.errors.ConfigError` for anything it
cannot interpret, including an unrecognised mapping key (e.g. ``{"any":
[...]}``), rather than coercing it into something plausible-looking.

**On this module's location.** BUILD_PLAN line 775 names the contract
``prismabib.query`` -- a top-level module -- even though §2.3's repository
layout (lines 178-229) does not list it among ``src/prismabib/*.py``. This
file is placed at ``src/prismabib/query.py`` to match the contract
verbatim; per the Stage 1 precedent, this addition to §2.3 belongs in
``docs/architecture/overview.md``'s "additions to §2.3" list, which is
documentation-engineer's to write, not this module's.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ConfigError, ValidationError
from prismabib.project import Project

_DEFAULT_FIELDS: tuple[str, ...] = ("TITLE-ABS-KEY",)

#: One ``compound_terms`` entry, as a caller may legally pass it to
#: :func:`build_query`: either the §3.1 ``project.toml``/``tomllib`` shape
#: -- a mapping ``{"all": [...]}`` -- or, for backward compatibility, a
#: bare sequence of terms that are all AND-ed together. Runtime-validated
#: by :func:`_coerce_compound_group`, since both shapes commonly arrive as
#: unchecked ``Any`` (parsed TOML/JSON), not through this alias's static
#: type.
CompoundTermGroup = Mapping[str, Sequence[str]] | Sequence[str]


class _CompoundTerm(BaseModel):
    """One entry of ``[query].compound_terms`` -- an ``all`` (AND) group.

    ``extra="forbid"`` so a mistyped or unsupported key (e.g. ``any``
    instead of ``all``) fails loudly at parse time -- via
    :func:`build_query_for_project`'s translation into
    :class:`~prismabib.errors.ConfigError` -- instead of being silently
    dropped, which is Pydantic's default ``extra="ignore"`` behaviour.
    """

    model_config = ConfigDict(extra="forbid")

    all: list[str]


class _QuerySpec(BaseModel):
    """The ``[query]`` table of ``project.toml`` (BUILD_PLAN §3.1, lines 327-335)."""

    terms: list[str] = []
    compound_terms: list[_CompoundTerm] = []
    fields: list[str] = list(_DEFAULT_FIELDS)


def _escape_term(term: str) -> str:
    """Escape a free-text search term for embedding in a quoted Scopus query clause.

    Backslashes are escaped first, then double quotes, so an
    injection-style term (e.g. one containing ``") OR TITLE-ABS-KEY("``)
    cannot break out of its quoted clause and inject additional Boolean
    structure (BUILD_PLAN line 814).

    Args:
        term: The raw search term.

    Returns:
        ``term`` with ``\\`` and ``"`` backslash-escaped.
    """
    return term.replace("\\", "\\\\").replace('"', '\\"')


def _quote(term: str) -> str:
    """Wrap an escaped term in double quotes.

    Args:
        term: The raw search term.

    Returns:
        ``'"' + escaped term + '"'``.
    """
    return f'"{_escape_term(term)}"'


def _render_field_clause(field: str, term: str) -> str:
    """Render a single ``FIELD("term")`` clause.

    Args:
        field: A Scopus field code, e.g. ``"TITLE-ABS-KEY"``.
        term: The raw search term.

    Returns:
        ``FIELD("term")``, with ``term`` escaped and quoted.
    """
    return f"{field}({_quote(term)})"


def _render_term(term: str, fields: Sequence[str]) -> str:
    """Render one simple term across one or more fields.

    Args:
        term: The raw search term.
        fields: The field code(s) to search the term in.

    Returns:
        A single ``FIELD("term")`` clause when ``fields`` has exactly one
        entry (this is the shape of the frozen §3.1 example); when
        ``fields`` has more than one entry, the per-field clauses are
        OR-joined and parenthesised, on the interpretation that a term
        matching *any* of several declared fields should match the query.
    """
    clauses = [_render_field_clause(field, term) for field in fields]
    if len(clauses) == 1:
        return clauses[0]
    return "(" + " OR ".join(clauses) + ")"


def _render_compound(sub_terms: Sequence[str], fields: Sequence[str]) -> str:
    """Render an ``all`` (AND) compound-term group.

    Args:
        sub_terms: The terms that must all be present.
        fields: The field code(s) each sub-term is searched in.

    Returns:
        A parenthesised, AND-joined clause, e.g.
        ``(TITLE-ABS-KEY("a") AND TITLE-ABS-KEY("b"))``.
    """
    return "(" + " AND ".join(_render_term(term, fields) for term in sub_terms) + ")"


def _coerce_compound_group(group: object) -> list[str]:
    """Normalise one ``compound_terms`` entry to its list of AND-ed sub-terms.

    Accepts the §3.1 ``project.toml``/``tomllib`` shape -- a mapping
    ``{"all": [...]}`` -- natively, and, for backward compatibility, a bare
    sequence of strings. ``group`` is validated at runtime (rather than
    trusted via its static type) because both shapes typically arrive as
    unchecked ``Any``: parsed TOML, or a caller's own list literal that may
    not actually match ``build_query``'s declared parameter type.

    Anything else raises :class:`~prismabib.errors.ConfigError` naming what
    was received, rather than being coerced -- in particular, a mapping is
    **not** iterated for its keys (the defect this function exists to
    close; see the module docstring), and a mapping key other than exactly
    ``"all"`` is rejected rather than silently ignored.

    Args:
        group: One entry of ``compound_terms``, as passed by a caller.

    Returns:
        The list of sub-terms that must all (AND) be present.

    Raises:
        ConfigError: If ``group`` is a bare string; a mapping whose keys
            are anything other than exactly ``{"all"}``; a mapping whose
            ``"all"`` value is not a sequence of strings; or any other
            value that is not a sequence of strings.
    """
    if isinstance(group, str):
        raise ConfigError(
            "a query.compound_terms entry must not be a bare string "
            f"(got {group!r}); did you mean the single-term group "
            f"{{'all': [{group!r}]}}?"
        )
    if isinstance(group, Mapping):
        keys = set(group.keys())
        if keys != {"all"}:
            raise ConfigError(
                "a query.compound_terms entry must be a mapping with exactly "
                f"the key 'all' (the project.toml shape {{'all': [...]}}); "
                f"got keys {sorted(str(key) for key in keys)!r} in {group!r}"
            )
        sub_terms = group["all"]
        if (
            not isinstance(sub_terms, Sequence)
            or isinstance(sub_terms, str)
            or not all(isinstance(item, str) for item in sub_terms)
        ):
            raise ConfigError(
                f"query.compound_terms 'all' value must be a list of strings; got {sub_terms!r}"
            )
        return list(sub_terms)
    if isinstance(group, Sequence) and all(isinstance(item, str) for item in group):
        return list(group)
    raise ConfigError(
        "a query.compound_terms entry must be a mapping {'all': [...]} (the "
        f"project.toml shape) or a list of strings; got {group!r}"
    )


def build_query(
    *,
    terms: Sequence[str] = (),
    compound_terms: Sequence[CompoundTermGroup] = (),
    fields: Sequence[str] = _DEFAULT_FIELDS,
) -> str:
    """Render a Scopus Boolean query string from term lists.

    Simple terms are rendered first (in the given order), one
    ``FIELD("term")`` clause per term, followed by each compound (``all``)
    group as a parenthesised AND clause, in the given order. Every clause
    is OR-joined at the top level.

    Args:
        terms: Simple terms, individually OR-joined into the query.
        compound_terms: Groups of terms that must all co-occur (AND). Each
            entry is either the §3.1 ``project.toml``/``tomllib`` shape --
            a mapping ``{"all": ["abnormal event detection", "video"]}`` --
            or, for backward compatibility, a bare list of strings, e.g.
            ``["abnormal event detection", "video"]``; both render
            ``(FIELD("abnormal event detection") AND FIELD("video"))``.
            Each group is itself one OR-ed clause of the overall query. See
            :func:`_coerce_compound_group` for exactly what is and is not
            accepted.
        fields: The Scopus field code(s), e.g. ``["TITLE-ABS-KEY"]``, that
            every term (simple or compound) is searched against.

    Returns:
        The rendered Boolean query string.

    Raises:
        ValidationError: If ``fields`` is empty, if both ``terms`` and
            ``compound_terms`` are empty (nothing to search for), or if a
            compound group's ``all`` list is empty.
        ConfigError: If a ``compound_terms`` entry is not interpretable --
            see :func:`_coerce_compound_group`.
    """
    if not fields:
        raise ValidationError("query.fields must not be empty")
    if not terms and not compound_terms:
        raise ValidationError(
            "query has no terms: [query].terms and [query].compound_terms are both empty"
        )

    clauses: list[str] = [_render_term(term, fields) for term in terms]
    for group in compound_terms:
        sub_terms = _coerce_compound_group(group)
        if not sub_terms:
            raise ValidationError("a query.compound_terms entry has an empty 'all' list")
        clauses.append(_render_compound(sub_terms, fields))

    return " OR ".join(clauses)


def build_query_for_project(project: Project) -> str:
    """Read a project's ``project.toml`` ``[query]`` table and render its Boolean query.

    Args:
        project: The project to read ``project.toml`` from.

    Returns:
        The rendered Boolean query string; see :func:`build_query`.

    Raises:
        ConfigError: If ``project.toml`` is missing, is not valid TOML, has
            no ``[query]`` table, or that table does not match the §3.1
            shape.
        ValidationError: If the parsed ``[query]`` table has no terms, or
            an empty ``fields`` list, or an empty compound-term group; see
            :func:`build_query`.
    """
    path = project.root / "project.toml"
    if not path.is_file():
        raise ConfigError(f"project.toml not found at {path}")
    try:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc

    query_table = raw.get("query")
    if query_table is None:
        raise ConfigError(f"{path} has no [query] table; cannot build a search query")

    # Diagnose the compound_terms shape *before* Pydantic does. `_QuerySpec`
    # rejects a bare string with a raw model_type error naming a private class
    # the reader has never heard of, while `_coerce_compound_group` already
    # carries a message that names the mistake and writes out the fix. Without
    # this pass that message is unreachable from project.toml -- which is the
    # only route most people take -- so the best error in this module fired
    # only for direct `build_query` callers. The first real user hit exactly
    # this and got the Pydantic dump.
    if isinstance(query_table, Mapping):
        for group in query_table.get("compound_terms") or ():
            _coerce_compound_group(group)

    try:
        spec = _QuerySpec.model_validate(query_table)
    except PydanticValidationError as exc:
        raise ConfigError(
            f"{path} [query] table does not match the expected schema: {exc}"
        ) from exc

    return build_query(
        terms=spec.terms,
        compound_terms=[group.all for group in spec.compound_terms],
        fields=spec.fields,
    )
