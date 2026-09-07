"""Shared test-only helpers for the ``taxonomy/`` suite (BUILD_PLAN §Stage 8).

Reuses :mod:`tests.bibliometrics_helpers`'s ``include_everything``/
``open_corpus`` (generic over any :class:`~prismabib.project.Project`, so
there is nothing taxonomy-specific to duplicate) and
:mod:`tests.store_helpers`'s ``make_entry``/``write_sealed_run`` -- the same
Layer 0 primitives every other stage's fixtures build on. What this module
adds is its own record spec: unlike Stage 7's ``BibRecordSpec``, the coder
matches against title/abstract/author-keyword *text*, so this stage's
fixtures need to control that text directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prismabib.project import Project
from prismabib.taxonomy.rules import CompiledRuleFile, load_rule_file
from prismabib.taxonomy.schema import TaxonomySchema
from tests.bibliometrics_helpers import PERMISSIVE_CRITERIA_YAML
from tests.store_helpers import make_entry, write_sealed_run

#: The run every :func:`build_taxonomy_project` corpus is written under.
#: Fixed, not generated, so two calls with identical specs are byte-identical.
TAXONOMY_RUN_ID = "20250101T000000Z-stage08tax"
TAXONOMY_RUN_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class TaxonomyRecordSpec:
    """One synthetic record whose title/abstract/author-keyword text a rule can match on.

    Attributes:
        number: A unique small integer; determines ``eid``/``record_id``.
        title: ``dc:title`` verbatim -- the coder's ``title`` field text.
        abstract: ``dc:description`` verbatim (the coder's ``abstract``
            field text), or ``None`` to omit it entirely.
        author_keywords: Raw ``authkeywords`` terms, ``" | "``-joined on
            write -- the coder's ``author_keywords`` field text.
    """

    number: int
    title: str = "A Synthetic Study"
    abstract: str | None = "A synthetic abstract."
    author_keywords: tuple[str, ...] = ()

    @property
    def eid(self) -> str:
        """The Scopus EID this spec is written to Layer 0 under."""
        return f"2-s2.0-7{self.number:011d}"

    @property
    def record_id(self) -> str:
        """The Layer 1 ``record_id`` this spec becomes (``scopus:<eid>``)."""
        return f"scopus:{self.eid}"

    def to_entry(self) -> dict[str, Any]:
        """Render this spec as one raw Scopus Search API entry."""
        return make_entry(
            eid=self.eid,
            title=self.title,
            description=self.abstract,
            authkeywords=" | ".join(self.author_keywords) if self.author_keywords else None,
            source_id=f"{4000000 + self.number}",
        )


def build_taxonomy_project(
    tmp_path: Path,
    records: list[TaxonomyRecordSpec],
    *,
    slug: str = "tax",
    criteria_yaml: str = PERMISSIVE_CRITERIA_YAML,
) -> Project:
    """Build a complete, freshly-loaded project from ``records``.

    Args:
        tmp_path: The directory to create the project under.
        records: The records to build.
        slug: The project slug.
        criteria_yaml: Written verbatim to ``criteria.yaml``. Defaults to
            :data:`tests.bibliometrics_helpers.PERMISSIVE_CRITERIA_YAML`, so
            every record reaches ``PrismaStage.LANGUAGE`` unfiltered.

    Returns:
        A :class:`~prismabib.project.Project` whose Layer 1 store is already
        built. No record is screened; combine with
        :func:`tests.bibliometrics_helpers.include_everything` for a
        non-empty ``INCLUDED`` corpus.
    """
    from prismabib.store.load import build_store

    project = Project.init(slug, title=f"Taxonomy fixture ({slug})", root=tmp_path)
    (project.root / "criteria.yaml").write_text(criteria_yaml, encoding="utf-8")
    write_sealed_run(
        project.raw_dir,
        TAXONOMY_RUN_ID,
        [record.to_entry() for record in records],
        started_at=TAXONOMY_RUN_STARTED_AT,
        criteria_version="1.0.0",
    )
    build_store(project, rebuild=True)
    return project


#: A small, two-dimension schema used across the taxonomy suite: one
#: single-label dimension (``learning_paradigm``) and one multi-label
#: dimension (``architecture``) -- BUILD_PLAN's own reference shape, trimmed
#: to a handful of categories per dimension so fixtures stay readable.
#: The same dimensions, with `learning_paradigm` multi-label -- so a record
#: can carry two categories at different declared confidences and the
#: confidence-band placement rule (ADR 0023 Decision 5c) is observable.
MULTI_LABEL_DIMENSIONS_YAML = """\
dimensions:
  - id: learning_paradigm
    multi_label: true
    categories: [supervised, unsupervised, self_supervised]
  - id: architecture
    multi_label: true
    categories: [cnn, transformer, gan]
"""

TEST_DIMENSIONS_YAML = """\
dimensions:
  - id: learning_paradigm
    multi_label: false
    categories: [supervised, unsupervised, self_supervised]
  - id: architecture
    multi_label: true
    categories: [cnn, transformer, gan]
