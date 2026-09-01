"""Shared test-only helpers for Stage 4's ``prisma/`` suite (BUILD_PLAN §Stage 4).

Kept beside :mod:`tests.store_helpers` rather than inside it: everything here
is about *Layer 2* -- criteria files, decision-log bytes, and the small
purpose-built Layer 1 corpora the PRISMA engine's set algebra is asserted
over -- while ``store_helpers`` owns Layer 0 fixture writing. This module
composes with that one (:func:`build_project` calls ``write_sealed_run`` and
``make_entry``) rather than duplicating it.

Nothing here monkeypatches or wraps a ``prismabib.*`` symbol (§3.7.3 rule 1).
The corrupting helpers (:func:`overwrite_log_bytes`, :func:`rewrite_sidecar`,
:func:`append_raw_bytes`) all operate on the *files on disk*, exactly as a
text editor or a crashed process would -- which is the whole point: they
simulate the outside world tampering with a log, not a patched
:class:`~prismabib.prisma.log.DecisionLog`.
"""

from __future__ import annotations

import errno
import hashlib
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import yaml

from prismabib.project import Project
from tests.store_helpers import REFERENCE_PROJECT_DIR, make_entry, write_sealed_run

if TYPE_CHECKING:
    from prismabib.prisma.flow import FlowCounts

#: The run id every :func:`build_project` corpus is written under. Fixed, not
#: generated, so a store built twice from the same specs is byte-identical.
RUN_ID = "20250101T000000Z-stage04ab"

#: The instant that run is stamped with (§3.7.3 rule 3: no wall clock in a
#: fixture).
RUN_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RecordSpec:
    """One synthetic Layer 1 record, described by the dimensions ``A``/``L`` filter on.

    Attributes:
        number: A unique small integer; the record's EID is
            ``2-s2.0-9{number:011d}`` and its Layer 1 ``record_id`` is
            :meth:`record_id`.
        year: ``prism:coverDate``'s year -- what ``criteria.temporal``
            filters on.
        language: ``language``; ``None`` omits the key entirely (Layer 1
            ``records.language`` is then ``NULL``).
        doc_type: ``subtypeDescription`` -- what ``criteria.doc_types.include``
            filters on.
        aggregation_type: ``prism:aggregationType``; ``"Conference
            Proceeding"`` is what makes Layer 1 classify the venue as
            ``conference`` and therefore subject to
            ``doc_types.conference_whitelist``.
        venue_name: ``prism:publicationName`` -- what the conference
            whitelist substring-matches against.
        subject_areas: ``subject-area`` codes -- what ``criteria.subject_areas``
            filters on. Empty means the entry carries no subject-area data
            at all (the reference fixture's situation).
        authors: ``(surname, given_name)`` pairs, in author order. Empty
            omits the ``author`` key entirely, which is what most of this
            module's corpora want; Stage 5's blinding tests need a record
            that genuinely carries author names, so that "the view model
            omits them" is a claim about the blinding and not about an
            empty column.
        cited_by_count: ``citedby-count``, which Layer 1 stores as a
            ``citation_snapshots`` row. Non-zero for the same reason.
    """

    number: int
    year: int = 2020
    language: str | None = "English"
    doc_type: str = "Article"
    aggregation_type: str = "Journal"
    venue_name: str = "Journal of Synthetic Testing"
    subject_areas: tuple[str, ...] = ()
    authors: tuple[tuple[str, str], ...] = ()
    cited_by_count: int = 0

    @property
    def eid(self) -> str:
        """The Scopus EID this spec is written to Layer 0 under."""
        return f"2-s2.0-9{self.number:011d}"

    @property
    def record_id(self) -> str:
        """The Layer 1 ``record_id`` this spec becomes (``scopus:<eid>``)."""
        return f"scopus:{self.eid}"

    def to_entry(self) -> dict[str, Any]:
        """Render this spec as one raw Scopus Search API entry."""
        entry = make_entry(
            eid=self.eid,
            title=f"Synthetic Record {self.number}",
            cover_date=f"{self.year}-06-01",
            language=self.language,
            subtype_description=self.doc_type,
            publication_name=self.venue_name,
            source_id=f"{2000000 + self.number}",
            authkeywords=f"synthetic | record {self.number}",
            citedby_count=self.cited_by_count,
            author=[
                {
                    "authid": f"{9000000 + index}",
                    "surname": surname,
                    "given-name": given_name,
                    "initials": f"{given_name[:1]}.",
                }
                for index, (surname, given_name) in enumerate(self.authors)
            ]
            or None,
        )
        entry["prism:aggregationType"] = self.aggregation_type
        if self.subject_areas:
            entry["subject-area"] = [{"@code": code} for code in self.subject_areas]
        return entry


