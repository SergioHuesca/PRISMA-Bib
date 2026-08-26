"""Resolving ``criteria.yaml`` across protocol amendments (BUILD_PLAN §Stage 4).

BUILD_PLAN's "Criteria amendment support" paragraph (lines 999-1000) says
only that ``engine.replay(criteria_version=...)`` "recomputes membership
under a different criteria file" -- it does not say *where* a superseded
``criteria.yaml`` lives once a newer version has overwritten it on disk.

**Orchestrator ruling for this stage: criteria history is git-only.** The
frozen §2.3 project skeleton (``project.py``'s module docstring) is not
extended with a per-version archive directory (e.g.
``criteria/1.0.0.yaml``, ``criteria/1.1.0.yaml``, ...) that this module
would then have to keep in sync with ``criteria.yaml`` on every amendment --
a second, driftable copy of exactly the kind this codebase's other modules
go out of their way to avoid (see ``store/load.py``'s docstring on why the
richer dedup key is not reimplemented in SQL). Instead, a superseded
version is resolved from ``criteria.yaml``'s own git history: whichever git
repository the project directory lives in is the single, already-existing,
already-trusted record of every version the file has ever held, with a
timestamp, an author, and a commit message explaining the amendment for
free.

This does mean a project directory that is not (yet) a git repository, or
whose ``criteria.yaml`` was never committed at the version being asked for,
cannot have that version replayed -- :func:`resolve_criteria` raises
:class:`~prismabib.errors.ConfigError` loudly in that case, naming the
requested version, rather than silently falling back to the current file or
inventing a placeholder. A protocol amendment is exactly the kind of event
BUILD_PLAN's overall philosophy (§2.2, §2.5) treats as needing an audit
trail; asking that the trail already exist, rather than reconstructing one
prismabib cannot vouch for, is consistent with that.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import ConfigError
from prismabib.project import Criteria, Project

#: Generous but bounded -- a hung `git` subprocess must not hang prismabib
#: indefinitely. Every call this module makes (`rev-parse`, `log`, `show`)
#: is a fast, local, read-only git operation with no network I/O, so this
#: is far more time than any of them should ever need.
_GIT_TIMEOUT_SECONDS = 30

#: Prepended to every `git` invocation this module makes.
#:
#: `log.showSignature = true` in an operator's global gitconfig makes
#: `git log --format=%H` interleave signature-verification lines ("gpg:
#: Signature made ...") with the hashes, and `_git_log_hashes` would take
#: each of those for a commit hash and hand it to `git show`. Turning it off
#: per-invocation is what keeps this module's output a function of the
#: repository rather than of the machine's git configuration -- the same
#: reproducibility requirement Stage 11 states as "a clean clone on a
#: different machine".
_GIT_CONFIG_OVERRIDES: Final[tuple[str, ...]] = ("-c", "log.showSignature=false")

#: Environment variables that would silently redirect `git` at a *different*
#: repository than `cwd`. `GIT_DIR`/`GIT_WORK_TREE` in particular make
#: `rev-parse --show-toplevel` answer for whatever repository they name, so a
#: criteria version could be resolved from someone else's history -- and be
#: reported as this project's. They are dropped for the child process.
#:
#: Deliberately *not* a wholesale environment reset: unlike the test helper
#: in `tests/prisma_helpers.py`, this runs against a real operator's
#: repository, where global config can carry the `safe.directory` entry that
#: makes reading it possible at all. Only the variables that change *which
#: repository is read* are removed.
_GIT_REPOSITORY_ENV_VARS: Final[tuple[str, ...]] = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_NAMESPACE",
)


def resolve_criteria(project: Project, criteria_version: str) -> Criteria:
    """Resolve one ``criteria.yaml`` version, current or historical.

    Args:
        project: The project whose ``criteria.yaml`` (and, for a
            historical version, git history) to resolve against.
        criteria_version: The ``criteria.yaml`` ``version`` string to
            resolve -- typically a value already seen in a
            :class:`~prismabib.prisma.events.DecisionEvent`'s
            ``criteria_version`` field, or the target of a planned
            amendment.

    Returns:
        The :class:`~prismabib.project.Criteria` in force under
        ``criteria_version``. If it equals ``project.criteria.version``,
        this is exactly ``project.criteria`` (read fresh from disk, no git
        lookup performed) -- the common, cheap case of asking about the
        version already in effect. Otherwise, it is parsed from the most
        recent git commit (in ``git log``'s default, reverse-chronological
        order) whose ``criteria.yaml`` blob declares that version.

    Raises:
        ConfigError: If ``criteria_version`` is not the project's current
            version and either ``project.root`` is not inside a git
            repository, ``criteria.yaml`` has no git history there, or no
            commit in that history declares ``criteria_version``. Every
            message names the requested version explicitly, per this
            stage's "if a requested version cannot be resolved, raise
            loudly naming it" instruction.
    """
    current = project.criteria
    if criteria_version == current.version:
        return current
    return _resolve_from_git_history(project, criteria_version, current_version=current.version)


def _resolve_from_git_history(
    project: Project, criteria_version: str, *, current_version: str
) -> Criteria:
    """The historical half of :func:`resolve_criteria` (only reached for a non-current version).

    Args:
        project: The project whose ``criteria.yaml`` to search.
        criteria_version: The version being searched for.
        current_version: ``project.criteria.version``, already read by the
            caller -- threaded through rather than re-read here purely for
            an error message, so a failed resolution costs exactly one
            ``criteria.yaml`` parse, not two.

    Returns:
        The parsed, validated :class:`~prismabib.project.Criteria` from the
        most recent matching commit.

    Raises:
        ConfigError: See :func:`resolve_criteria`.
    """
    criteria_path = (project.root / "criteria.yaml").resolve()
    top_level = _git_top_level(project.root)
    if top_level is None:
        raise ConfigError(
            f"criteria_version {criteria_version!r} is not the project's current "
            f"version ({current_version!r}), and {project.root} is not "
            "inside a git repository. Criteria history is resolved from git "
            "history only (no per-version archive directory exists) -- commit "
            "criteria.yaml to a git repository to make prior versions replayable."
        )
    try:
        relative_path = criteria_path.relative_to(top_level)
    except ValueError as exc:
        raise ConfigError(
            f"criteria_version {criteria_version!r} could not be resolved: "
            f"{criteria_path} does not appear to live inside the git repository "
            f"rooted at {top_level}: {exc}"
        ) from exc

    commit_hashes = _git_log_hashes(top_level, relative_path)
    if not commit_hashes:
        raise ConfigError(
            f"criteria_version {criteria_version!r} could not be resolved: "
            f"{criteria_path} has no git history under {top_level}."
        )
    for commit_hash in commit_hashes:
        candidate = _criteria_at_commit(top_level, commit_hash, relative_path)
        if candidate is not None and candidate.version == criteria_version:
            return candidate
    raise ConfigError(
        f"criteria_version {criteria_version!r} was not found anywhere in "
        f"{relative_path}'s git history under {top_level} "
        f"(searched {len(commit_hashes)} commit(s))."
    )


def _criteria_at_commit(top_level: Path, commit_hash: str, relative_path: Path) -> Criteria | None:
    """Parse ``criteria.yaml`` as it stood at one commit, tolerating an unparsable one.

    Args:
        top_level: The git repository's top-level directory.
        commit_hash: The commit to read ``relative_path`` from.
        relative_path: ``criteria.yaml``'s path relative to ``top_level``.

    Returns:
        The parsed :class:`~prismabib.project.Criteria`, or ``None`` if
        that commit's blob does not exist at ``relative_path``, is not
        valid YAML, or does not validate as
        :class:`~prismabib.project.Criteria`. A commit predating the
        current schema (e.g. before a required field was added) is a real
        possibility in a long-lived project's history and must not abort
        the search for a different, earlier commit that *does* still
        parse and declares the requested version.
    """
    content = _git_show(top_level, commit_hash, relative_path)
    if content is None:
        return None
    try:
        raw = yaml.safe_load(content)
        return Criteria.model_validate(raw or {})
    except (yaml.YAMLError, PydanticValidationError):
        return None


def _run_git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run one ``git`` subcommand, never raising on a non-zero exit.

    Args:
        args: The ``git`` subcommand and its arguments (without the
            leading ``"git"`` itself).
        cwd: The directory to run it in.

    Returns:
        The completed process; every caller inspects ``returncode`` itself
        rather than this function deciding what counts as failure --
        "not a git repository" and "file not found at this commit" are
        both ordinary, expected non-zero exits here, not exceptions.

    Raises:
        ConfigError: If the ``git`` executable itself cannot be found on
            ``PATH``.
    """
    try:
        return subprocess.run(
            ["git", *_GIT_CONFIG_OVERRIDES, *args],
            cwd=cwd,
            env=_git_environment(),
            capture_output=True,
            text=True,
            # Explicit, never `locale.getencoding()`. `criteria.yaml` is
            # written UTF-8 by this codebase (every other file read passes
            # `encoding="utf-8"`), and a criteria file containing so much as
            # a `conference_whitelist: ["Congres Europeen"]` with an accent
            # would otherwise raise `UnicodeDecodeError` under `LC_ALL=C` --
            # or, under a non-UTF-8 8-bit locale, decode *silently wrong*,
            # changing which records match the whitelist, changing `A`, and
            # changing the published `excluded_automated`.
            encoding="utf-8",
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ConfigError(
            "git is not available on PATH; criteria history is resolved from git "
            f"history only and could not run `git {' '.join(args)}`: {exc}"
        ) from exc


def _git_environment() -> dict[str, str]:
    """The environment every ``git`` subprocess here runs under.

    Returns:
        A copy of this process's environment with
        :data:`_GIT_REPOSITORY_ENV_VARS` removed, so an ambient ``GIT_DIR``
        or ``GIT_WORK_TREE`` cannot point a lookup at a repository other
        than the one containing ``cwd``. Everything else (``PATH``,
        ``HOME``, and therefore the operator's global config) is inherited
        unchanged.
    """
    environment = dict(os.environ)
    for name in _GIT_REPOSITORY_ENV_VARS:
        environment.pop(name, None)
    return environment


def _git_top_level(directory: Path) -> Path | None:
    """The git repository ``directory`` lives in, if any.

    Args:
        directory: A candidate directory, not necessarily itself the
            repository root.

    Returns:
        The repository's top-level directory, or ``None`` if ``directory``
        is not inside a git repository (or ``directory`` does not exist).
    """
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=directory)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip())


def _git_log_hashes(top_level: Path, relative_path: Path) -> list[str]:
    """Every commit touching ``relative_path``, most recent first.

    Args:
        top_level: The git repository's top-level directory.
        relative_path: The file's path relative to ``top_level``.

    Returns:
        Commit hashes in ``git log``'s default (reverse-chronological)
        order, following renames (``--follow``, so a ``criteria.yaml``
        that was moved or renamed mid-project still resolves). Empty if
        the file has no history in this repository.
    """
    result = _run_git(["log", "--follow", "--format=%H", "--", str(relative_path)], cwd=top_level)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _git_show(top_level: Path, commit_hash: str, relative_path: Path) -> str | None:
    """One file's content as of one commit.

    Args:
        top_level: The git repository's top-level directory.
        commit_hash: The commit to read from.
        relative_path: The file's path relative to ``top_level``.

    Returns:
        The file's content at that commit, or ``None`` if ``relative_path``
        did not exist in the tree at ``commit_hash``.
    """
    result = _run_git(["show", f"{commit_hash}:{relative_path.as_posix()}"], cwd=top_level)
    if result.returncode != 0:
        return None
    return result.stdout


__all__ = ["resolve_criteria"]
