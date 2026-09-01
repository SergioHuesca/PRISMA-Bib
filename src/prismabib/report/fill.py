"""``prismabib fill`` -- substitute ``{{key}}`` placeholders from ``numbers.json``.

BUILD_PLAN §Stage 10 and EXECUTION_PLAN both single this out: *"fails on any
unknown or unused key"*, and EXECUTION_PLAN adds that any weakening of that
behaviour is a blocking defect rather than a nice-to-have. Both directions
matter, and the second is the one people are tempted to drop:

- **Unknown key** -- the manuscript cites ``{{corpus.sizes}}``. Without the
  check the placeholder survives into the output, or worse renders as empty,
  and a sentence loses its number silently.
- **Unused key** -- ``numbers.json`` defines ``geography.share.CHN`` and no
  sentence cites it any more. That is the *reverse* drift: a number that was
  once load-bearing has been edited out of the prose, and nobody noticed that
  the claim it supported went with it.

Placeholders inside fenced or indented code blocks are left alone. A methods
paper documents its own substitution syntax -- ``Cite a count as
{{flow.included}}`` inside a fence is documentation, not a citation -- and
substituting there would corrupt the example while satisfying every check.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from prismabib.errors import PrismabibError

if TYPE_CHECKING:
    from collections.abc import Mapping

#: ``{{ key }}`` with optional inner whitespace. Keys are dotted identifiers,
#: which is narrow on purpose: a permissive pattern would match LaTeX and
#: Jinja constructs that happen to share the braces.
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_.]*)\s*\}\}")

#: A fenced code block, ``` or ~~~, with any info string.
_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})")


class FillError(PrismabibError):
    """A manuscript and a ``numbers.json`` that do not agree.

    Raised for an unknown key, an unused key, or both at once -- both lists
    are reported together, because a run that fixed one and then failed on
    the other would cost a second round trip for no reason.
    """


def _code_block_lines(text: str) -> frozenset[int]:
    """Line indices that lie inside a fenced code block.

    Args:
        text: The manuscript.

    Returns:
        Zero-based indices of every line inside a fence, including the fence
        lines themselves. An unclosed fence is treated as running to the end
        of the document, which is how every Markdown renderer treats it.
    """
    inside: set[int] = set()
    fence: str | None = None
    for index, line in enumerate(text.splitlines()):
        match = _FENCE.match(line)
        if fence is None and match is not None:
            fence = match.group(2)[0]
            inside.add(index)
            continue
        if fence is not None:
            inside.add(index)
            if match is not None and match.group(2)[0] == fence:
                fence = None
    return frozenset(inside)


def fill_manuscript(text: str, numbers: Mapping[str, Any]) -> str:
    """Substitute every placeholder in ``text`` from ``numbers``.

    Args:
        text: The manuscript, Markdown or LaTeX.
        numbers: A flat scalar mapping, as written to ``numbers.json``.

    Returns:
        ``text`` with every placeholder outside a code block replaced by its
        value. No ``{{`` referring to a known key survives.

    Raises:
        FillError: If the manuscript cites a key ``numbers`` does not define,
            or if ``numbers`` defines a key the manuscript never cites. The
            message lists both sets in sorted order.
    """
    skip = _code_block_lines(text)
    cited: set[str] = set()
    unknown: set[str] = set()
    out_lines: list[str] = []

    for index, line in enumerate(text.splitlines(keepends=True)):
        if index in skip:
            out_lines.append(line)
            continue

        def replace(match: re.Match[str]) -> str:
            key = match.group(1)
            cited.add(key)
            if key not in numbers:
                unknown.add(key)
                return match.group(0)
            return str(numbers[key])

        out_lines.append(_PLACEHOLDER.sub(replace, line))

    unused = set(numbers) - cited
    if unknown or unused:
        problems: list[str] = []
        if unknown:
            problems.append(
                f"cites {len(unknown)} key(s) numbers.json does not define: "
                f"{', '.join(sorted(unknown))}"
            )
        if unused:
            problems.append(
                f"numbers.json defines {len(unused)} key(s) the manuscript never cites: "
                f"{', '.join(sorted(unused))}"
            )
        raise FillError(
            "the manuscript and numbers.json disagree -- " + "; and ".join(problems) + ".\n"
            "\nBoth directions are errors on purpose. An undefined key means a sentence "
            "loses its number silently. An unused key means a number that was once cited "
            "no longer is, so the claim it supported may have been edited away with it "
            "(BUILD_PLAN Stage 10). If a key is genuinely no longer needed, remove it from "
            "the export rather than leaving it unreferenced."
        )
    return "".join(out_lines)


__all__ = ["FillError", "fill_manuscript"]
