"""The Source mode choice and the wording around it, as values in and values out (GUI spec #313).

Nothing here prompts or prints: the CLI (and later the GUI) asks the questions and shows the text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from prismabib.errors import ValidationError
from prismabib.project import Project
from prismabib.source_mode import SourceMode

AccessAnswer = Literal["yes", "no", "not sure"]

_ACCESS_ANSWERS: Final[dict[str, AccessAnswer]] = {
    "y": "yes",
    "yes": "yes",
    "n": "no",
    "no": "no",
    "s": "not sure",
    "not sure": "not sure",
}


def parse_access_answer(text: str) -> AccessAnswer:
    """Normalise an answer to "Do you have access to Scopus?".

    Raises:
        ValidationError: For anything but y, yes, n, no, s or not sure (any case or spacing).
    """
    answer = _ACCESS_ANSWERS.get(" ".join(text.lower().split()))
    if answer is None:
        raise ValidationError("please answer y, n or not sure")
    return answer


def source_mode_from_answer(text: str, *, fill_gaps: bool = True) -> SourceMode:
    """The Source mode for an answer to the Scopus question.

    Args:
        text: The answer to "Do you have access to Scopus?".
        fill_gaps: For a "yes": whether OpenAlex fills the gaps of Scopus records. Ignored otherwise.

    Raises:
        ValidationError: If ``text`` is not an accepted answer.
    """
    if parse_access_answer(text) != "yes":
        return "openalex"
    return "scopus+openalex" if fill_gaps else "scopus"


def create_project(
    slug: str, *, title: str, root: Path, source_mode: SourceMode, reviewer: str
) -> Project:
    """Create a Project with its Source mode, recorded as an event by ``reviewer``.

    Raises:
        ConfigError: If ``reviewer`` is blank. Nothing is written.
    """
    return Project.init(slug, title=title, root=root, source_mode=source_mode, actor=reviewer)


def not_sure_notes(slug: str) -> list[str]:
    """What to show once the "not sure" choice has been recorded."""
    return [
        "Mode openalex was recorded: this tool cannot assume you have Scopus access.",
        (
            "To check a Scopus key later, run `prismabib credentials set scopus` and then "
            "`prismabib credentials test scopus` (one request; it needs no project). Only "
            "`credentials test scopus --project <slug>` and `prismabib doctor --check-scopus` "
            "need a project in a Scopus mode."
        ),
        (
            f"To switch: `prismabib sources {slug} --set scopus --actor NAME` (or "
            "`--set scopus+openalex`). It is refused once any OpenAlex search run exists under "
            "raw/openalex-search/ (whatever its state) or screening decisions exist; then "
            "create a new project."
        ),
    ]


def next_steps(
    slug: str, project_root: Path, *, mode: SourceMode, existed: bool, key_missing: bool
) -> list[str]:
    """The guidance after `init`: which files to edit, then the commands to run, in order.

    Args:
        slug: The Project slug.
        project_root: The Project directory, named in the file list.
        mode: The Project's Source mode.
        existed: Whether the Project was already there (then nothing was rewritten).
        key_missing: Whether no OpenAlex key is stored; an OpenAlex-only Project then asks for one.
    """
    openalex = mode == "openalex"
    lines = [""]
    if existed:
        lines += [
            (
                "project.toml and criteria.yaml were already there and were left exactly "
                "as they are; any missing directory has been recreated."
            ),
            "",
            "The two files that define this review are, in the order they are used:",
        ]
    else:
        command = "openalex-search" if openalex else "search"
        lines.append(
            f"`prismabib {command}` cannot run until you edit these two files, in this order:"
        )
    lines += ["", f"  1. {project_root / 'project.toml'}"]
    if openalex:
        lines.append(
            "     Fill in `search` under [openalex_query] -- the OpenAlex search itself "
            '(words, "quoted phrases", uppercase AND / OR / NOT). It is scaffolded empty, '
            "and an empty query is refused rather than sent."
        )
    else:
        lines.append(
            "     Fill in [query] terms / compound_terms -- the Boolean search itself. They "
            "are scaffolded empty, and an empty query is refused rather than silently run "
            "against all of Scopus."
        )
    lines.append(f"  2. {project_root / 'criteria.yaml'}")
    # An OpenAlex work never carries a language, so that rule cannot exclude anything.
    restricted = "doc_types.include" if openalex else "doc_types.include and languages"
    lines += [
        (
            f"     Set temporal.year_start / year_end, and whichever of {restricted} "
            "your protocol restricts; every list left empty means no restriction on "
            "that dimension, which is a real eligibility choice rather than a placeholder. "
            "Replace the starter exclusion reason codes with the ones your review actually "
            "distinguishes, and bump `version` whenever you change any of it. The file's own "
            "comments explain each block."
        ),
        "",
        "Then, in order:",
    ]
    if openalex:
        if key_missing:
            lines.append("  prismabib credentials set openalex   # a free OpenAlex key is required")
        lines += [
            f"  prismabib openalex-search {slug} --budget 30 --dry-run   # cost; no request",
            f"  prismabib openalex-search {slug} --budget 30   # resumable",
            f"  prismabib build {slug} --rebuild",
        ]
    else:
        lines += [
            f"  prismabib search {slug}   # spends Scopus quota; resumable",
            f"  prismabib build {slug}",
        ]
    lines.append(f"  prismabib flow {slug}")
    return lines
