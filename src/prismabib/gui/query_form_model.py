"""What the Query form decides and writes, without widgets (GUI spec #312, ticket #327).

The search terms go to ``project.toml`` (``[query]`` in the Scopus modes, ``[openalex_query]``
in mode ``openalex``). The year range and subject areas are not part of a query: the library
reads them from ``criteria.yaml`` (``temporal`` and ``subject_areas``), so that is where they are
written. Only those lines change; every comment and every other key stays as it was. Validation
is the library's own: :func:`prismabib.query.build_query`,
:func:`prismabib.query.validate_openalex_search` and the :class:`prismabib.project.Criteria` model.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import ValidationError as PydanticValidationError

from prismabib.asjc import KNOWN_ABBREVS
from prismabib.errors import ConfigError, ValidationError
from prismabib.project import Criteria, Project
from prismabib.query import build_query, validate_openalex_search

SUBJECT_AREA_CHOICES: Final = tuple(sorted(KNOWN_ABBREVS))
ENRICHMENT_REASON: Final = (
    "Restricting by subject area with Scopus needs Enrichment, which this version does not offer."
)
_PART_SEPARATOR: Final = " + "
_DEFAULT_FIELDS: Final = ["TITLE-ABS-KEY"]


@dataclass
class QueryInputs:
    """What the form holds: chips, a year range, subject areas."""

    terms: list[str] = field(default_factory=list)
    #: Each group is a list of parts that must all appear (AND).
    compound_terms: list[list[str]] = field(default_factory=list)
    year_start: int | None = None
    year_end: int | None = None
    subject_areas: list[str] = field(default_factory=list)
    #: A hand-written OpenAlex search the chips cannot represent; saving replaces it.
    unparsed_search: str | None = None
    #: Why a file could not be read into the form, in the library's words.
    load_problem: str | None = None


def group_to_chip(group: list[str]) -> str:
    """A compound group as one chip: its parts joined by `` + ``."""
    return _PART_SEPARATOR.join(group)


def chip_to_group(chip: str) -> list[str]:
    """One compound chip back into its parts; blank parts are dropped."""
    return [p.strip() for p in chip.split(_PART_SEPARATOR) if p.strip()]


def _clean(values: list[str]) -> list[str]:
    return [v.strip() for v in values if v.strip()]


# -- reading -------------------------------------------------------------------------------------

_PHRASES = re.compile(r'"([^"]+)"')


def _parse_search(search: str) -> tuple[list[str], list[list[str]]] | None:
    """The chips behind a search this form wrote ("a" OR "b", or "a" AND "b"), else ``None``."""
    for joiner in (" OR ", " AND "):
        parts = search.split(joiner)
        phrases = [_PHRASES.fullmatch(p.strip()) for p in parts]
        if all(phrases):
            found = [m.group(1) for m in phrases if m]
            return (found, []) if joiner == " OR " else ([], [found])
    return None


def _toml(project: Project) -> dict[str, Any]:
    try:
        return tomllib.loads((project.root / "project.toml").read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"project.toml could not be read: {exc}") from exc


def load(project: Project) -> QueryInputs:
    """Read the saved query, years and subject areas into form inputs (hand edits included)."""
    inputs = QueryInputs()
    problems: list[str] = []
    try:
        parsed = _toml(project)
        if project.source_mode == "openalex":
            search = str(parsed.get("openalex_query", {}).get("search", "")).strip()
            if search:
                chips = _parse_search(search)
                if chips is None:
                    inputs.unparsed_search = search
                else:
                    inputs.terms, inputs.compound_terms = chips
        else:
            table = parsed.get("query", {})
            inputs.terms = _clean([str(t) for t in table.get("terms", [])])
            for entry in table.get("compound_terms", []):
                parts = entry.get("all", []) if isinstance(entry, dict) else entry
                inputs.compound_terms.append(_clean([str(p) for p in parts]))
    except (ConfigError, AttributeError, TypeError) as exc:
        problems.append(str(exc))
    try:
        criteria = project.criteria
        inputs.year_start = criteria.temporal.year_start
        inputs.year_end = criteria.temporal.year_end
        inputs.subject_areas = list(criteria.subject_areas)
    except ConfigError as exc:
        problems.append(str(exc))
    inputs.load_problem = " ".join(problems) or None
    return inputs


# -- validating ----------------------------------------------------------------------------------


def _openalex_search(inputs: QueryInputs) -> str:
    """The search string for chips: terms OR-ed, or one compound group AND-ed (no grouping in v1)."""
    if (inputs.terms and inputs.compound_terms) or len(inputs.compound_terms) > 1:
        raise ValidationError(
            "OpenAlex searches have no grouping in this version: use search terms only, "
            "or a single compound term on its own."
        )
    phrases = inputs.terms or (inputs.compound_terms[0] if inputs.compound_terms else [])
    for phrase in phrases:
        if '"' in phrase:
            raise ValidationError(f"Remove the double quote from {phrase!r}.")
    return (" OR " if inputs.terms else " AND ").join(f'"{p}"' for p in phrases)


def _criteria_text(project: Project, inputs: QueryInputs, *, with_subjects: bool) -> str:
    """The new ``criteria.yaml`` text: only year_start, year_end and subject_areas are replaced."""
    path = project.root / "criteria.yaml"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"criteria.yaml could not be read: {exc}") from exc
    edits = [
        (r"^(\s+year_start:)[^\n]*", rf"\g<1> {inputs.year_start}"),
        (r"^(\s+year_end:)[^\n]*", rf"\g<1> {inputs.year_end}"),
    ]
    if with_subjects:
        flow = "[" + ", ".join(json.dumps(a) for a in inputs.subject_areas) + "]"
        edits.append((r"^(subject_areas:)[^\n]*\n(?:[ \t]+-[^\n]*\n)*", rf"\g<1> {flow}\n"))
    for pattern, replacement in edits:
        text, count = re.subn(pattern, replacement, text, flags=re.MULTILINE)
        if count != 1:  # a layout this form cannot edit in place: say so, never guess
            raise ConfigError(
                "criteria.yaml has a layout this form cannot edit; change the years and "
                "subject areas in the file by hand."
            )
    return text


def _msg(exc: PydanticValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = err["loc"][0] if err["loc"] else ""
        key = {"temporal": "years", "subject_areas": "subject_areas"}.get(str(loc), "criteria")
        out.setdefault(key, err["msg"].removeprefix("Value error, "))
    return out


def field_errors(project: Project, inputs: QueryInputs) -> dict[str, str]:
    """Each wrong field's message, keyed ``terms``, ``compound_terms``, ``years`` or
    ``subject_areas``; empty when everything is valid. Messages are the library's.
    """
    errors: dict[str, str] = {}
    mode = project.source_mode
    terms, groups = _clean(inputs.terms), [_clean(g) for g in inputs.compound_terms]
    try:
        if not terms and not groups:
            errors["terms"] = "Add at least one search term or compound term."
        elif mode == "openalex":
            validate_openalex_search(
                _openalex_search(QueryInputs(terms=terms, compound_terms=groups))
            )
        else:
            build_query(terms=terms, compound_terms=[{"all": g} for g in groups])
    except (ValidationError, ConfigError) as exc:
        errors["compound_terms" if "compound" in str(exc) else "terms"] = str(exc)
    if inputs.year_start is None or inputs.year_end is None:
        errors["years"] = "Enter both the first and the last year."
    scopus = mode != "openalex"
    if scopus and inputs.subject_areas:
        errors["subject_areas"] = ENRICHMENT_REASON
    if "years" not in errors:
        try:
            text = _criteria_text(project, inputs, with_subjects=not scopus)
            Criteria.model_validate(yaml.safe_load(text) or {})
        except PydanticValidationError as exc:
            errors.update({k: v for k, v in _msg(exc).items() if k not in errors})
        except ConfigError as exc:
            errors["years"] = str(exc)
    return errors


# -- writing -------------------------------------------------------------------------------------


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[\n" + "".join(f"  {i},\n" for i in items) + "]"


def _query_body(terms: list[str], groups: list[list[str]], fields: list[str]) -> str:
    compound = ["{ all = [" + ", ".join(_toml_string(p) for p in g) + "] }" for g in groups]
    return (
        f"terms = {_list([_toml_string(t) for t in terms])}\n"
        f"compound_terms = {_list(compound)}\n"
        f"fields = [{', '.join(_toml_string(f) for f in fields)}]\n"
    )


def _search_line(search: str) -> str:
    quoted = _toml_string(search) if "'" in search else f"'{search}'"
    return f"search = {quoted}\n"


def _replace_table(text: str, name: str, body: str) -> str:
    """Swap the body of ``[name]`` (header to the next header), or append the table."""
    pattern = re.compile(rf"^\[{re.escape(name)}\][^\n]*\n(?:(?!\[)[^\n]*\n?)*", re.MULTILINE)
    new = f"[{name}]\n{body}"
    if pattern.search(text):
        # keep the blank lines that separated this table from the next one
        return pattern.sub(
            lambda m: new + "\n" * (len(m.group()) - len(m.group().rstrip("\n")) - 1),
            text,
            count=1,
        )
    return text.rstrip("\n") + "\n\n" + new


def _atomic_write(path: Path, text: str) -> None:
    # The text was read with universal newlines: write it back with the file's own line ending.
    eol = "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.replace("\n", eol).encode("utf-8"))
    os.replace(tmp, path)


def save_query(project: Project, inputs: QueryInputs) -> None:
    """Write the terms to ``project.toml`` and the years and subject areas to ``criteria.yaml``.

    Both files are checked before either is written. In Scopus modes the subject areas in
    ``criteria.yaml`` are left as they are.

    Raises:
        ValidationError: Naming every wrong field; nothing is written.
        ConfigError: If a file's layout cannot be edited in place.
    """
    errors = field_errors(project, inputs)
    if errors:
        raise ValidationError(" ".join(f"{k}: {v}" for k, v in errors.items()))
    terms, groups = _clean(inputs.terms), [_clean(g) for g in inputs.compound_terms]
    path = project.root / "project.toml"
    old = path.read_text(encoding="utf-8")
    old_parsed = tomllib.loads(old)
    if project.source_mode == "openalex":
        name, body = (
            "openalex_query",
            _search_line(_openalex_search(QueryInputs(terms=terms, compound_terms=groups))),
        )
    else:
        fields = list(old_parsed.get("query", {}).get("fields", _DEFAULT_FIELDS))
        name, body = "query", _query_body(terms, groups, fields)
    new = _replace_table(old, name, body)
    check = tomllib.loads(new)  # the edit must change that one table and nothing else
    if {k: v for k, v in check.items() if k != name} != {
        k: v for k, v in old_parsed.items() if k != name
    }:
        raise ConfigError("project.toml has a layout this form cannot edit; edit it by hand.")
    criteria = _criteria_text(project, inputs, with_subjects=project.source_mode == "openalex")
    _atomic_write(path, new)
    _atomic_write(project.root / "criteria.yaml", criteria)
