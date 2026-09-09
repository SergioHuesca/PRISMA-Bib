"""The export bundle, against a real store and a real decision log.

BUILD_PLAN §Stage 10's acceptance criteria live here: every figure carries its
source CSV (S10-AC1), the manifest carries a git SHA and flags a dirty tree
(S10-AC2), and the diagram's numbers equal ``FlowCounts`` (S10-AC4).
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from prismabib.prisma.flow import compute_flow_counts
from prismabib.report.export import export_project
from tests.integration.report.test_flow_diagram import FIELD_TO_LABEL, box_text
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from prismabib.project import Project

CORPUS = CorpusSpec(
    records=[RecordSpec(number=n, cited_by_count=n * 3) for n in range(1, 13)],
    criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC", "REVIEW_OR_SURVEY")),
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A freshly loaded project with a Layer 1 store."""
    return build_project(tmp_path, CORPUS, slug="export-me")


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC1")
def test_export__every_figure__has_a_sibling_source_csv(project: Project) -> None:
    """A figure a reader cannot check the numbers of is a picture, not evidence.

    Walks the written directory rather than the returned paths, so a figure
    that reached disk by some route the result object does not describe is
    still held to the rule.
    """
    result = export_project(project)

    # Every file that is not itself a source CSV, rather than `*.svg`.
    # Globbing one extension meant a figure written in any other format --
    # the PNG BUILD_PLAN Stage 10 specifies and ADR 0026 defers -- would
    # escape S10-AC1 entirely, silently, on the day it was added. The
    # criterion is about figures, not about SVG.
    figures = sorted(
        path
        for path in (result.root / "figures").iterdir()
        if path.is_file() and path.suffix != ".csv"
    )
    assert figures, "the export wrote no figures at all"
    for figure in figures:
        assert figure.with_suffix(".csv").is_file(), f"{figure.name} has no sibling source CSV"


#: Reason key -> the label the figure introduces its count with. Transcribed
#: by hand rather than imported from `flow_diagram._AUTOMATED_REASON_LABELS`:
#: an expectation that restates the thing under test agrees with itself, and
#: a swapped label would still pass.
REASON_TO_LABEL_TEXT = {
    "year": "by publication year",
    "subject_area": "by subject area",
    "doc_type": "by document type",
    "venue": "by conference whitelist",
}