"""


def write_dimensions(project: Project, yaml_text: str = TEST_DIMENSIONS_YAML) -> None:
    """Write ``project``'s ``taxonomy/dimensions.yaml``.

    Args:
        project: The project to write into.
        yaml_text: The YAML content. Defaults to :data:`TEST_DIMENSIONS_YAML`.
    """
    path = project.taxonomy_dimensions_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")


def write_rule_file(project: Project, dimension: str, yaml_text: str) -> Path:
    """Write one rule file under ``project``'s ``taxonomy/rules/``.

    Args:
        project: The project to write into.
        dimension: The rule file's basename (without ``.yaml``), conventionally
            matching the dimension it declares.
        yaml_text: The rule file's YAML content.

    Returns:
        The path written.
    """
    path = project.taxonomy_rules_dir / f"{dimension}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_text, encoding="utf-8")
    return path


def load_rule_files(
    project: Project, schema: TaxonomySchema, *names: str
) -> tuple[CompiledRuleFile, ...]:
    """Load every named rule file under ``project.taxonomy_rules_dir``.

    Args:
        project: The project whose rule files to load.
        schema: The project's taxonomy schema, to cross-check each file against.
        *names: Each rule file's basename (without ``.yaml``).

    Returns:
        The compiled rule files, in the order named.
    """
    return tuple(
        load_rule_file(project.taxonomy_rules_dir / f"{name}.yaml", schema=schema) for name in names
    )


#: A rule file for ``learning_paradigm`` (single-label) with three
#: unambiguous categories, matched on the title -- deliberately simple
#: strings (a suite-wide rule per §3.7.3: no test here asserts that a
#: *particular* regex matches a *particular* string, only that the engine's
#: load/fold/count machinery behaves correctly around whatever a rule
#: produces).
#: Three categories at three different declared confidences, so a test can
#: tell `min`, `max` and "first category wins" apart when placing a record
#: into a confidence band (ADR 0023 Decision 5c).
#:
#: `LEARNING_PARADIGM_RULES_V1` declares no `confidence` at all, so every
#: category there defaults to 1.0 -- which makes those four placement rules
#: indistinguishable, and left the banding rule asserted by nothing.
LEARNING_PARADIGM_RULES_GRADED_CONFIDENCE = """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'AlphaMarker'}
    confidence: 0.95
  - id: unsupervised
    any:
      - {field: title, pattern: 'BetaMarker'}
    confidence: 0.6
  - id: self_supervised
    any:
      - {field: title, pattern: 'GammaMarker'}
    confidence: 0.8
"""

LEARNING_PARADIGM_RULES_V1 = """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'AlphaMarker'}
  - id: unsupervised
    any:
      - {field: title, pattern: 'BetaMarker'}
  - id: self_supervised
    any:
      - {field: title, pattern: 'GammaMarker'}
"""

#: The same dimension, one version bump later, with an extra ``none`` clause
#: added to ``supervised`` -- used by the "override survives a rule-version
#: bump" test.
LEARNING_PARADIGM_RULES_V2 = """\
version: 2.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'AlphaMarker'}
    none:
      - {field: abstract, pattern: 'AlphaMarkerExcluded'}
  - id: unsupervised
    any:
      - {field: title, pattern: 'BetaMarker'}
  - id: self_supervised
    any:
      - {field: title, pattern: 'GammaMarker'}
"""

#: A rule file for ``architecture`` (multi-label) whose categories can
#: legitimately co-occur on one record.
ARCHITECTURE_RULES_V1 = """\
version: 1.0.0
dimension: architecture
counting_unit: assignments
categories:
  - id: cnn
    any:
      - {field: author_keywords, pattern: 'DeltaMarker'}
  - id: transformer
    any:
      - {field: author_keywords, pattern: 'EpsilonMarker'}
  - id: gan
    any:
      - {field: author_keywords, pattern: 'ZetaMarker'}
"""

#: A deliberately over-assigning rule file: two ``learning_paradigm``
#: categories can both fire on the same record (``AmbiguousMarker`` appears
#: in both), which is exactly the corruption
#: ``test_counting_unit__over_assigned_fixture__assertion_fails_loudly``
#: exists to catch.
LEARNING_PARADIGM_RULES_OVER_ASSIGNED = """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'AmbiguousMarker'}
  - id: unsupervised
    any:
      - {field: title, pattern: 'AmbiguousMarker'}
  - id: self_supervised
    any:
      - {field: title, pattern: 'GammaMarker'}
"""

#: A rule file that references ``index_keywords`` -- a field the real Layer 1
#: loader never populates from a Search API capture (``store/load.py``'s own
#: module docstring), so this is valid and matches nothing on *any* corpus
#: built the ordinary way. Used by the field-diagnostic tests (ADR 0023
#: Decision 6).
LEARNING_PARADIGM_RULES_INDEX_KEYWORDS = """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: index_keywords, pattern: 'AnythingAtAll'}
  - id: unsupervised
    any:
      - {field: title, pattern: 'BetaMarker'}
  - id: self_supervised
    any:
      - {field: title, pattern: 'GammaMarker'}
"""

__all__ = [
    "ARCHITECTURE_RULES_V1",
    "LEARNING_PARADIGM_RULES_GRADED_CONFIDENCE",
    "LEARNING_PARADIGM_RULES_INDEX_KEYWORDS",
    "LEARNING_PARADIGM_RULES_OVER_ASSIGNED",
    "LEARNING_PARADIGM_RULES_V1",
    "LEARNING_PARADIGM_RULES_V2",
    "MULTI_LABEL_DIMENSIONS_YAML",
    "TAXONOMY_RUN_ID",
    "TAXONOMY_RUN_STARTED_AT",
    "TEST_DIMENSIONS_YAML",
    "TaxonomyRecordSpec",
    "build_taxonomy_project",
    "load_rule_files",
    "write_dimensions",
    "write_rule_file",
]