@dataclass(frozen=True)
class CriteriaSpec:
    """The ``criteria.yaml`` a test wants in force, as data.

    Defaults are deliberately permissive on every dimension except
    ``temporal`` so a test that cares about one filter can set only that
    one and leave the rest neutral.
    """

    version: str = "1.0.0"
    year_start: int = 2000
    year_end: int = 2030
    subject_areas: tuple[str, ...] = ()
    doc_types_include: tuple[str, ...] = ()
    conference_whitelist: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    abstract_reason_codes: tuple[str, ...] = ("OFF_TOPIC", "REVIEW_OR_SURVEY")
    fulltext_reason_codes: tuple[str, ...] = ("INACCESSIBLE", "NOT_PRIMARY_RESEARCH")

    def to_yaml(self) -> str:
        """Render this spec as ``criteria.yaml`` text.

        ``allow_unicode=True``, so a non-ASCII value (a ``conference_whitelist``
        entry naming a French-language conference, say) lands in the file as
        the UTF-8 bytes a human-written ``criteria.yaml`` would carry, rather
        than as PyYAML's default ASCII ``\\xNN`` escapes. Only then does the
        file exercise the UTF-8 decoding
        :func:`prismabib.prisma.criteria._run_git` pins; escaped, every
        ``criteria.yaml`` this helper writes would be pure ASCII and the
        encoding of the pipe it is read back through could never matter.
        Every spec whose values are ASCII renders byte-identically either way.
        """
        document = {
            "version": self.version,
            "temporal": {"year_start": self.year_start, "year_end": self.year_end},
            "subject_areas": list(self.subject_areas),
            "doc_types": {
                "include": list(self.doc_types_include),
                "conference_whitelist": list(self.conference_whitelist),
            },
            "languages": list(self.languages),
            "manual_abstract": {"exclude_reason_codes": list(self.abstract_reason_codes)},
            "manual_fulltext": {"exclude_reason_codes": list(self.fulltext_reason_codes)},
        }
        return str(yaml.safe_dump(document, sort_keys=True, allow_unicode=True))


@dataclass
class CorpusSpec:
    """A whole synthetic project: its records, its criteria, and its ``identified`` count.

    Attributes:
        records: The Layer 1 records to build.
        criteria: The ``criteria.yaml`` to write.
        total_results: ``RunManifest.total_results`` -- PRISMA's
            "identified". Defaults to ``len(records)`` when ``None``, which
            is the only value that makes ``FlowCounts``' first equation
            close.
    """

    records: list[RecordSpec]
    criteria: CriteriaSpec = field(default_factory=CriteriaSpec)
    total_results: int | None = None


def write_criteria(project: Project, criteria: CriteriaSpec) -> None:
    """Overwrite ``project``'s ``criteria.yaml`` with ``criteria``.

    Args:
        project: The project to write into.
        criteria: The criteria to render.
    """
    (project.root / "criteria.yaml").write_text(criteria.to_yaml(), encoding="utf-8")


def build_project(tmp_path: Path, spec: CorpusSpec, *, slug: str = "stage04") -> Project:
    """Build a complete, freshly-loaded project from ``spec``.

    Args:
        tmp_path: The directory to create the project under.
        spec: The corpus, criteria, and ``total_results`` to build.
        slug: The project slug (also its directory name).

    Returns:
        A :class:`~prismabib.project.Project` whose Layer 1 store is already
        built (``build_store(project, rebuild=True)``) and whose
        ``criteria.yaml`` is ``spec.criteria``.
    """
    # Imported here, not at module scope: `build_store` pulls in DuckDB, and
    # the pure-unit modules that import RecordSpec/CriteriaSpec should not pay
    # that import cost.
    from prismabib.store.load import build_store

    project = Project.init(slug, title=f"Stage 4 fixture ({slug})", root=tmp_path)
    write_criteria(project, spec.criteria)
    write_sealed_run(
        project.raw_dir,
        RUN_ID,
        [record.to_entry() for record in spec.records],
        started_at=RUN_STARTED_AT,
        total_results=(spec.total_results if spec.total_results is not None else len(spec.records)),
        criteria_version=spec.criteria.version,
    )
    build_store(project, rebuild=True)
    return project