@pytest.mark.integration
def test_export__source_csv__reproduces_the_figure_values(project: Project) -> None:
    """A CSV that has drifted from its figure is worse than no CSV.

    Every count in the CSV is looked up in the SVG text, so a renderer that
    started computing its own numbers -- rather than placing the ones it was
    given -- fails here even though both files would still exist.
    """
    result = export_project(project)
    counts = compute_flow_counts(project)

    rows = list(csv.reader(io.StringIO((result.root / "figures" / "prisma_flow.csv").read_text())))
    header, *body = rows
    assert header == ["stage", "count"]

    svg = (result.root / "figures" / "prisma_flow.svg").read_text(encoding="utf-8")
    for stage, value in body:
        if "." in stage:  # excluded_fulltext.<CODE>
            continue
        assert int(value) == getattr(counts, stage), f"{stage} differs from FlowCounts"
        # Anchored to the field's own label, not searched for across the whole
        # document: `value in svg` matches any integer anywhere, including the
        # x/y geometry, which is the inert form this project's diagram test
        # already had to be rewritten away from twice.
        box_id, template = FIELD_TO_LABEL[stage]
        assert re.search(template.format(value=int(value)), box_text(svg, box_id)), (
            f"{stage}={value} is in the CSV but not under its label on the diagram"
        )


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC2")
def test_export__manifest__contains_git_sha_and_versions(project: Project) -> None:
    """The manifest is what makes an exported number resolvable to code.

    The SHA is asserted to be a real 40-character object *and* to be the one
    the repository actually has, not merely well-formed: a plausible-looking
    constant would satisfy a regex and resolve to nothing.
    """
    result = export_project(project)

    manifest = json.loads((result.root / "manifest.json").read_text(encoding="utf-8"))
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["git_commit"] or "")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    )
    assert manifest["git_commit"] == head.stdout.strip()
    assert manifest["package_version"]
    assert manifest["criteria_version"] == project.criteria.version
    assert manifest["run_ids"], "an export with no run ids cannot be traced to a capture"
    assert set(manifest) >= {"dirty", "commit_is_pushed", "exported_at", "project"}


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC2")
def test_export__dirty_working_tree__warns_and_marks_dirty_true(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dirty tree means the recorded SHA does not describe what ran.

    A throwaway repository is created and dirtied, and the exporter is pointed
    at it through its own ``_code_root`` seam -- the OS boundary, not a
    prismabib internal being reached into. Dirtying the real working tree
    would be a test that mutates the checkout it runs from.
    """
    from prismabib.report import export as export_module

    repo = tmp_path / "fake-code"
    repo.mkdir()
    for args in (["init", "--quiet", "--initial-branch=main"],):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "committed.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "committed.py").write_text("x = 2  # uncommitted\n", encoding="utf-8")

    monkeypatch.setattr(export_module, "_code_root", lambda: repo)
    result = export_project(project)

    assert result.manifest["dirty"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", result.manifest["git_commit"])


@pytest.mark.integration
def test_export__clean_tree__marks_dirty_false(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive control.

    Without it, ``dirty: true`` could be a constant and the test above would
    still pass -- which is the shape of vacuous test §5 risk 12 warns about.
    """
    from prismabib.report import export as export_module

    repo = tmp_path / "clean-code"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "committed.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(export_module, "_code_root", lambda: repo)
    result = export_project(project)

    assert result.manifest["dirty"] is False


@pytest.mark.integration
def test_export__commit_on_no_remote_branch__is_flagged_separately(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unpushed commit is untraceable, and ``dirty`` does not cover it.

    BUILD_PLAN calls this the same defect class as a dirty tree and notes that
    the dirty flag catches only the second. A clean local repository with no
    remote is exactly that state.
    """
    from prismabib.report import export as export_module

    repo = tmp_path / "unpushed"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    monkeypatch.setattr(export_module, "_code_root", lambda: repo)
    result = export_project(project)

    assert result.manifest["dirty"] is False
    assert result.manifest["commit_is_pushed"] is False


@pytest.mark.integration
def test_export__outside_a_git_repository__reports_unknown_as_dirty(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Unknown provenance" must never render as "clean".

    Exporting from an unpacked tarball is legitimate, and the manifest has to
    say that the SHA is unavailable rather than imply a clean checkout.
    """
    from prismabib.report import export as export_module

    monkeypatch.setattr(export_module, "_code_root", lambda: tmp_path)
    result = export_project(project)

    assert result.manifest["git_commit"] is None
    assert result.manifest["dirty"] is True


@pytest.mark.integration
def test_export__inconsistent_counts__refuses_to_write(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An export that wrote an unbalanced diagram is §1.4 with a manifest attached.

    The counts are made inconsistent at the boundary the exporter reads them
    from, so this asserts the exporter's own guard rather than re-testing
    ``assert_consistent``.
    """
    import dataclasses

    from prismabib.errors import ValidationError
    from prismabib.report import export as export_module

    real = compute_flow_counts(project)
    broken = dataclasses.replace(real, included=real.included + 5)
    monkeypatch.setattr(export_module, "compute_flow_counts", lambda _p: broken)

    with pytest.raises(ValidationError, match="inconsistent"):
        export_project(project)


@pytest.mark.integration
def test_export__cli_summary__prints_none_placeholder_not_the_word_None(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outside a repository the summary must say ``(none)``, not ``None``.

    ``str(commit)[:12] or "(none)"`` looks like a fallback and is not one:
    ``str(None)`` is ``"None"``, which is truthy, so the CLI printed the word
    "None" in exactly the case the fallback existed to explain.
    """
    from typer.testing import CliRunner

    from prismabib.cli import app
    from prismabib.report import export as export_module

    monkeypatch.setattr(export_module, "_code_root", lambda: tmp_path)
    result = CliRunner().invoke(app, ["export", project.slug, "--root", str(project.root.parent)])

    assert result.exit_code == 0
    assert "(none)" in result.output
    assert "git commit              None" not in result.output


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC1")
def test_export__source_csv__carries_every_automated_reason_the_figure_shows(
    project: Project,
) -> None:
    """The reason breakdown reaches the CSV, not just the figure.

    ``test_export__source_csv__reproduces_the_figure_values`` walks CSV -> SVG,
    so a number the figure shows and the CSV omits is invisible to it -- and
    that is exactly what happened when ADR 0016 added a mapping field: the
    ``isinstance(..., int)`` sweep that builds the rows skipped it, and the
    CSV shipped four numbers short of the figure it exists to reproduce.

    This walks the other direction.
    """
    result = export_project(project)
    counts = compute_flow_counts(project)

    rows = list(csv.reader(io.StringIO((result.root / "figures" / "prisma_flow.csv").read_text())))
    body = dict(rows[1:])
    in_csv = {stage: n for stage, n in body.items() if stage.startswith("excluded_automated.")}

    assert in_csv == {
        f"excluded_automated.{reason}": str(count)
        for reason, count in counts.excluded_automated_by_reason.items()
    }

    svg = (result.root / "figures" / "prisma_flow.svg").read_text(encoding="utf-8")
    automated_text = box_text(svg, "after-automated")
    for reason, count in counts.excluded_automated_by_reason.items():
        assert re.search(rf"{REASON_TO_LABEL_TEXT[reason]}: {count}(?!\d)", automated_text), (
            f"{reason}={count} is in the CSV but not under its label on the diagram"
        )


# ---------------------------------------------------------------------------
# `commit_is_pushed` -- issue #25 §1. The only existing test builds a repo
# with *no remote*, where `git branch -r --contains` returns empty whatever
# the implementation does: hard-coding `commit_is_pushed = False` passed the
# whole suite. These exercise `_git_provenance` directly, which needs no
# monkeypatching of `prismabib.*` (§3.7.3) because it already takes `cwd`.
# ---------------------------------------------------------------------------


def _repo_with_bare_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A git repo with one commit pushed to a local bare remote.

    Args:
        tmp_path: The test's temporary directory.

    Returns:
        ``(repo, remote)``. A *local* bare remote rather than a network one:
        `git branch -r` reads remote-tracking refs, so what is being tested
        is the ref bookkeeping, and nothing here needs a server.
    """
    remote = tmp_path / "remote.git"
    repo = tmp_path / "work"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main", str(repo)],
        check=True,
        capture_output=True,
    )
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "push", "--quiet", "-u", "origin", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo, remote


@pytest.mark.integration
def test_git_provenance__pushed_commit__reports_true(tmp_path: Path) -> None:
    """The positive control the suite lacked.

    Without it, `commit_is_pushed` could be hard-coded `False` and every
    test still passed -- the same vacuity the `dirty` tests were correctly
    guarded against.
    """
    repo, _remote = _repo_with_bare_remote(tmp_path)

    from prismabib.report.export import _git_provenance

    assert _git_provenance(repo)["commit_is_pushed"] is True


@pytest.mark.integration
def test_git_provenance__commit_made_after_the_push__reports_false(tmp_path: Path) -> None:
    """The discriminating case: a remote exists, and this commit is not on it.

    The pre-existing negative test used a repo with no remote at all, where
    the query returns empty for a reason that has nothing to do with the
    commit. Here the remote is configured and populated, so a `False` means
    what the key claims it means.
    """
    repo, _remote = _repo_with_bare_remote(tmp_path)
    (repo / "b.py").write_text("y = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "later"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    from prismabib.report.export import _git_provenance

    assert _git_provenance(repo)["commit_is_pushed"] is False


@pytest.mark.integration
def test_git_provenance__branch_deleted_upstream__still_reports_true(tmp_path: Path) -> None:
    """A known limitation, pinned as behaviour rather than left as a caveat.

    `git branch -r --contains` reads *remote-tracking* refs, which are only
    as fresh as the last fetch. Delete the branch upstream and the stale
    local `origin/main` still contains the commit, so this reports `True`
    for a SHA no reader can now fetch.

    That is not hypothetical for this repository: `main` squash-merges and
    deletes branches, so lingering refs are the normal state. BUILD_PLAN
    asks that the manifest SHA be one that *exists on GitHub*, and this
    check cannot establish that offline -- `_git_provenance`'s docstring now
    says so rather than claiming otherwise.

    Asserting the limitation makes it a decision someone can revisit with a
    failing test in hand, instead of a sentence in a comment that the code
    may or may not still match.
    """
    repo, remote = _repo_with_bare_remote(tmp_path)
    subprocess.run(
        ["git", "--git-dir", str(remote), "update-ref", "-d", "refs/heads/main"], check=True
    )

    from prismabib.report.export import _git_provenance

    assert _git_provenance(repo)["commit_is_pushed"] is True, (
        "documents the staleness limitation; if this starts failing the check became "
        "remote-aware, and the docstring must stop hedging"
    )


@pytest.mark.integration
def test_export__stale_artefact_from_an_earlier_run__does_not_survive(
    project: Project,
) -> None:
    """A table no current code produces must not sit in the bundle looking current.

    Issue #25 §6. Nothing in `exports/` records when each file was written,
    so a leftover from an older prismabib -- a table since renamed, a figure
    since removed -- is indistinguishable from the files beside it. Worse,
    it inherits `manifest.json`'s credibility, which describes the run that
    just happened.

    The two planted files are the realistic shapes: a renamed table and a
    figure whose generator was deleted.
    """
    export_project(project)
    stale_table = project.root / "exports" / "tables" / "renamed_since.csv"
    stale_figure = project.root / "exports" / "figures" / "removed_since.svg"
    stale_table.write_text("stale,rows\n1,2\n", encoding="utf-8")
    stale_figure.write_text("<svg/>", encoding="utf-8")

    result = export_project(project)

    assert not stale_table.exists()
    assert not stale_figure.exists()
    # ...and the run that removed them still produced its own artefacts, so
    # the test cannot pass by the export having done nothing at all.
    assert result.tables and result.figures


@pytest.mark.integration
def test_export__a_researcher_s_own_file_beside_the_bundle__is_left_alone(
    project: Project,
) -> None:
    """Clearing is scoped to what this function writes, not to `exports/`.

    A reviewer may reasonably keep a manuscript or a cover letter next to
    the generated bundle. Deleting a researcher's own work is not a cost the
    staleness guarantee is worth, so the sweep covers `figures/` and
    `tables/` only.
    """
    export_project(project)
    manuscript = project.root / "exports" / "manuscript.md"
    manuscript.write_text("# Draft\n", encoding="utf-8")

    export_project(project)

    assert manuscript.read_text(encoding="utf-8") == "# Draft\n"
