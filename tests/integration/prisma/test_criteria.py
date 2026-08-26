"""Integration tests for :mod:`prismabib.prisma.criteria` (BUILD_PLAN §Stage 4, line 995).

``engine.replay(criteria_version=...)`` can only recompute membership under a
superseded ``criteria.yaml`` if that version can still be found. This module
owns the *finding*: the git-history resolution
:func:`~prismabib.prisma.criteria.resolve_criteria` performs, and -- far more
importantly -- every way it can fail. A protocol amendment that cannot be
replayed must say so, naming the version asked for; silently falling back to
the current criteria would answer a question about the past with the
present.

Real ``git`` subprocesses against real, disposable repositories created under
``tmp_path``. No network (``git`` here never leaves the filesystem), and no
patched ``prismabib`` symbol.
"""

from __future__ import annotations

import locale
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismabib.errors import ConfigError
from prismabib.prisma.criteria import resolve_criteria
from prismabib.project import Project
from tests.prisma_helpers import CriteriaSpec, commit_criteria, run_git, write_criteria

#: A ``conference_whitelist`` entry that only survives a UTF-8 round trip.
#: Chosen to be the kind of value a real protocol carries -- a
#: French-language venue -- rather than a decorative one, because getting it
#: wrong is not cosmetic: the whitelist is substring-matched against
#: ``venues.name`` in :func:`prismabib.prisma.engine._passes_conference_whitelist`,
#: so a mangled entry silently changes ``A`` and with it the published
#: ``excluded_automated``.
NON_ASCII_VENUE = "Congrès Européen de Recherche"


