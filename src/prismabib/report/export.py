"""Write ``exports/`` -- the citable bundle.

BUILD_PLAN §Stage 10. Everything a manuscript quotes comes out of this
directory, and the guarantee is that nothing in it was typed by a human.

``manifest.json`` is what makes an exported number *traceable*. It records the
criteria version, the run ids, the package version and the git commit SHA, so a
reader who has a number can recover the state of the code that produced it.
Three ways that traceability can fail, and what is done about each:

- **A dirty working tree.** The SHA names a commit whose contents are not what
  ran. ``dirty: true`` and a loud warning (S10-AC2).
- **An unpushed commit.** The SHA is real but resolves nowhere a reader can
  reach. BUILD_PLAN calls this out as the same defect class as a dirty tree,
  which ``dirty`` alone does not cover, so ``commit_is_pushed`` records it
  separately.
- **No git at all.** Exporting from an unpacked tarball is legitimate but not
  traceable; the SHA is ``null`` and ``dirty`` is ``true``, because "unknown"
  must never read as "clean".
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from prismabib.prisma.flow import compute_flow_counts
from prismabib.report.flow_diagram import flow_diagram_svg
from prismabib.report.numbers import numbers_map
from prismabib.report.tables import Table, build_tables, to_csv, to_latex, to_markdown
from prismabib.store.db import connect

if TYPE_CHECKING:
    from prismabib.prisma.flow import FlowCounts
    from prismabib.project import Project

_log = structlog.get_logger(__name__)

#: Seconds before a git subprocess is abandoned. Same reasoning as
#: `criteria._GIT_TIMEOUT_SECONDS`: a hung git must not hang an export.
_GIT_TIMEOUT_SECONDS = 30

#: Keys whose values legitimately differ between two runs of the same export.
#: Stage 11's reproducibility test compares ``numbers.json`` across runs and
#: this is the allowlist it honours; BUILD_PLAN requires any addition here to
#: carry a reviewer's justification, because every added key is a value that
#: stops being checked.
VOLATILE_MANIFEST_KEYS = ("exported_at",)


@dataclass(frozen=True)
class ExportResult:
    """What an export produced.

    Attributes:
        root: The ``exports/`` directory written.
        numbers: The flat scalar mapping, as written to ``numbers.json``.
        manifest: The provenance mapping, as written to ``manifest.json``.
        figures: Paths of every figure written, sorted.
        tables: Paths of every table rendering written, sorted.
    """

    root: Path
    numbers: dict[str, Any]
    manifest: dict[str, Any]
    figures: tuple[Path, ...]
    tables: tuple[Path, ...]


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one git subcommand, never raising on a non-zero exit.

    Args:
        args: The subcommand and its arguments.
        cwd: Where to run it.

    Returns:
        The completed process. A non-zero exit is an ordinary answer here --
        "not a repository", "no upstream" -- and each caller reads
        ``returncode`` itself.
    """
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")


def _code_root() -> Path:
    """The directory to resolve git provenance from: the installed package.

    Returns:
        The directory containing ``prismabib/``.

    Not the *project* directory, and the distinction is the whole point.
    BUILD_PLAN line 450: the SHA in ``manifest.json`` is *"the same SHA"* a
    GitHub Release tag points at, *"what makes an exported number traceable to
    a published state of the code"*. A researcher's ``projects/<slug>/`` is
    usually not a git repository at all -- and when it is, it is *their*
    repository, whose HEAD says nothing about which prismabib produced the
    numbers. Resolving from the project root recorded ``commit: null`` for
    every ordinary review while a perfectly good code SHA sat one directory
    away.
    """
    import prismabib

    return Path(prismabib.__file__).resolve().parent


def _git_provenance(cwd: Path) -> dict[str, Any]:
    """The git facts a reader needs to resolve an exported number.

    Args:
        cwd: A directory inside the repository whose commit is being
            recorded -- :func:`_code_root`, not the project.

    Returns:
        ``commit``, ``dirty`` and ``commit_is_pushed``. When prismabib was
        installed from a wheel rather than a checkout there is no repository
        to ask, so ``commit`` is ``None`` and ``dirty`` is ``True``:
        "unknown" must never render as "clean", and ``package_version``
        carries the provenance in that case.
    """
    head = _git(["rev-parse", "HEAD"], cwd=cwd)
    if head.returncode != 0:
        return {"commit": None, "dirty": True, "commit_is_pushed": False}

    commit = head.stdout.strip()
    status = _git(["status", "--porcelain"], cwd=cwd)
    dirty = status.returncode != 0 or bool(status.stdout.strip())

    # `branch -r --contains` is empty when no remote branch contains the
    # commit, which is exactly "a reader cannot fetch this".
    contains = _git(["branch", "-r", "--contains", commit], cwd=cwd)
    pushed = contains.returncode == 0 and bool(contains.stdout.strip())

    return {"commit": commit, "dirty": dirty, "commit_is_pushed": pushed}


def _package_version() -> str:
    """The installed package version.

    Returns:
        ``prismabib.__version__``, which is derived from the git tag by
        hatch-vcs. BUILD_PLAN line 1407 requires it in the manifest beside
        the SHA: together they say which released state produced a number.
    """
    from prismabib import __version__

    return __version__


