"""Shared test-only helpers for the ``bibliometrics/`` suite (BUILD_PLAN Stage 7).

Kept beside :mod:`tests.prisma_helpers` and :mod:`tests.store_helpers`
rather than folded into either: :mod:`tests.prisma_helpers`'s ``RecordSpec``
has no affiliation/author-id/keyword control (Stage 4's fixtures never
needed one), and this stage's tests need all three to exercise geography,
co-authorship and keyword analyses precisely. Everything here composes with
those modules' primitives (:func:`tests.store_helpers.make_entry`,
:func:`tests.store_helpers.write_sealed_run`) rather than duplicating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus, build_store
from tests.conftest import SeededIdFactory
from tests.store_helpers import make_entry, write_sealed_run

#: A project with no restriction on year, subject area, doc type, venue or
#: language -- so every loaded record reaches `PrismaStage.LANGUAGE`
#: unfiltered, and :func:`include_everything` can put every one of them into
#: `PrismaStage.INCLUDED` without a test having to reason about which
#: automated filter would otherwise remove it.
PERMISSIVE_CRITERIA_YAML = """\
version: "1.0.0"
temporal:
  year_start: 1900
  year_end: 2100
subject_areas: []
doc_types:
  include: []
  conference_whitelist: []
languages: []
manual_abstract:
  exclude_reason_codes: [OFF_TOPIC]
manual_fulltext:
  exclude_reason_codes: [INACCESSIBLE]
"""


@dataclass(frozen=True)
class AffiliationSpec:
    """One affiliation on a synthetic record, as Scopus's wire format shapes it."""

    afid: str
    name: str = "Test University"
    city: str | None = None
    country: str | None = None


@dataclass(frozen=True)
class AuthorSpec:
    """One author on a synthetic record, as Scopus's wire format shapes it."""

    author_id: str
    surname: str
    given_name: str = "A"


@dataclass(frozen=True)
class BibRecordSpec:
    """One synthetic record for the bibliometrics suite.

    Attributes:
        number: A unique small integer; determines ``eid``/``record_id``.
        year: ``prism:coverDate``'s year. Layer 1 requires a parseable
            ``prism:coverDate`` for every loaded record (an entry lacking
            one is malformed and never loaded at all) -- so, unlike
            ``records.year`` in the abstract, this fixture can never
            produce a record with a ``NULL`` year. Tests of that case
            build a :class:`polars.DataFrame` by hand instead (see
            ``tests/unit/bibliometrics/test_trends.py``).
        venue_name: ``prism:publicationName``, before normalisation.
        source_id: ``source-id`` (drives ``venue_id`` -- see
            ``store/load.py::_venue_id_from_entry``). Two specs with the
            same ``venue_name`` but different ``source_id`` still normalise
            to one venue group under :mod:`prismabib.bibliometrics.venues`,
            which is exactly the case that module exists to catch.
        venue_type: ``prism:aggregationType``.
        cited_by_count: One ``citation_snapshots`` row, stamped with the
            *run's* ``started_at`` -- see :func:`build_bib_project`.
        author_keywords: Raw ``authkeywords`` terms, ``" | "``-joined on
            write.
        affiliations: See :class:`AffiliationSpec`.
        authors: See :class:`AuthorSpec`.
    """

    number: int
    year: int = 2020
    venue_name: str = "Journal of Synthetic Testing"
    source_id: str | None = None
    venue_type: str = "Journal"
    cited_by_count: int = 0
    author_keywords: tuple[str, ...] = ()
    affiliations: tuple[AffiliationSpec, ...] = ()
    authors: tuple[AuthorSpec, ...] = ()

    @property
    def eid(self) -> str:
        """The Scopus EID this spec is written to Layer 0 under."""
        return f"2-s2.0-8{self.number:011d}"

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
            publication_name=self.venue_name,
            source_id=self.source_id or f"{3000000 + self.number}",
            citedby_count=self.cited_by_count,
            authkeywords=" | ".join(self.author_keywords) if self.author_keywords else None,
            affiliation=[
                {
                    "afid": affiliation.afid,
                    "affilname": affiliation.name,
                    "affiliation-city": affiliation.city,
                    "affiliation-country": affiliation.country,
                }
                for affiliation in self.affiliations
            ]
            or None,
            author=[
                {
                    "authid": author.author_id,
                    "surname": author.surname,
                    "given-name": author.given_name,
                    "initials": f"{author.given_name[:1]}.",
                }
                for author in self.authors
            ]
            or None,
        )
        entry["prism:aggregationType"] = self.venue_type
        return entry