def copy_reference_project_with_criteria(tmp_path: Path, *, slug: str = "reference") -> Project:
    """Copy the frozen reference fixture, *including* its real ``criteria.yaml``.

    :func:`tests.store_helpers.copy_reference_project` deliberately copies
    only ``raw/``, leaving ``Project.init``'s permissive default
    ``criteria.yaml`` (version ``0.1.0``, every list empty) in place. Stage 4
    needs the fixture's own frozen version ``1.0.0`` criteria -- the worked
    example BUILD_PLAN §3.1 illustrates -- because that is what makes ``A``
    and ``L`` non-trivial.

    Args:
        tmp_path: The directory to create the project copy under.
        slug: The project slug.

    Returns:
        A writable :class:`~prismabib.project.Project` with the reference
        fixture's ``raw/`` and ``criteria.yaml``. The Layer 1 store is *not*
        built; the caller runs ``build_store`` itself.
    """
    project = Project.init(slug, title="Reference fixture (Stage 4 copy)", root=tmp_path)
    shutil.copytree(REFERENCE_PROJECT_DIR / "raw", project.raw_dir, dirs_exist_ok=True)
    shutil.copy(REFERENCE_PROJECT_DIR / "criteria.yaml", project.root / "criteria.yaml")
    return project


# ---------------------------------------------------------------------------
# The published reference-fixture golden, and the screening that produces it
# ---------------------------------------------------------------------------
#
# This lives here, not in a test module, because two tests assert against it --
# `tests/integration/prisma/test_flow.py`'s
# `test_flow_counts__reference_fixture__matches_golden` and `tests/e2e/`'s
# `test_e2e__reference_project__flow_counts_match_published_golden` -- and two
# copies of a golden are strictly worse than one: they can drift apart, and the
# day they do, neither is obviously the wrong one. There is exactly one
# definition of both the screening plan and the numbers it must produce.

#: How the reference fixture is screened for the golden below. Read as "the
#: first 10 records of ``L`` are included at title/abstract, the next 6
#: excluded, the next 4 unsure, and the remaining 76 not yet screened" --
#: then, of those 10, "5 included at full text, 3 excluded, 2 unsure".
ABSTRACT_INCLUDED = 10
ABSTRACT_EXCLUDED = 6
ABSTRACT_UNSURE = 4
FULLTEXT_INCLUDED = 5
FULLTEXT_EXCLUDED_INACCESSIBLE = 2
FULLTEXT_EXCLUDED_NOT_PRIMARY = 1
FULLTEXT_UNSURE = 2

#: Every record in the frozen fixture, per its ``manifest.json``'s
#: ``total_results`` -- PRISMA's "identified", and the only permitted source
#: for it (BUILD_PLAN line 807).
REFERENCE_IDENTIFIED = 120

#: Records the automated filter removes: the 24 conference papers whose venue
#: names match none of ``criteria.yaml``'s ``conference_whitelist`` tokens.
REFERENCE_EXCLUDED_AUTOMATED = 24


