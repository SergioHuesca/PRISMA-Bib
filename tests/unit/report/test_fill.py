"""The fill contract: substitution, and failing in both directions.

BUILD_PLAN §Stage 10 and EXECUTION_PLAN both name this the last line of
defence against the §1.4 failure mode, and EXECUTION_PLAN adds that weakening
the fail-on-unknown/unused behaviour is a blocking defect rather than a
nice-to-have. These tests are what make that statement enforceable.
"""

from __future__ import annotations

import re

import pytest

from prismabib.errors import ValidationError
from prismabib.report.fill import FillError, fill_manuscript


@pytest.mark.unit
def test_fill__valid_manuscript__substitutes_every_placeholder() -> None:
    """No `{{` survives, and the values are the ones supplied."""
    text = "We screened {{flow.after_language}} records and included {{flow.included}}.\n"

    filled = fill_manuscript(text, {"flow.after_language": 96, "flow.included": 12})

    assert filled == "We screened 96 records and included 12.\n"
    assert "{{" not in filled


@pytest.mark.unit
def test_fill__unknown_key__raises_naming_it() -> None:
    """A manuscript citing a key nothing defines must not render silently.

    Without this the placeholder either survives into the typeset output or
    renders empty, and a sentence loses its number without anyone being told.
    """
    with pytest.raises(FillError) as excinfo:
        fill_manuscript("We screened {{corpus.sizes}} records.", {"corpus.size": 96})

    message = str(excinfo.value)
    assert "corpus.sizes" in message
    assert "does not define" in message


@pytest.mark.unit
def test_fill__unused_key__raises_naming_it() -> None:
    """The reverse drift: a number that stopped being cited.

    This is the direction that gets dropped as pedantic, and it is the one
    that catches a claim being edited out of the prose while the number that
    supported it stays in the export -- so the export still looks complete.
    """
    with pytest.raises(FillError) as excinfo:
        fill_manuscript(
            "We screened {{corpus.size}} records.",
            {"corpus.size": 96, "geography.share.CHN": 0.31},
        )

    message = str(excinfo.value)
    assert "geography.share.CHN" in message
    assert "never cites" in message


@pytest.mark.unit
def test_fill__both_directions_wrong__reports_both_at_once() -> None:
    """One run reports both problems; fixing one must not reveal the other."""
    with pytest.raises(FillError) as excinfo:
        fill_manuscript("{{a.typo}}", {"a.real": 1})

    message = str(excinfo.value)
    assert "a.typo" in message
    assert "a.real" in message


@pytest.mark.unit
def test_fill__placeholder_inside_a_code_block__is_left_alone() -> None:
    """A methods paper documents its own syntax; substituting there corrupts it.

    The fenced example must survive verbatim, and -- the part that makes this
    more than cosmetic -- a key cited *only* inside a fence does not count as
    cited, so it still trips the unused-key check rather than being silently
    satisfied by documentation.
    """
    text = (
        "We screened {{corpus.size}} records.\n"
        "\n"
        "```markdown\n"
        "Cite a count as {{flow.included}} and it is substituted at build time.\n"
        "```\n"
    )

    filled = fill_manuscript(text, {"corpus.size": 96})

    assert "Cite a count as {{flow.included}}" in filled
    assert "We screened 96 records." in filled


@pytest.mark.unit
def test_fill__key_cited_only_in_a_code_block__is_still_unused() -> None:
    """The corollary, asserted rather than assumed.

    If a fenced mention counted as a citation, a number could be "used" by
    documentation alone -- which is exactly the state the unused-key check
    exists to reject.
    """
    text = "Text.\n\n```\n{{flow.included}}\n```\n"

    with pytest.raises(FillError, match=re.escape("flow.included")):
        fill_manuscript(text, {"flow.included": 12})


@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(96, "96", id="int"),
        pytest.param(75.5, "75.5", id="float"),
        pytest.param("1.0.0", "1.0.0", id="str"),
        pytest.param(True, "True", id="bool"),
    ],
)
def test_fill__scalar_values__render_as_their_str(value: object, expected: str) -> None:
    """Every scalar type reaches the page as its ordinary string form."""
    assert fill_manuscript("{{k}}", {"k": value}) == expected


@pytest.mark.unit
def test_fill__no_placeholders_and_no_numbers__is_a_no_op() -> None:
    """An empty contract is satisfied, not an error."""
    assert fill_manuscript("Plain prose.\n", {}) == "Plain prose.\n"


@pytest.mark.unit
def test_fill__latex_target__escapes_specials_in_string_values() -> None:
    """A venue name with ``&`` must not abort ``pdflatex`` at the citing sentence.

    ``numbers.json`` carries ``venues.top*.name``, and "Robotics & Automation"
    is a real IEEE venue. `tables.py` has escaped its generated tables from
    the start, so before this an export could produce a table that compiles
    beside a sentence that does not -- from the same venue name.
    """
    filled = fill_manuscript(
        r"The dominant venue was {{venues.top1.name}}.",
        {"venues.top1.name": "Robotics & Automation 100%_x"},
        escape_latex=True,
    )

    assert r"Robotics \& Automation 100\%\_x" in filled
    assert "Robotics & Automation" not in filled


@pytest.mark.unit
def test_fill__latex_target__leaves_numbers_alone() -> None:
    """Numbers contain nothing LaTeX reads; escaping them could only corrupt them."""
    filled = fill_manuscript(
        "{{n}} records, median {{m}}.",
        {"n": 96, "m": 75.5},
        escape_latex=True,
    )

    assert filled == "96 records, median 75.5."


@pytest.mark.unit
def test_fill__markdown_target__does_not_escape() -> None:
    """The positive control: escaping is opt-in, and Markdown must not get it.

    Without this, an implementation that escaped unconditionally would satisfy
    the test above while corrupting every Markdown manuscript.
    """
    filled = fill_manuscript("{{v}}", {"v": "Robotics & Automation"}, escape_latex=False)

    assert filled == "Robotics & Automation"


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(5, id="bare-number"),
        pytest.param("hello", id="bare-string"),
        pytest.param([], id="list"),
        pytest.param([{"k": 1}], id="list-of-objects"),
    ],
)
def test_fill__numbers_json_not_an_object__raises_a_readable_error(payload: object) -> None:
    """JSON's top level can legally be any of these, and each failed differently.

    A bare number raised ``TypeError: argument of type 'int' is not iterable``
    straight out of the CLI. A bare *string* was worse: iterating it yields
    characters, so ``fill`` reported single letters as unused keys and looked
    like it was working.
    """
    with pytest.raises(ValidationError, match="must contain a JSON object"):
        fill_manuscript("{{k}}", payload)  # type: ignore[arg-type]


@pytest.mark.unit
def test_fill__numbers_json_with_a_nested_value__raises_naming_the_key() -> None:
    """A list has no rendering inside a sentence, and ``str()`` would give it one."""
    with pytest.raises(ValidationError, match="non-scalar"):
        fill_manuscript("{{k}}", {"k": [1, 2]})
