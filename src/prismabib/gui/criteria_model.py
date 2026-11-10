"""What the Criteria form decides, without widgets (GUI spec #312, ticket #326).

Validation is the library's own: the form text is handed to :class:`prismabib.project.Criteria`
and each Pydantic error is attached to the field it names. There is no second validator here.
"""

from __future__ import annotations

from typing import Final

from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ValidationError
from prismabib.project import Criteria

#: Form field keys, in the order the form shows them; the first three hold single values.
FIELDS: Final = (
    "version",
    "temporal.year_start",
    "temporal.year_end",
    "subject_areas",
    "doc_types.include",
    "doc_types.conference_whitelist",
    "languages",
    "manual_abstract.exclude_reason_codes",
    "manual_fulltext.exclude_reason_codes",
)
LIST_FIELDS: Final = FIELDS[3:]


class CriteriaFormError(ValidationError):
    """The form text does not make valid Criteria; ``fields`` maps a field key to its message."""

    def __init__(self, fields: dict[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in fields.items()))
        self.fields = fields


def _lines(text: str) -> list[str]:
    """Items separated by commas or new lines; blanks dropped."""
    return [item.strip() for line in text.splitlines() for item in line.split(",") if item.strip()]


def form_from_criteria(criteria: Criteria) -> dict[str, str]:
    """The form's text for each field (lists one item per line)."""
    out = {}
    for key in FIELDS:
        value: object = criteria
        for part in key.split("."):
            value = getattr(value, part)
        out[key] = "\n".join(value) if isinstance(value, list) else str(value)
    return out


def _field_for(loc: tuple[int | str, ...]) -> str:
    dotted = ".".join(str(part) for part in loc)
    if dotted == "temporal":  # the window check (end before start) belongs beside the end year
        return "temporal.year_end"
    return next((k for k in FIELDS if dotted == k or dotted.startswith(k + ".")), "form")


def criteria_from_form(form: dict[str, str]) -> Criteria:
    """Build Criteria from form text with the library's validation.

    Raises:
        CriteriaFormError: With one message per wrong field.
    """
    raw: dict[str, object] = {
        "version": form["version"].strip(),
        "temporal": {
            "year_start": form["temporal.year_start"].strip(),
            "year_end": form["temporal.year_end"].strip(),
        },
        "doc_types": {
            "include": _lines(form["doc_types.include"]),
            "conference_whitelist": _lines(form["doc_types.conference_whitelist"]),
        },
        "manual_abstract": {
            "exclude_reason_codes": _lines(form["manual_abstract.exclude_reason_codes"])
        },
        "manual_fulltext": {
            "exclude_reason_codes": _lines(form["manual_fulltext.exclude_reason_codes"])
        },
        "subject_areas": _lines(form["subject_areas"]),
        "languages": _lines(form["languages"]),
    }
    try:
        return Criteria.model_validate(raw)
    except PydanticValidationError as exc:
        fields: dict[str, str] = {}
        for error in exc.errors():
            message = error["msg"].removeprefix("Value error, ")
            fields.setdefault(_field_for(error["loc"]), message)
        raise CriteriaFormError(fields) from exc