def reference_golden() -> FlowCounts:
    """The whole ``FlowCounts`` the reference fixture must produce, screened as above.

    A function rather than a module-level constant so that no test can
    accidentally mutate the shared golden's ``excluded_fulltext`` dict and
    leave a different one behind for the next test (``FlowCounts`` is a frozen
    dataclass, but the dict inside it is not).

    Returns:
        The published golden. ``identified`` and ``excluded_automated`` are
        properties of the frozen fixture; every remaining number follows from
        the screening plan above.
    """
    # Imported lazily for the same reason `build_project` imports `build_store`
    # lazily: `prisma.flow` reaches DuckDB transitively, and the pure-unit tests
    # that import this module's dataclasses should not pay for that.
    from prismabib.prisma.flow import FlowCounts as _FlowCounts

    after_automated = REFERENCE_IDENTIFIED - REFERENCE_EXCLUDED_AUTOMATED
    return _FlowCounts(
        identified=REFERENCE_IDENTIFIED,
        duplicates_across_searches=0,
        removed_other_reasons=0,
        excluded_automated=REFERENCE_EXCLUDED_AUTOMATED,
        # All 24 are venue exclusions: the fixture's criteria carry a
        # conference whitelist and no year, subject or doc-type restriction that
        # removes anything. Derived from the fixture and cross-checked against
        # REFERENCE_EXCLUDED_AUTOMATED, never read off a failing test (§5 risk 11).
        excluded_automated_by_reason={
            "year": 0,
            "subject_area": 0,
            "doc_type": 0,
            "venue": REFERENCE_EXCLUDED_AUTOMATED,
        },
        after_automated=after_automated,
        # Inert on this fixture: its one non-English record is also a
        # non-whitelisted conference paper, so `A` has already removed it by
        # the time the language filter runs. Deliberate and accepted -- see
        # the Stage 11 note in .claude/PROGRESS.md.
        excluded_language=0,
        after_language=after_automated,
        excluded_title_abstract=ABSTRACT_EXCLUDED,
        unsure_title_abstract=after_automated - ABSTRACT_EXCLUDED - ABSTRACT_INCLUDED,
        retrieved_fulltext=ABSTRACT_INCLUDED,
        excluded_fulltext={
            "INACCESSIBLE": FULLTEXT_EXCLUDED_INACCESSIBLE,
            "NOT_PRIMARY_RESEARCH": FULLTEXT_EXCLUDED_NOT_PRIMARY,
        },
        unsure_fulltext=FULLTEXT_UNSURE,
        included=FULLTEXT_INCLUDED,
    )


def screen_reference_project(project: Project) -> None:
    """Apply the fixed screening plan above to ``project``.

    Records are taken in sorted ``record_id`` order so the same decisions land
    on the same records on every machine and every run (§3.7.3 rule 3: never
    rely on set or filesystem ordering).

    Args:
        project: A reference-fixture copy whose Layer 1 store is already built.
    """
    from prismabib.prisma import engine
    from prismabib.prisma.log import DecisionLog
    from prismabib.stage import PrismaStage
    from tests.conftest import SeededIdFactory

    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ref"))
    eligible = sorted(engine.language_set(project))

    abstract_plan = (
        [("include", None)] * ABSTRACT_INCLUDED
        + [("exclude", "REVIEW_OR_SURVEY")] * ABSTRACT_EXCLUDED
        + [("unsure", None)] * ABSTRACT_UNSURE
    )
    for record_id, (decision, reason_code) in zip(eligible, abstract_plan, strict=False):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision=decision,  # type: ignore[arg-type]
            reason_code=reason_code,
        )

    fulltext_plan = (
        [("include", None)] * FULLTEXT_INCLUDED
        + [("exclude", "INACCESSIBLE")] * FULLTEXT_EXCLUDED_INACCESSIBLE
        + [("exclude", "NOT_PRIMARY_RESEARCH")] * FULLTEXT_EXCLUDED_NOT_PRIMARY
        + [("unsure", None)] * FULLTEXT_UNSURE
    )
    for record_id, (decision, reason_code) in zip(
        eligible[:ABSTRACT_INCLUDED], fulltext_plan, strict=True
    ):
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=record_id,
            reviewer="kp",
            decision=decision,  # type: ignore[arg-type]
            reason_code=reason_code,
        )


# ---------------------------------------------------------------------------
# Git-backed criteria history (what `prisma/criteria.py` resolves against)
# ---------------------------------------------------------------------------

#: A fixed identity and a fully self-contained environment for every `git`
#: call these helpers make. Passing this rather than inheriting the ambient
#: environment keeps the test independent of whoever runs it: no global
#: `user.name`, no `commit.gpgsign`, no `core.hooksPath`, no `init.
#: defaultBranch` surprise (§3.7.3 rule 3 -- determinism is a requirement).
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "prismabib tests",
    "GIT_AUTHOR_EMAIL": "tests@example.invalid",
    "GIT_COMMITTER_NAME": "prismabib tests",
    "GIT_COMMITTER_EMAIL": "tests@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-18T14:22:07+00:00",
    "GIT_COMMITTER_DATE": "2026-01-18T14:22:07+00:00",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "HOME": "/nonexistent",
    "PATH": os.environ.get("PATH", ""),
}


