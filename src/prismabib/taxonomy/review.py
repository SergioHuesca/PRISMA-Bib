"""The taxonomy review priority queue and the coverage/audit report (BUILD_PLAN §Stage 8).

The whole argument for rules-plus-override (ADR 0005) is that a human only
ever touches the hard cases. :func:`build_review_queue` surfaces exactly
those, in priority order:

1. **Uncoded** -- no rule fired for a required dimension.
2. **Conflicting** -- more than one category fired in a
   ``multi_label: false`` dimension.
3. **Audit sample** -- a seeded, reproducible 10% of the remaining,
   confidently rule-coded records, which is the *only* way to estimate rule
   precision (ADR 0023 Decision 5).

A record a human has already reviewed for a dimension (an override event
exists, even one asserting ``categories: ()``) is never re-queued for that
dimension: the reviewer's verdict already stands.

**ADR 0023 Decision 5: the audit seed is data-derived.** ``sha256`` over the
project slug, the dimension id, the rule file's declared ``version``, and
the sorted coded record ids -- not ``hash()`` (varies with
``PYTHONHASHSEED``), not the clock, not an unseeded :mod:`random`. The seed
changes when what is being audited changes: a rule-version bump re-draws the
sample, which is exactly when the previous sample has stopped being evidence
about the current rules. The resolved seed is carried on
:class:`ReviewQueue` so an audit agreement rate quoted in a methods section
can be regenerated from the number beside it.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from prismabib.project import Project
from prismabib.taxonomy.coder import CodingResult, FieldDiagnostic
from prismabib.taxonomy.overrides import OverrideEvent, OverrideFoldKey, fold_override_events
from prismabib.taxonomy.rules import CompiledRuleFile
from prismabib.taxonomy.schema import TaxonomySchema

#: BUILD_PLAN's fixed priority order.
QueuePriority = Literal["uncoded", "conflicting", "audit_sample"]

#: The default fraction of confidently rule-coded records the audit sample draws.
DEFAULT_AUDIT_FRACTION = 0.10

_SEED_SEPARATOR = "\x1f"  # ASCII unit separator: never occurs in a slug/id/version.


@dataclass(frozen=True)
class QueueEntry:
    """One row of the review queue: a record, a dimension, and why it is here.

    Attributes:
        record_id: The record to review.
        dimension: The dimension it needs review in.
        priority: ``"uncoded"``, ``"conflicting"``, or ``"audit_sample"``.
        rule_categories: The rule-assigned categories for this
            (record, dimension) -- the starting point a reviewer sees (ADR
            0023 Consequence 3: "the review queue showing the current rule
            assignments as the starting point for the event").
    """

    record_id: str
    dimension: str
    priority: QueuePriority
    rule_categories: tuple[str, ...]


@dataclass(frozen=True)
class ReviewQueue:
    """The full, ordered review queue plus the audit sample's resolved seeds.

    Attributes:
        entries: Every queue row, ordered ``uncoded`` first, then
            ``conflicting``, then ``audit_sample`` (BUILD_PLAN's own
            priority order); within a bucket, ordered by ``(dimension,
            record_id)``, or by ``(dimension, record_id)`` over the sampled
            set for ``audit_sample``.
        audit_seeds: ``dimension -> resolved seed`` (ADR 0023 Decision 5) --
            recorded here so an audit agreement rate is reproducible from a
            number that ships beside it, not from re-deriving the seed by
            hand.
        audit_samples: ``dimension -> the designated audit record ids``,
            sorted. **Not the same as the ``audit_sample`` entries**: those
            are the members still outstanding, and shrink as reviewing
            proceeds, while this is the stable set the agreement rate is
            measured over (ADR 0023 Decision 5b). Conflating the two is what
            made the rate permanently uncomputable.
    """

    entries: tuple[QueueEntry, ...]
    audit_seeds: Mapping[str, int]
    audit_samples: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def audit_sample_seed(
    *, project_slug: str, dimension_id: str, rule_version: str, record_ids: Sequence[str]
) -> int:
    """Derive the audit sample's seed (ADR 0023 Decision 5).

    ``sha256`` over the project slug, the dimension id, the rule file's
    declared version, and the *sorted* coded record ids -- every input is
    data, not the clock, not ``PYTHONHASHSEED``, so the same corpus and rule
    version resolve to the same seed on any machine, in any process, next
    year. Sorting ``record_ids`` first makes the digest independent of
    whatever order the caller happened to iterate them in.

    Args:
        project_slug: The project's slug.
        dimension_id: The dimension being audited.
        rule_version: The rule file's declared ``version``.
        record_ids: Every record id in the coded stage -- **not** only the
            coded ones. The seed digests the corpus, so it changes when the
            corpus does; which of those records are eligible for the audit
            pool is a separate question, answered in
            :func:`build_review_queue`. Naming it "coded" here (and in ADR
            0023 Decision 5) described the pool rather than the digest.

    Returns:
        A seed suitable for :class:`random.Random`.
    """
    digest = hashlib.sha256(
        _SEED_SEPARATOR.join(
            (project_slug, dimension_id, rule_version, *sorted(record_ids))
        ).encode("utf-8")
    ).hexdigest()
    return int(digest[:16], 16)


def build_review_queue(
    result: CodingResult,
    schema: TaxonomySchema,
    rule_files: Sequence[CompiledRuleFile],
    *,
    project_slug: str,
    audit_fraction: float = DEFAULT_AUDIT_FRACTION,
) -> ReviewQueue:
    """Build the priority-ordered review queue for every dimension a rule file covers.

    Args:
        result: A :func:`prismabib.taxonomy.coder.code` result.
        schema: The project's declared taxonomy dimensions.
        rule_files: The compiled rule files whose dimensions to queue.
        project_slug: The owning project's slug (fed into the audit seed).
        audit_fraction: The share of unambiguously coded records to
            designate for audit per dimension. Defaults to
            :data:`DEFAULT_AUDIT_FRACTION` (10%, BUILD_PLAN's own default).

    Returns:
        The queue, priority-ordered, plus every dimension's resolved audit
        seed and designated audit sample.

    **The audit sample is a designated set, not a slice of the work list**
    (ADR 0023 Decision 5b). Those are two different things and an earlier
    version of this function conflated them, with the result that the
    agreement rate could never be computed at all:

    - the *work list* answers "what should a human look at next?", so it
      must exclude pairs already reviewed;
    - the *audit sample* is a measurement instrument, and the agreement rate
      is computed over exactly the reviewed members of it.

    Drawing the sample from the work list's pool made those mutually
    exclusive by construction -- every sampled record was, definitionally,
    one with no override, so the rate looked up overrides that could not
    exist and returned ``None`` forever. Reviewing a sampled record removed
    it from the pool and the next run simply re-drew a different sample.

    So the sample is drawn over **every unambiguously coded record for the
    dimension, reviewed or not**, and is stable while the corpus and rule
    version are: it is a function of ``(project, dimension, rule_version,
    corpus)``, which is exactly what :func:`audit_sample_seed` digests.
    Reviewing progress changes which sampled records are still *outstanding*
    -- and only those become queue entries -- never which records were
    designated.
    """
    uncoded_entries: list[QueueEntry] = []
    conflicting_entries: list[QueueEntry] = []
    audit_entries: list[QueueEntry] = []
    audit_seeds: dict[str, int] = {}
    audit_samples: dict[str, tuple[str, ...]] = {}

    for rule_file in sorted(rule_files, key=lambda rf: (rf.dimension, rf.version, str(rf.path))):
        dimension = schema.dimension(rule_file.dimension)
        effective = result.effective_categories(dimension.id)
        audit_pool: list[str] = []

        for record_id in result.record_ids:
            reviewed = (record_id, dimension.id) in result.human_reviewed
            # The audit pool is built over *every* unambiguously coded
            # record, reviewed or not -- that is what makes the sample
            # stable as reviewing proceeds (ADR 0023 Decision 5b). The
            # rule-assigned view is the right basis for it: an audit asks
            # "were the rules right about this record?", so a record's
            # eligibility must not change the moment someone answers.
            rule_view = result.rule_categories(record_id, dimension.id)
            if rule_view and not (not dimension.multi_label and len(rule_view) > 1):
                audit_pool.append(record_id)

            if reviewed:
                continue  # a human verdict already stands: not outstanding work
            categories = effective.get(record_id, ())
            if not categories:
                uncoded_entries.append(QueueEntry(record_id, dimension.id, "uncoded", ()))
            elif not dimension.multi_label and len(categories) > 1:
                conflicting_entries.append(
                    QueueEntry(record_id, dimension.id, "conflicting", categories)
                )

        seed = audit_sample_seed(
            project_slug=project_slug,
            dimension_id=dimension.id,
            rule_version=rule_file.version,
            record_ids=result.record_ids,
        )
        audit_seeds[dimension.id] = seed
        # `math.ceil`, not `round`: banker's rounding gives 0 for a
        # 5-record pool at 10% and 2 for a 15-record pool, so a caption
        # reading "a 10% audit sample" would be wrong in both directions.
        # Ceiling also guarantees a non-empty sample wherever a pool
        # exists at all, which is what makes the rate reachable on a
        # small corpus.
        sample_size = math.ceil(len(audit_pool) * audit_fraction)
        sampled: list[str] = []
        if sample_size:
            sampled = sorted(random.Random(seed).sample(sorted(audit_pool), k=sample_size))
        audit_samples[dimension.id] = tuple(sampled)
        for record_id in sampled:
            # Only the *outstanding* members become work. The designated set
            # above is what the agreement rate measures over.
            if (record_id, dimension.id) in result.human_reviewed:
                continue
            audit_entries.append(
                QueueEntry(record_id, dimension.id, "audit_sample", effective.get(record_id, ()))
            )

    uncoded_entries.sort(key=lambda entry: (entry.dimension, entry.record_id))
    conflicting_entries.sort(key=lambda entry: (entry.dimension, entry.record_id))
    audit_entries.sort(key=lambda entry: (entry.dimension, entry.record_id))

    return ReviewQueue(
        entries=tuple(uncoded_entries) + tuple(conflicting_entries) + tuple(audit_entries),
        audit_seeds=audit_seeds,
        audit_samples=audit_samples,
    )


def audit_agreement_rate(
    result: CodingResult,
    overrides_fold: Mapping[OverrideFoldKey, OverrideEvent],
    queue: ReviewQueue,
    dimension_id: str,
) -> float | None:
    """The audit sample's agreement rate for one dimension -- computed, not assumed.

    ADR 0023 Decision 7 / BUILD_PLAN's ``test_audit__disagreement_rate__is_computed_not_assumed``:
    this is derived entirely from override events actually recorded against
    audit-sampled records, never a value a caller supplies.

    Args:
        result: A :func:`prismabib.taxonomy.coder.code` result.
        overrides_fold: :func:`prismabib.taxonomy.overrides.fold_override_events`'s
            result -- the current override for every ``(record_id, dimension)``.
        queue: The review queue :func:`build_review_queue` produced for the
            same ``result``.
        dimension_id: Which dimension to compute the rate for.

    Returns:
        ``agreements / judged`` over every audit-sampled record for
        ``dimension_id`` that has since received a human override, where an
        "agreement" is the override's category set exactly equalling the
        rule-assigned category set it replaced. Measured over
        :attr:`ReviewQueue.audit_samples` -- the *designated* set -- not over
        the queue's outstanding ``audit_sample`` entries, which by
        construction contain no reviewed record at all. ``None`` if no
        designated record for this dimension has been reviewed yet -- there is nothing
        to compute a rate from, and reporting ``0.0`` would misstate "no
        evidence yet" as "0% agreement".
    """
    agreements = 0
    judged = 0
    for record_id in queue.audit_samples.get(dimension_id, ()):
        override = overrides_fold.get((record_id, dimension_id))
        if override is None:
            continue  # designated for audit, not yet reviewed
        judged += 1
        # `result.rule_categories`, not a scan of `result.assignments`: the
        # coder drops a rule row once a human verdict stands, so scanning
        # the surviving assignments returns the empty set for exactly the
        # records being judged -- scoring every agreeing reviewer as a
        # disagreement (ADR 0023 Decision 5b).
        if frozenset(override.categories) == result.rule_categories(record_id, dimension_id):
            agreements += 1
    if judged == 0:
        return None
    return agreements / judged


@dataclass(frozen=True)
class DimensionCoverage:
    """One dimension's coverage summary: how much of the corpus is coded, and by whom.

    Attributes:
        dimension: The dimension this summary is over.
        corpus_size: ``|C|`` (or whichever stage was coded).
        pct_coded: Percentage of records with at least one effective
            category (rule- or human-assigned).
        pct_by_rule: Percentage coded by a rule with no human override.
        pct_by_human: Percentage carrying a human override with a non-empty
            category set.
        pct_reviewed_none: Percentage carrying a human override that
            asserted ``categories: ()`` -- reviewed, deliberately empty.
        pct_uncoded: Percentage with no rule match and no human review at all.
        audit_agreement_rate: See :func:`audit_agreement_rate`.
        audit_agreement_by_band: The same rate, split by the confidence the
            rule file declared for the categories that fired (ADR 0023
            Decision 5c) -- ``band label -> rate or None``. A single
            corpus-wide rate is a weighted average over every firing rule,
            so it moves whenever the *mix* of categories in a corpus moves
            and no rule has changed; the bands say which rules the number is
            actually about.
        field_diagnostics: This dimension's :class:`~prismabib.taxonomy.coder.FieldDiagnostic`\\ s.
    """

    dimension: str
    corpus_size: int
    pct_coded: float
    pct_by_rule: float
    pct_by_human: float
    pct_reviewed_none: float
    pct_uncoded: float
    audit_agreement_rate: float | None
    audit_agreement_by_band: Mapping[str, float | None]
    field_diagnostics: tuple[FieldDiagnostic, ...]


@dataclass(frozen=True)
class CoverageReport:
    """The full coverage/audit report: one :class:`DimensionCoverage` per rule file's dimension."""

    dimensions: tuple[DimensionCoverage, ...]


def _percentage(count: int, corpus_size: int) -> float:
    """``count`` as a percentage of ``corpus_size``, or ``0.0`` on an empty corpus."""
    return (count / corpus_size * 100.0) if corpus_size else 0.0


#: Confidence bands the audit agreement rate is reported across (ADR 0023
#: Decision 5c). Half-open on the lower bound, so every declared confidence
#: in ``[0.0, 1.0]`` lands in exactly one band.
#:
#: Filtering the audit *pool* by a threshold was the obvious alternative and
#: is backwards: it leaves the low-confidence rules -- the ones whose author
#: has already written down "I expect this to be wrong sometimes" --
#: permanently unaudited, which is precisely where evidence is worth most.
CONFIDENCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("high (>=0.85)", 0.85, 1.01),
    ("medium (0.7-0.85)", 0.70, 0.85),
    ("low (<0.7)", 0.0, 0.70),
)


def audit_agreement_by_band(
    result: CodingResult,
    overrides_fold: Mapping[OverrideFoldKey, OverrideEvent],
    queue: ReviewQueue,
    rule_file: CompiledRuleFile,
    dimension_id: str,
) -> dict[str, float | None]:
    """The audit agreement rate, split by the confidence the rules declared.

    Args:
        result: The coding result the queue was built from.
        overrides_fold: The folded override events.
        queue: The queue carrying the designated audit samples.
        rule_file: The compiled rule file, for its declared confidences.
        dimension_id: Which dimension to report.

    Returns:
        ``band label -> rate``, ``None`` for a band no reviewed record falls
        in. A record is placed by the **lowest** confidence among the
        categories the rules assigned it: a record coded partly by a 0.6
        rule is only as trustworthy as that rule, and reporting it in the
        high band would launder the weakest evidence through the strongest.

        Before this, ``confidence`` was a field a rule author could set to
        anything -- including ``0.0`` -- that no code path consumed.
    """
    confidence_of = {category.id: category.confidence for category in rule_file.categories}
    buckets: dict[str, list[bool]] = {label: [] for label, _, _ in CONFIDENCE_BANDS}
    for record_id in queue.audit_samples.get(dimension_id, ()):
        override = overrides_fold.get((record_id, dimension_id))
        if override is None:
            continue
        rule_view = result.rule_categories(record_id, dimension_id)
        confidences = [confidence_of.get(category, 0.0) for category in rule_view]
        weakest = min(confidences) if confidences else 0.0
        for label, low, high in CONFIDENCE_BANDS:
            if low <= weakest < high:
                buckets[label].append(frozenset(override.categories) == rule_view)
                break
    return {
        label: (sum(judged) / len(judged) if judged else None) for label, judged in buckets.items()
    }


def build_coverage_report(
    project: Project,
    result: CodingResult,
    schema: TaxonomySchema,
    rule_files: Sequence[CompiledRuleFile],
    overrides: Sequence[OverrideEvent],
) -> CoverageReport:
    """Build the coverage/audit report BUILD_PLAN's Stage 8 acceptance criteria require.

    Args:
        project: The owning project (its slug feeds the audit seed).
        result: A :func:`prismabib.taxonomy.coder.code` result.
        schema: The project's declared taxonomy dimensions.
        rule_files: The compiled rule files to report on.
        overrides: Every human override event on record.

    Returns:
        The report: percentage coded per dimension, the rule-vs-human split,
        and the audit agreement rate.
    """
    overrides_fold = fold_override_events(overrides)
    queue = build_review_queue(result, schema, rule_files, project_slug=project.slug)
    corpus_size = len(result.record_ids)

    dimensions: list[DimensionCoverage] = []
    for rule_file in sorted(rule_files, key=lambda rf: (rf.dimension, rf.version, str(rf.path))):
        dimension = schema.dimension(rule_file.dimension)
        effective = result.effective_categories(dimension.id)

        n_rule = n_human = n_reviewed_none = 0
        for record_id in result.record_ids:
            reviewed = (record_id, dimension.id) in result.human_reviewed
            categories = effective.get(record_id, ())
            if reviewed and not categories:
                n_reviewed_none += 1
            elif reviewed:
                n_human += 1
            elif categories:
                n_rule += 1
        n_uncoded = corpus_size - n_rule - n_human - n_reviewed_none

        dimensions.append(
            DimensionCoverage(
                dimension=dimension.id,
                corpus_size=corpus_size,
                pct_coded=_percentage(n_rule + n_human, corpus_size),
                pct_by_rule=_percentage(n_rule, corpus_size),
                pct_by_human=_percentage(n_human, corpus_size),
                pct_reviewed_none=_percentage(n_reviewed_none, corpus_size),
                pct_uncoded=_percentage(n_uncoded, corpus_size),
                audit_agreement_rate=audit_agreement_rate(
                    result, overrides_fold, queue, dimension.id
                ),
                audit_agreement_by_band=audit_agreement_by_band(
                    result, overrides_fold, queue, rule_file, dimension.id
                ),
                field_diagnostics=tuple(
                    diagnostic
                    for diagnostic in result.field_diagnostics
                    if diagnostic.dimension == dimension.id
                ),
            )
        )

    return CoverageReport(dimensions=tuple(dimensions))


__all__ = [
    "CONFIDENCE_BANDS",
    "DEFAULT_AUDIT_FRACTION",
    "CoverageReport",
    "DimensionCoverage",
    "QueueEntry",
    "QueuePriority",
    "ReviewQueue",
    "audit_agreement_by_band",
    "audit_agreement_rate",
    "audit_sample_seed",
    "build_coverage_report",
    "build_review_queue",
]