def _run_ids(project: Project) -> tuple[str, ...]:
    """Every sealed Layer 0 run the store was built from.

    Args:
        project: The project being exported.

    Returns:
        Run ids in sorted order, which is chronological by construction.
    """
    connection = connect(project, read_only=True)
    try:
        rows = connection.execute("SELECT run_id FROM runs ORDER BY run_id").fetchall()
    finally:
        connection.close()
    return tuple(str(row[0]) for row in rows)


def _build_manifest(project: Project, counts: FlowCounts) -> dict[str, Any]:
    """Assemble ``manifest.json``.

    Args:
        project: The project being exported.
        counts: Its flow counts, whose ``included`` is recorded so the
            manifest alone answers "how big was the review".

    Returns:
        The manifest mapping, with keys sorted at write time.
    """
    provenance = _git_provenance(_code_root())
    return {
        "project": project.slug,
        "criteria_version": project.criteria.version,
        "run_ids": list(_run_ids(project)),
        "package_version": _package_version(),
        "git_commit": provenance["commit"],
        "dirty": provenance["dirty"],
        "commit_is_pushed": provenance["commit_is_pushed"],
        "included": counts.included,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _flow_source_rows(counts: FlowCounts) -> Table:
    """The diagram's numbers, as the CSV that ships beside it.

    S10-AC1 requires every figure to have a sibling source CSV, and
    ``test_export__source_csv__reproduces_the_figure_values`` requires that
    CSV to *reproduce* the figure rather than merely accompany it. Building
    both from one :class:`FlowCounts` is what makes that hold by construction.

    Args:
        counts: The counts rendered in the diagram.

    Returns:
        A stage/count table covering every number on the diagram.
    """
    import dataclasses

    rows: list[tuple[Any, ...]] = [
        (field.name, getattr(counts, field.name))
        for field in dataclasses.fields(counts)
        if isinstance(getattr(counts, field.name), int)
    ]
    rows.extend(
        (f"excluded_fulltext.{code}", n) for code, n in sorted(counts.excluded_fulltext.items())
    )
    # The `isinstance(..., int)` sweep above skips both mapping fields, so each
    # needs its own line here. Without this one the CSV held four fewer numbers
    # than the figure it is supposed to reproduce, and the round-trip test could
    # not see it: that test walks CSV -> SVG, so a number present in the figure
    # and absent from the CSV is invisible to it (S10-AC1).
    rows.extend(
        (f"excluded_automated.{reason}", n)
        for reason, n in sorted(counts.excluded_automated_by_reason.items())
    )
    return Table(
        slug="prisma_flow",
        caption="PRISMA 2020 flow counts",
        columns=("stage", "count"),
        rows=tuple(rows),
    )


def _write(path: Path, text: str) -> Path:
    """Write ``text`` to ``path``, creating parents.

    Args:
        path: Destination.
        text: Content, written UTF-8 with ``\\n`` endings on every platform so
            two machines produce identical bytes.

    Returns:
        ``path``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def export_project(project: Project) -> ExportResult:
    """Write every citable artefact for ``project`` into ``exports/``.

    Args:
        project: The project to export. Its flow counts are recomputed here
            rather than accepted as an argument, so an export can never
            report numbers that the current store and decision log do not
            support.

    Returns:
        An :class:`ExportResult` describing what was written.

    Raises:
        ValidationError: If the flow counts do not close, or if any number
            is not a JSON scalar. An export that wrote an inconsistent
            diagram would be the §1.4 failure with a provenance file
            attached.
        StoreError: If no Layer 1 store exists yet.
    """
    counts = compute_flow_counts(project)
    counts.assert_consistent()

    root = project.root / "exports"
    numbers = numbers_map(project, counts=counts)
    manifest = _build_manifest(project, counts)

    if manifest["dirty"]:
        _log.warning(
            "report.export.dirty_working_tree",
            commit=manifest["git_commit"],
            detail=(
                "exporting from a working tree with uncommitted changes: the recorded "
                "commit does not describe the code that produced these numbers"
            ),
        )
    if manifest["git_commit"] is not None and not manifest["commit_is_pushed"]:
        _log.warning(
            "report.export.commit_not_pushed",
            commit=manifest["git_commit"],
            detail=(
                "the recorded commit is not on any remote branch, so a reader cannot "
                "fetch the code these numbers came from"
            ),
        )

    figures: list[Path] = []
    figures.append(
        _write(
            root / "figures" / "prisma_flow.svg",
            flow_diagram_svg(counts, title=project.title),
        )
    )
    figures.append(_write(root / "figures" / "prisma_flow.csv", to_csv(_flow_source_rows(counts))))

    tables: list[Path] = []
    for table in build_tables(project, numbers):
        tables.append(_write(root / "tables" / f"{table.slug}.csv", to_csv(table)))
        tables.append(_write(root / "tables" / f"{table.slug}.md", to_markdown(table)))
        tables.append(_write(root / "tables" / f"{table.slug}.tex", to_latex(table)))

    _write(root / "numbers.json", json.dumps(numbers, indent=2, sort_keys=True) + "\n")
    _write(root / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    _log.info(
        "report.export.complete",
        project=project.slug,
        figures=len(figures),
        tables=len(tables),
        numbers=len(numbers),
        dirty=manifest["dirty"],
    )
    return ExportResult(
        root=root,
        numbers=numbers,
        manifest=manifest,
        figures=tuple(sorted(figures)),
        tables=tuple(sorted(tables)),
    )


__all__ = ["VOLATILE_MANIFEST_KEYS", "ExportResult", "export_project"]