def run_git(root: Path, *args: str) -> None:
    """Run one ``git`` subcommand in ``root``, raising on failure."""
    subprocess.run(
        ["git", *args],
        cwd=root,
        env=_GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    )


def commit_criteria(project: Project, criteria: CriteriaSpec, message: str) -> None:
    """Write ``criteria`` into ``project`` and commit it to a git repository.

    Initialises a repository at ``project.root`` on first call. This is how
    a test builds the *criteria history*
    :func:`prismabib.prisma.criteria.resolve_criteria` reads: BUILD_PLAN's
    Stage 4 keeps no per-version archive directory, so a superseded
    ``criteria.yaml`` version exists only as a git blob.

    Args:
        project: The project whose ``criteria.yaml`` to amend.
        criteria: The new criteria to write and commit.
        message: The commit message.
    """
    if not (project.root / ".git").is_dir():
        run_git(project.root, "init", "--quiet", "--initial-branch=main")
    write_criteria(project, criteria)
    run_git(project.root, "add", "criteria.yaml")
    run_git(project.root, "commit", "--quiet", "--message", message)


# ---------------------------------------------------------------------------
# Decision-log byte surgery (what a text editor or a crash would do)
# ---------------------------------------------------------------------------


def sidecar_path(project: Project) -> Path:
    """The ``decisions.jsonl.sha256`` sidecar path for ``project``."""
    path = project.decisions_path
    return path.with_name(path.name + ".sha256")


def read_log_bytes(project: Project) -> bytes:
    """The current raw bytes of ``project``'s ``decisions.jsonl``."""
    return project.decisions_path.read_bytes()


def overwrite_log_bytes(project: Project, content: bytes) -> None:
    """Replace ``decisions.jsonl``'s content wholesale, leaving the sidecar alone.

    Args:
        project: The project whose log to overwrite.
        content: The exact bytes to write.
    """
    project.decisions_path.write_bytes(content)


def append_raw_bytes(project: Project, content: bytes) -> None:
    """Append raw bytes to ``decisions.jsonl``, leaving the sidecar alone.

    Args:
        project: The project whose log to append to.
        content: The exact bytes to append -- a partial line, a whole line,
            or several.
    """
    with project.decisions_path.open("ab") as handle:
        handle.write(content)


def rewrite_sidecar(project: Project, content: bytes | None = None) -> None:
    """Rewrite the checksum sidecar to describe ``content``.

    Args:
        project: The project whose sidecar to rewrite.
        content: The bytes the sidecar should describe. Defaults to the
            decision log's current content, i.e. "re-bless whatever is on
            disk now" -- which is how a test gets a *hand-written* log past
            the checksum guard in order to assert a later, different rule
            (an unknown ``schema_version``, a duplicate ``event_id``).
    """
    payload = read_log_bytes(project) if content is None else content
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path(project).write_text(f"{digest}  {project.decisions_path.name}\n", encoding="utf-8")


def sidecar_matches_log(project: Project) -> bool:
    """Whether the sidecar's recorded digest matches the log's current bytes."""
    recorded = sidecar_path(project).read_text(encoding="utf-8").split(maxsplit=1)[0]
    return recorded == hashlib.sha256(read_log_bytes(project)).hexdigest()


# ---------------------------------------------------------------------------
# A stand-in for `msvcrt`, so the Windows lock backend can be run on Linux
# ---------------------------------------------------------------------------
#
# This is a **fake, and it is injected** -- `_ByteRangeLocking.from_module`
# takes the module to read `locking`/`LK_NBLCK`/`LK_UNLCK` off, and
# `_select_lock_backend` takes the loader that produces it. Nothing here
# monkeypatches a `prismabib.*` symbol (§3.7.3 rule 1); the backend under test
# is the real one, driving a substitute for the one C call this machine does
# not have.
#
# Read the limits of that honestly, and see `docs/testing.md`: a fake that
# agrees with itself proves only that the backend's *logic* -- the retry loop,
# the seek-and-restore, the error translation -- is self-consistent. Whether
# `msvcrt.locking` really behaves as modelled here is checked by the
# `full-windows` CI job, and by nothing else in this repository.