@pytest.fixture
def ascii_locale() -> Iterator[None]:
    """Run the test body under an ASCII (``C``) ``LC_CTYPE``.

    This is the OS boundary, not a ``prismabib`` internal (§3.7.3 rule 1):
    ``LC_CTYPE=C`` is an ordinary state for a CI runner, a cron job, or a
    ``docker run`` without a locale, and it is exactly what
    ``subprocess.run(..., text=True)`` consults to decide how to decode a
    child process's output *when the caller does not pin an encoding*. Under
    it, an unpinned ``git show`` of a UTF-8 ``criteria.yaml`` raises
    ``UnicodeDecodeError``; under a non-UTF-8 8-bit locale it would decode
    silently wrong instead, which is worse. Without this fixture the test
    below would pass on any UTF-8 developer machine whether or not the
    encoding is pinned, and so would assert nothing.
    """
    previous = locale.setlocale(locale.LC_CTYPE)
    locale.setlocale(locale.LC_CTYPE, "C")
    yield
    locale.setlocale(locale.LC_CTYPE, previous)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A bare project skeleton with ``criteria.yaml`` at version 1.0.0."""
    project = Project.init("criteria", title="Criteria", root=tmp_path)
    write_criteria(project, CriteriaSpec(version="1.0.0"))
    return project


@pytest.mark.integration
def test_resolve_criteria__the_current_version__is_read_from_the_file_not_from_git(
    project: Project,
) -> None:
    resolved = resolve_criteria(project, "1.0.0")

    assert resolved == project.criteria
    assert not (project.root / ".git").exists()


@pytest.mark.integration
def test_resolve_criteria__superseded_version__is_read_from_git_history(
    project: Project,
) -> None:
    commit_criteria(project, CriteriaSpec(version="1.0.0", year_start=1990), "v1.0.0")
    commit_criteria(project, CriteriaSpec(version="2.0.0", year_start=2020), "v2.0.0")

    resolved = resolve_criteria(project, "1.0.0")

    assert (resolved.version, resolved.temporal.year_start) == ("1.0.0", 1990)
    assert project.criteria.version == "2.0.0"


@pytest.mark.integration
def test_resolve_criteria__non_ascii_criteria_under_an_ascii_locale__round_trips_intact(
    project: Project, ascii_locale: None
) -> None:
    commit_criteria(
        project,
        CriteriaSpec(version="1.0.0", conference_whitelist=(NON_ASCII_VENUE, "CVPR")),
        "v1.0.0",
    )
    committed_bytes = (project.root / "criteria.yaml").read_bytes()
    commit_criteria(project, CriteriaSpec(version="2.0.0"), "v2.0.0")

    resolved = resolve_criteria(project, "1.0.0")

    assert resolved.doc_types.conference_whitelist == [NON_ASCII_VENUE, "CVPR"]
    # The test's own premise, asserted rather than assumed: the blob git is
    # asked for really does hold multi-byte UTF-8. Written with ASCII escapes
    # instead, no byte above 0x7f would ever cross the pipe and this test
    # would pass under any encoding at all.
    assert NON_ASCII_VENUE.encode("utf-8") in committed_bytes


@pytest.mark.integration
def test_resolve_criteria__superseded_version_outside_a_git_repository__raises(
    project: Project,
) -> None:
    with pytest.raises(ConfigError, match="not inside a git repository") as excinfo:
        resolve_criteria(project, "0.9.0")

    assert "'0.9.0'" in str(excinfo.value)


@pytest.mark.integration
def test_resolve_criteria__repository_with_no_commits_at_all__raises_naming_the_version(
    project: Project,
) -> None:
    run_git(project.root, "init", "--quiet", "--initial-branch=main")

    with pytest.raises(ConfigError, match="has no git history") as excinfo:
        resolve_criteria(project, "0.9.0")

    assert "'0.9.0'" in str(excinfo.value)


@pytest.mark.integration
def test_resolve_criteria__criteria_yaml_never_committed__raises(project: Project) -> None:
    run_git(project.root, "init", "--quiet", "--initial-branch=main")
    (project.root / "README.md").write_text("unrelated\n", encoding="utf-8")
    run_git(project.root, "add", "README.md")
    run_git(project.root, "commit", "--quiet", "--message", "unrelated")

    with pytest.raises(ConfigError, match="has no git history"):
        resolve_criteria(project, "0.9.0")


@pytest.mark.integration
def test_resolve_criteria__version_absent_from_history__raises_naming_the_search(
    project: Project,
) -> None:
    commit_criteria(project, CriteriaSpec(version="1.0.0"), "v1.0.0")
    commit_criteria(project, CriteriaSpec(version="2.0.0"), "v2.0.0")

    with pytest.raises(ConfigError, match=r"searched 2 commit\(s\)") as excinfo:
        resolve_criteria(project, "0.9.0")

    assert "'0.9.0'" in str(excinfo.value)


@pytest.mark.integration
def test_resolve_criteria__unparsable_commit_in_history__is_skipped_not_fatal(
    project: Project,
) -> None:
    commit_criteria(project, CriteriaSpec(version="1.0.0", year_start=1990), "v1.0.0")
    (project.root / "criteria.yaml").write_text("version: [unclosed\n", encoding="utf-8")
    run_git(project.root, "add", "criteria.yaml")
    run_git(project.root, "commit", "--quiet", "--message", "broken YAML")
    commit_criteria(project, CriteriaSpec(version="2.0.0"), "v2.0.0")

    resolved = resolve_criteria(project, "1.0.0")

    assert (resolved.version, resolved.temporal.year_start) == ("1.0.0", 1990)


@pytest.mark.integration
def test_resolve_criteria__commit_failing_schema_validation__is_skipped_not_fatal(
    project: Project,
) -> None:
    commit_criteria(project, CriteriaSpec(version="1.0.0", year_start=1990), "v1.0.0")
    (project.root / "criteria.yaml").write_text("version: not-a-semver\n", encoding="utf-8")
    run_git(project.root, "add", "criteria.yaml")
    run_git(project.root, "commit", "--quiet", "--message", "schema-invalid criteria")
    commit_criteria(project, CriteriaSpec(version="2.0.0"), "v2.0.0")

    resolved = resolve_criteria(project, "1.0.0")

    assert resolved.temporal.year_start == 1990


@pytest.mark.integration
def test_resolve_criteria__commit_that_deleted_criteria_yaml__is_skipped_not_fatal(
    project: Project,
) -> None:
    commit_criteria(project, CriteriaSpec(version="1.0.0", year_start=1990), "v1.0.0")
    (project.root / "criteria.yaml").unlink()
    run_git(project.root, "rm", "--quiet", "criteria.yaml")
    run_git(project.root, "commit", "--quiet", "--message", "remove criteria.yaml")
    commit_criteria(project, CriteriaSpec(version="2.0.0"), "v2.0.0")

    resolved = resolve_criteria(project, "1.0.0")

    assert resolved.temporal.year_start == 1990


@pytest.mark.integration
def test_resolve_criteria__criteria_yaml_symlinked_outside_the_repository__raises(
    project: Project, tmp_path: Path
) -> None:
    # A plausible sharing arrangement -- one methodology file symlinked into
    # several projects -- that git history cannot answer for, because the file
    # git knows about is not the file being read.
    commit_criteria(project, CriteriaSpec(version="1.0.0"), "v1.0.0")
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "criteria.yaml").write_text(CriteriaSpec(version="3.0.0").to_yaml(), encoding="utf-8")
    (project.root / "criteria.yaml").unlink()
    (project.root / "criteria.yaml").symlink_to(shared / "criteria.yaml")

    with pytest.raises(ConfigError, match="does not appear to live inside the git repository"):
        resolve_criteria(project, "1.0.0")


@pytest.mark.integration
def test_resolve_criteria__git_not_on_path__raises_config_error(
    project: Project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The OS boundary, not a `prismabib` internal (§3.7.3 rule 1): an empty
    # PATH is exactly the state a machine without git installed is in.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    with pytest.raises(ConfigError, match="git is not available on PATH"):
        resolve_criteria(project, "0.9.0")
