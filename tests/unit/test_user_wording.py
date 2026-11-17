"""Wording the researcher reads: grep assertions over the glossary (spec #390, item 7)."""

from __future__ import annotations

import re
from pathlib import Path

GLOSSARY = Path(__file__).resolve().parents[2] / "GLOSSARY.md"


def entry(term: str) -> str:
    """The body of the glossary entry for ``term`` (text after its bold heading)."""
    text = GLOSSARY.read_text(encoding="utf-8")
    match = re.search(
        rf"^\*\*{re.escape(term)}\*\*:\n(.*?)(?=\n\n|\Z)", text, re.DOTALL | re.MULTILINE
    )
    assert match, f"GLOSSARY.md has no entry for {term!r}"
    return match.group(1)


def test_glossary__reviewer_entry__is_the_only_identity_and_there_are_no_accounts() -> None:
    body = entry("Reviewer")

    assert "The Reviewer name typed on each Decision is the only identity on a Decision" in body
    assert "there are no accounts" in body


def test_glossary__actor_entry__is_not_a_second_identity_on_a_decision() -> None:
    body = entry("actor")

    assert "the library's own record of who changed a Source mode" in body
    assert "not a second identity on a Decision" in body


def test_glossary__new_entries__name_no_module_field_or_file() -> None:
    for term in ("Reviewer", "actor"):
        body = entry(term)
        assert not re.search(r"\.(py|yaml|jsonl|md|duckdb)\b|\b\w+_\w+\b|`", body), body