class FakeWindowsLocking:
    """An in-process model of ``msvcrt.locking``'s byte-range locks.

    Models the four properties the Windows backend actually depends on:

    1. A lock covers a **byte range taken at the descriptor's current
       position**, not the file -- so this call reads the position itself
       with ``os.lseek`` rather than being told it, and a backend that
       forgot to seek would lock a different region and be caught.
    2. A region is owned by **at most one handle**; a second handle's
       attempt fails rather than waits.
    3. Failure is ``OSError`` with ``errno.EACCES``, which is what the real
       CRT reports for an already-locked region.
    4. ``LK_UNLCK`` must name the region the lock was taken on.

    Thread-safe, because the conformance suite drives it from two threads to
    reproduce contention the way a second process would.

    Attributes:
        LK_NBLCK: The CRT's ``_LK_NBLCK``. The value is the real one, but
            nothing in the backend depends on the number: it reads the
            constants off the module it is given, exactly as it reads them
            off ``msvcrt``.
        LK_UNLCK: The CRT's ``_LK_UNLCK``.
        regions: Every ``(offset, nbytes)`` this fake has been asked to lock
            or unlock, in call order -- what pins the sentinel range.
    """

    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._owners: dict[tuple[int, int], int] = {}
        self.regions: list[tuple[int, int]] = []

    def __call__(self, fd: int, mode: int, nbytes: int) -> None:
        """Lock or unlock ``nbytes`` at ``fd``'s current position.

        Args:
            fd: The descriptor, positioned where the region starts.
            mode: :attr:`LK_NBLCK` or :attr:`LK_UNLCK`.
            nbytes: The region's length.

        Raises:
            OSError: ``EACCES`` when locking a region another handle owns,
                or unlocking a region this handle does not own.
        """
        region = (os.lseek(fd, 0, os.SEEK_CUR), nbytes)
        with self._mutex:
            self.regions.append(region)
            if mode == self.LK_UNLCK:
                if self._owners.get(region) != fd:
                    raise OSError(errno.EACCES, "Permission denied", None, 33)
                del self._owners[region]
                return
            if region in self._owners:
                raise OSError(errno.EACCES, "Permission denied", None, 33)
            self._owners[region] = fd

    def owner_of(self, region: tuple[int, int]) -> int | None:
        """The descriptor currently holding ``region``, or ``None``.

        Args:
            region: An ``(offset, nbytes)`` pair.

        Returns:
            The owning descriptor, or ``None`` if the region is free.
        """
        with self._mutex:
            return self._owners.get(region)


def fake_msvcrt(locking: FakeWindowsLocking) -> ModuleType:
    """Wrap ``locking`` in a real module object shaped like ``msvcrt``.

    A genuine :class:`~types.ModuleType` rather than a namespace object, so
    what ``_ByteRangeLocking.from_module`` receives is the same *kind* of
    thing ``importlib.import_module("msvcrt")`` returns on Windows.

    Args:
        locking: The fake to expose as the module's ``locking``.

    Returns:
        A module exposing ``locking``, ``LK_NBLCK`` and ``LK_UNLCK``.
    """
    module = ModuleType("msvcrt")
    module.locking = locking
    module.LK_NBLCK = FakeWindowsLocking.LK_NBLCK
    module.LK_UNLCK = FakeWindowsLocking.LK_UNLCK
    return module


__all__ = [
    "ABSTRACT_EXCLUDED",
    "ABSTRACT_INCLUDED",
    "ABSTRACT_UNSURE",
    "FULLTEXT_EXCLUDED_INACCESSIBLE",
    "FULLTEXT_EXCLUDED_NOT_PRIMARY",
    "FULLTEXT_INCLUDED",
    "FULLTEXT_UNSURE",
    "REFERENCE_EXCLUDED_AUTOMATED",
    "REFERENCE_IDENTIFIED",
    "RUN_ID",
    "RUN_STARTED_AT",
    "CorpusSpec",
    "CriteriaSpec",
    "FakeWindowsLocking",
    "RecordSpec",
    "append_raw_bytes",
    "build_project",
    "commit_criteria",
    "copy_reference_project_with_criteria",
    "fake_msvcrt",
    "overwrite_log_bytes",
    "read_log_bytes",
    "reference_golden",
    "rewrite_sidecar",
    "run_git",
    "screen_reference_project",
    "sidecar_matches_log",
    "sidecar_path",
    "write_criteria",
]