@dataclass
class BibCorpusSpec:
    """A whole synthetic project for the bibliometrics suite.

    Attributes:
        records: The records to build, possibly across several runs (see
            :attr:`run_started_ats`).
        run_started_ats: One ``started_at`` per sealed run; ``records`` is
            split across ``len(run_started_ats)`` runs in order (all in the
            first run when there is only one, the default). Multiple runs
            with distinct timestamps are what let a test produce a
            non-uniform citation snapshot or a specific
            ``first_incomplete_year`` boundary.
        criteria_yaml: Written verbatim to ``criteria.yaml``.
    """

    records: list[BibRecordSpec]
    run_started_ats: tuple[datetime, ...] = (datetime(2025, 6, 15, tzinfo=UTC),)
    criteria_yaml: str = PERMISSIVE_CRITERIA_YAML


def _chunk(records: list[BibRecordSpec], n: int) -> list[list[BibRecordSpec]]:
    """Split ``records`` into exactly ``n`` order-preserving chunks, some possibly empty.

    Exactly ``n``, including when ``len(records) < n``. Ceil-division
    chunking returns *fewer* than ``n`` in that case, which the caller then
    feeds to ``zip(..., strict=True)`` against ``n`` run timestamps and gets
    a ``ValueError`` from -- an obscure failure for the natural thing to
    write when testing non-uniform snapshots,
    ``BibCorpusSpec(records=[r1, r2], run_started_ats=(a, b, c))``.
    """
    if n <= 1:
        return [records]
    size = -(-len(records) // n) if records else 0
    chunks = [records[index : index + size] for index in range(0, len(records), size or 1)]
    return (chunks + [[] for _ in range(n)])[:n]


def build_bib_project(tmp_path: Path, spec: BibCorpusSpec, *, slug: str = "bib") -> Project:
    """Build a complete, freshly-loaded project from ``spec``.

    Args:
        tmp_path: The directory to create the project under.
        spec: The corpus and criteria to build.
        slug: The project slug.

    Returns:
        A :class:`~prismabib.project.Project` whose Layer 1 store is already
        built. No record is screened -- see :func:`include_everything`.
    """
    project = Project.init(slug, title=f"Bibliometrics fixture ({slug})", root=tmp_path)
    (project.root / "criteria.yaml").write_text(spec.criteria_yaml, encoding="utf-8")

    chunks = _chunk(spec.records, len(spec.run_started_ats))
    for index, (started_at, chunk) in enumerate(zip(spec.run_started_ats, chunks, strict=True)):
        write_sealed_run(
            project.raw_dir,
            f"2025010{index + 1}T000000Z-bibrun{index:03d}",
            [record.to_entry() for record in chunk],
            started_at=started_at,
            criteria_version="1.0.0",
        )
    build_store(project, rebuild=True)
    return project


def include_everything(project: Project) -> None:
    """Log an unconditional ``include`` for every language-eligible record.

    Puts every record :func:`prismabib.prisma.engine.language_set` admits
    into ``PrismaStage.INCLUDED``, so a test can exercise a non-empty
    ``INCLUDED`` corpus without hand-building a per-record screening plan.

    Args:
        project: A project whose Layer 1 store is already built.
    """
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="bibinc"))
    eligible = sorted(engine.language_set(project))
    for record_id in eligible:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="t",
            decision="include",
            reason_code=None,
        )
    for record_id in eligible:
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=record_id,
            reviewer="t",
            decision="include",
            reason_code=None,
        )


def open_corpus(project: Project) -> Corpus:
    """:meth:`~prismabib.store.load.Corpus.open` a fresh, read-only handle onto ``project``."""
    return Corpus.open(project, read_only=True)


__all__ = [
    "PERMISSIVE_CRITERIA_YAML",
    "AffiliationSpec",
    "AuthorSpec",
    "BibCorpusSpec",
    "BibRecordSpec",
    "build_bib_project",
    "include_everything",
    "open_corpus",
]
