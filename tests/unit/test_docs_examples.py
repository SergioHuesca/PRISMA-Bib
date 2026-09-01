"""Documentation examples must survive the code they describe.

A tutorial is a promise that following it works. These tests check the
promise mechanically, because reading a page cannot: the demo walkthrough
shipped a sample PRISMA diagram whose numbers did not balance --
``1322 != 1340`` -- in a tool whose entire purpose is that they do, and a
``criteria.yaml`` excerpt that is rejected if pasted whole.

Only what is *checkable* is checked. Prose is not, and no attempt is made to
run Scopus.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ValidationError
from prismabib.prisma.flow import FlowCounts
from prismabib.project import Criteria
from prismabib.query import build_query

DOCS = Path(__file__).parent.parent.parent / "docs"

#: Blocks deliberately showing what *not* to write. Keyed by the marker their
#: own text carries, so a block cannot be exempted silently.
_WRONG_MARKERS = ("# WRONG", "# wrong", "Do not")


def _fenced(path: Path, language: str) -> list[str]:
    """Every fenced block of one language in a page (helper, not a test)."""
    return re.findall(rf"```{language}\n(.*?)```", path.read_text(encoding="utf-8"), re.DOTALL)


@pytest.mark.unit
@pytest.mark.parametrize("page", sorted((DOCS / "how-to").glob("*.md")), ids=lambda p: p.name)
def test_docs__toml_examples__parse_and_render(page: Path) -> None:
    """Every `[query]` example parses as TOML and renders as a Scopus query.

    An example a reader pastes and cannot run is worse than no example: it
    costs them the time to discover the page is wrong, and the trust to use
    the rest of it.
    """
    for index, block in enumerate(_fenced(page, "toml"), start=1):
        if any(marker in block for marker in _WRONG_MARKERS):
            continue
        try:
            parsed = tomllib.loads(block)
        except tomllib.TOMLDecodeError as exc:  # pragma: no cover - failure path
            pytest.fail(f"{page.name} toml block {index} does not parse: {exc}")

        query = parsed.get("query")
        if not isinstance(query, dict) or not (query.get("terms") or query.get("compound_terms")):
            continue  # a fragment, or the empty scaffold `init` writes
        build_query(
            terms=query.get("terms", ()),
            compound_terms=query.get("compound_terms", ()),
            fields=query.get("fields", ("TITLE-ABS-KEY",)),
        )


@pytest.mark.unit
@pytest.mark.parametrize("page", sorted((DOCS / "how-to").glob("*.md")), ids=lambda p: p.name)
def test_docs__complete_criteria_examples__validate(page: Path) -> None:
    """A YAML block that looks like a whole `criteria.yaml` must be one.

    Judged by the presence of `version:`, which is what makes a block read as
    a complete file rather than an excerpt. Excerpts are skipped -- they are
    legitimate, provided the page says so.
    """
    for index, block in enumerate(_fenced(page, "yaml"), start=1):
        if any(marker in block for marker in _WRONG_MARKERS):
            continue
        parsed = yaml.safe_load(block)
        if not isinstance(parsed, dict) or "version" not in parsed:
            continue
        try:
            Criteria.model_validate(parsed)
        except PydanticValidationError as exc:  # pragma: no cover - failure path
            missing = ", ".join(str(error["loc"][0]) for error in exc.errors())
            pytest.fail(
                f"{page.name} yaml block {index} carries `version:` so it reads as a complete "
                f"criteria.yaml, but pasting it is rejected: missing {missing}. Either complete "
                "it or drop `version:` and say in the text that it is an excerpt."
            )


#: The order `cli._print_flow` emits the thirteen counts in. Positional rather
#: than label-matched: the labels contain parentheses, capitals and a repeated
#: word ("excluded" appears in both the screening and eligibility sections), and
#: a parser keyed on them is more fragile than the thing it checks.
_FLOW_FIELDS = (
    "identified",
    "duplicates_across_searches",
    "removed_other_reasons",
    "excluded_automated",
    "after_automated",
    "excluded_language",
    "after_language",
    "excluded_title_abstract",
    "unsure_title_abstract",
    "retrieved_fulltext",
    "excluded_fulltext_total",
    "unsure_fulltext",
    "included",
)


@pytest.mark.unit
@pytest.mark.parametrize("page", sorted(DOCS.rglob("*.md")), ids=lambda p: p.name)
def test_docs__sample_prisma_diagrams__add_up(page: Path) -> None:
    """A PRISMA diagram printed in the docs must satisfy `assert_consistent`.

    The demo page shipped one that did not: `after_language` was 1,322 while
    its three parts summed to 1,340. A reader following the page would have put
    an impossible diagram on a slide, from a tool whose one claim is that its
    numbers close -- and `assert_consistent` would have rejected the very
    output the page shows.
    """
    text = page.read_text(encoding="utf-8")
    for block in re.findall(r"```\n(PRISMA 2020 flow.*?)```", text, re.DOTALL):
        values = [
            int(match.replace(",", ""))
            for match in re.findall(r"^\s{2,}\S.*?\s{2,}-?([0-9][0-9,]*)\s*$", block, re.MULTILINE)
        ]
        assert len(values) == len(_FLOW_FIELDS), (
            f"{page.name}: expected {len(_FLOW_FIELDS)} counts in the printed diagram, "
            f"found {len(values)}. If `cli._print_flow` changed shape, update _FLOW_FIELDS."
        )
        fields = dict(zip(_FLOW_FIELDS, values, strict=True))
        total = fields.pop("excluded_fulltext_total")
        counts = FlowCounts(**fields, excluded_fulltext={"REASON": total} if total else {})
        try:
            counts.assert_consistent()
        except ValidationError as exc:  # pragma: no cover - failure path
            pytest.fail(f"{page.name} prints a PRISMA diagram that does not close: {exc}")
