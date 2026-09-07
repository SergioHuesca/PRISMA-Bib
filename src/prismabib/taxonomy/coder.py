"""The taxonomy coder: a pure function over ``(corpus, rules, overrides)`` (ADR 0023 Decision 1).

    assignments = code(corpus_records, rule_files, overrides)

Nothing here is stored. ADR 0018 fixed the rule that governs this stage: any
table Layer 1 holds must be a function of Layer 0, and a taxonomy assignment
is a function of Layer 1 *and* the rule files -- which live in the project's
own git repository, versioned on their own cadence, deliberately outside
Layer 0. So :func:`code` recomputes on every call, over whatever
``(rule_files, overrides)`` its caller hands it, and three of Stage 8's
acceptance criteria (deterministic, idempotent, overrides survive a rule
version bump) fall out as properties of a pure function rather than
invariants a store would have to maintain.

**Fold precedence (ADR 0023 Decision 3).** A human override, when one
exists for ``(record_id, dimension)``, *replaces* every rule-assigned
category for that pair -- it is never merged with the rules' output. This
is why :class:`CodingResult` tracks :attr:`~CodingResult.human_reviewed`
separately from its assignment rows: an override asserting ``categories: ()``
contributes no assignment row at all, and without a separate marker that
would be indistinguishable from "no rule fired and nobody has looked".

**Determinism.** No ``hash()`` (varies with ``PYTHONHASHSEED``), no clock, no
unseeded ``random``, and every output list is explicitly sorted rather than
left in dict/set iteration order -- the whole point of a pure function is
that two independently-opened :class:`~prismabib.store.load.Corpus` handles
over the same rule files and overrides produce byte-identical results.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import polars as pl

from prismabib.errors import ValidationError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus
from prismabib.taxonomy.overrides import OverrideEvent, OverrideFoldKey, fold_override_events
from prismabib.taxonomy.rules import CompiledCategoryRule, CompiledRuleFile, RuleField
from prismabib.taxonomy.schema import CountingUnit, TaxonomySchema

#: Where an assignment came from: a rule match, or a human override that
#: replaced the dimension's rule output (ADR 0023 Decision 3).
Source = Literal["rule", "human"]

#: The four fields a rule (or the coder's own coverage diagnostics) may
#: reference, mirroring :data:`prismabib.taxonomy.rules.RuleField`.
_FIELDS: tuple[RuleField, ...] = ("title", "abstract", "author_keywords", "index_keywords")


@dataclass(frozen=True)
class Assignment:
    """One ``(record, dimension, category)`` assignment, with its provenance.

    Attributes:
        record_id: The bibliographic record this assignment applies to.
        dimension: Which taxonomy dimension.
        category: The assigned category.
        rule_id: The category id of the rule that fired, when
            ``source == "rule"``; ``None`` for a human assignment (an
            override event carries no rule provenance -- it replaced the
            rules' output rather than extending it).
        rule_version: The rule file's ``version`` when ``source == "rule"``;
            ``None`` for a human assignment.
        confidence: The category rule's declared confidence when
            ``source == "rule"``; ``None`` for a human assignment (a human
            judgement is not scored the way a regex match is).
        source: ``"rule"`` or ``"human"``.
    """

    record_id: str
    dimension: str
    category: str
    rule_id: str | None
    rule_version: str | None
    confidence: float | None
    source: Source


@dataclass(frozen=True)
class FieldDiagnostic:
    """ADR 0023 Decision 6: a valid rule field that carries no content on this corpus.

    Not a load-time error -- the rule may be correct for the next corpus,
    and refusing it would make rule files un-shareable between reviews. The
    live corpus is the canonical example: the Scopus Search API's
    ``view=COMPLETE`` response never carries indexed keywords, so any rule
    referencing ``index_keywords`` matches nothing there, silently, forever
    -- valid, and worth surfacing in the coverage report.

    Attributes:
        dimension: The rule file's declared dimension.
        field: The field that carries no content anywhere in the coded set.
        rule_path: The rule file that referenced it, as a string (a
            :class:`~pathlib.Path` is not hashable identically across two
            otherwise-equal objects built from different string forms, which
            would defeat the deduplication :func:`code` relies on).
    """

    dimension: str
    field: RuleField
    rule_path: str


@dataclass(frozen=True)
class CodingResult:
    """The output of one :func:`code` call: every assignment, plus what the fold needs.

    Attributes:
        stage: The :class:`~prismabib.stage.PrismaStage` this result was
            computed over.
        record_ids: Every record id this run considered, sorted -- ``|C|``
            (or whichever stage was requested) by construction.
        assignments: Every ``(record, dimension, category)`` assignment,
            rule and human alike, in a fixed deterministic order (sorted by
            ``(dimension, record_id, category, source)``). An override's
            ``categories: ()`` contributes no row here; see
            :attr:`human_reviewed`.
        field_diagnostics: See :class:`FieldDiagnostic`.
        human_reviewed: Every ``(record_id, dimension)`` pair with at least
            one override event, whether or not its ``categories`` was empty.
            This is what lets :meth:`uncoded` distinguish "a human looked and
            confirmed nothing applies" from "nobody looked" (ADR 0023
            Decision 3/4).
        superseded_rule_assignments: The rule rows an override replaced --
            every row dropped from :attr:`assignments` because a human
            verdict stands for its ``(record, dimension)``. Same order key.

            Retained rather than discarded because dropping them makes the
            audit agreement rate unanswerable (ADR 0023 Decision 5b): the
            rate asks "did the reviewer agree with what the rules said?",
            and after an override the rules' answer is otherwise gone. A
            reviewer who *agreed* would then be compared against an empty
            set and scored as a disagreement -- ten unanimous agreements
            reading as 0%, which is a precise-looking wrong number in the
            one artefact whose job is validating the rules.

            It also gives the review queue somewhere to read "what the rules
            currently say" for a record already reviewed once, which ADR
            0023 Consequence 3 promises a reviewer.
    """

    stage: PrismaStage
    record_ids: tuple[str, ...]
    assignments: tuple[Assignment, ...]
    field_diagnostics: tuple[FieldDiagnostic, ...]
    human_reviewed: frozenset[OverrideFoldKey]
    superseded_rule_assignments: tuple[Assignment, ...] = ()

    def rule_categories(self, record_id: str, dimension_id: str) -> frozenset[str]:
        """What the rules assign for one pair, whether or not a human overrode it.

        Args:
            record_id: The record.
            dimension_id: The dimension.

        Returns:
            The rule-assigned category set. Reads :attr:`assignments` for a
            pair no human has touched and
            :attr:`superseded_rule_assignments` for one they have, so the
            answer is "what the rules say", never "what survived the
            override" -- see that attribute for why the difference matters.
        """
        source = (
            self.superseded_rule_assignments
            if (record_id, dimension_id) in self.human_reviewed
            else self.assignments
        )
        return frozenset(
            assignment.category
            for assignment in source
            if assignment.record_id == record_id
            and assignment.dimension == dimension_id
            and assignment.source == "rule"
        )

    def effective_categories(self, dimension_id: str) -> dict[str, tuple[str, ...]]:
        """The fold's answer for one dimension: each record's current category set.

        Args:
            dimension_id: Which dimension to fold.

        Returns:
            ``record_id -> categories``, for every record in
            :attr:`record_ids`. A record with no assignment row for
            ``dimension_id`` maps to ``()`` -- which may mean "uncoded" or
            "a human said nothing applies"; see :meth:`uncoded` for the
            distinction.
        """
        by_record: dict[str, list[str]] = {record_id: [] for record_id in self.record_ids}
        for assignment in self.assignments:
            if assignment.dimension != dimension_id:
                continue
            by_record.setdefault(assignment.record_id, []).append(assignment.category)
        return {record_id: tuple(categories) for record_id, categories in by_record.items()}

    def uncoded(self, dimension_id: str) -> tuple[str, ...]:
        """Records with zero effective categories for ``dimension_id`` *and* no human review.

        Args:
            dimension_id: Which dimension to check.

        Returns:
            Sorted record ids: no rule fired, and no override (not even an
            empty-``categories`` one) exists for them in this dimension.
        """
        effective = self.effective_categories(dimension_id)
        return tuple(
            sorted(
                record_id
                for record_id in self.record_ids
                if not effective.get(record_id)
                and (record_id, dimension_id) not in self.human_reviewed
            )
        )

    def as_dataframe(self) -> pl.DataFrame:
        """This result's :attr:`assignments`, as a :class:`polars.DataFrame`.

        Returns:
            Columns ``record_id``, ``dimension``, ``category``, ``rule_id``,
            ``rule_version``, ``confidence``, ``source`` -- BUILD_PLAN's own
            ``taxonomy_assignments`` shape (ADR 0005) -- in the same
            deterministic order as :attr:`assignments`.
        """
        columns = (
            "record_id",
            "dimension",
            "category",
            "rule_id",
            "rule_version",
            "confidence",
            "source",
        )
        rows = [
            (
                a.record_id,
                a.dimension,
                a.category,
                a.rule_id,
                a.rule_version,
                a.confidence,
                a.source,
            )
            for a in self.assignments
        ]
        return pl.DataFrame(rows, schema=columns, orient="row", infer_schema_length=None)


def _joined_keywords(keywords: pl.DataFrame) -> dict[str, str]:
    """Join every record's raw keyword terms into one search string per record.

    Args:
        keywords: A :meth:`~prismabib.store.load.Corpus.keywords`-shaped
            frame: ``record_id``, ``keyword_id``, ``term_raw``, ``term_norm``, ``kind``.

    Returns:
        ``record_id -> "term | term | ..."``, using the raw (not
        normalised) form, matching Scopus's own ``authkeywords`` separator
        convention. A record with no keyword rows of this kind is simply
        absent -- callers default to ``""``.
    """
    if keywords.height == 0:
        return {}
    grouped = keywords.group_by("record_id").agg(pl.col("term_raw"))
    return {row["record_id"]: " | ".join(row["term_raw"]) for row in grouped.iter_rows(named=True)}


def _field_texts(
    records: pl.DataFrame, author_keywords: pl.DataFrame, index_keywords: pl.DataFrame
) -> dict[str, dict[RuleField, str]]:
    """Build the per-record, per-field search text a rule pattern is matched against.

    Args:
        records: :meth:`~prismabib.store.load.Corpus.records`'s frame.
        author_keywords: :meth:`~prismabib.store.load.Corpus.keywords`\\
            (``kind="author"``)'s frame.
        index_keywords: :meth:`~prismabib.store.load.Corpus.keywords`\\
            (``kind="index"``)'s frame.

    Returns:
        ``record_id -> {field -> text}`` for every record in ``records``.
        ``title``/``abstract`` come straight from ``records`` (``""`` for a
        ``NULL`` abstract); ``author_keywords``/``index_keywords`` come from
        :func:`_joined_keywords` (``""`` for a record with none of that kind).
    """
    if records.height == 0:
        return {}
    author_by_record = _joined_keywords(author_keywords)
    index_by_record = _joined_keywords(index_keywords)
    texts: dict[str, dict[RuleField, str]] = {}
    for row in records.select("record_id", "title", "abstract").iter_rows(named=True):
        record_id = row["record_id"]
        texts[record_id] = {
            "title": row["title"] or "",
            "abstract": row["abstract"] or "",
            "author_keywords": author_by_record.get(record_id, ""),
            "index_keywords": index_by_record.get(record_id, ""),
        }
    return texts


def _category_matches(category: CompiledCategoryRule, text: Mapping[RuleField, str]) -> bool:
    """Whether ``category`` fires against one record's field texts.

    Args:
        category: The compiled category rule.
        text: ``{field -> text}`` for the record being tested.

    Returns:
        ``True`` iff any ``any`` clause matches and no ``none`` clause
        matches (ADR 0005: ``none`` is a negative lookaside -- "transformer
        oil" suppresses an otherwise-fired "transformer").
    """
    matched = any(clause.pattern.search(text[clause.field]) is not None for clause in category.any)
    if not matched:
        return False
    suppressed = any(
        clause.pattern.search(text[clause.field]) is not None for clause in category.none
    )
    return not suppressed


def _referenced_fields(rule_file: CompiledRuleFile) -> frozenset[RuleField]:
    """Every field at least one category rule in ``rule_file`` references."""
    return frozenset(
        clause.field
        for category in rule_file.categories
        for clause in (*category.any, *category.none)
    )


def code(
    corpus: Corpus,
    rule_files: Sequence[CompiledRuleFile],
    overrides: Sequence[OverrideEvent],
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
) -> CodingResult:
    """Code ``corpus`` against ``rule_files``, then fold ``overrides`` over the result.

    ADR 0023 Decision 1's ``assignments = code(corpus_records, rule_files,
    overrides)``, exactly: a pure function of its three arguments -- the
    same corpus content, rule files and overrides always produce the same
    :class:`CodingResult`, and running it twice never duplicates anything,
    because nothing is written. No :class:`~prismabib.taxonomy.schema.TaxonomySchema`
    is needed here: a rule file already carries its own dimension
    (:attr:`~prismabib.taxonomy.rules.CompiledRuleFile.dimension`,
    cross-checked against the schema at :func:`~prismabib.taxonomy.rules.load_rule_file`
    time) and an override event already carries its own
    (:attr:`~prismabib.taxonomy.overrides.OverrideEvent.dimension`,
    validated at :meth:`~prismabib.taxonomy.overrides.OverrideLog.append`
    time) -- this function only ever applies what load time already
    validated.

    Args:
        corpus: The corpus to read via :meth:`~prismabib.store.load.Corpus.records`
            and :meth:`~prismabib.store.load.Corpus.keywords`.
        rule_files: Every compiled rule file to apply. Each is applied
            independently against ``corpus`` -- a rule file describes one
            dimension (:attr:`~prismabib.taxonomy.rules.CompiledRuleFile.dimension`),
            and this function does not require every declared dimension to
            have a rule file.
        overrides: Every human override event on record for this project
            (typically :meth:`prismabib.taxonomy.overrides.OverrideLog.load`'s
            result) -- folded per ``(record_id, dimension)``, latest wins.
        stage: Which named PRISMA-flow record set to code. Defaults to
            :attr:`~prismabib.stage.PrismaStage.INCLUDED` (``C``).

    Returns:
        The coded, overridden result.
    """
    records = corpus.records(stage)
    author_keywords = corpus.keywords("author", stage)
    index_keywords = corpus.keywords("index", stage)
    texts = _field_texts(records, author_keywords, index_keywords)
    record_ids = tuple(sorted(texts))

    field_has_content: dict[RuleField, bool] = {
        field: any(texts[record_id][field] for record_id in record_ids) for field in _FIELDS
    }

    rule_rows: list[Assignment] = []
    diagnostics: set[FieldDiagnostic] = set()
    for rule_file in sorted(rule_files, key=lambda rf: (rf.dimension, rf.version, str(rf.path))):
        for field in sorted(_referenced_fields(rule_file)):
            if not field_has_content[field]:
                diagnostics.add(
                    FieldDiagnostic(
                        dimension=rule_file.dimension, field=field, rule_path=str(rule_file.path)
                    )
                )
        for record_id in record_ids:
            text = texts[record_id]
            for category in rule_file.categories:
                if _category_matches(category, text):
                    rule_rows.append(
                        Assignment(
                            record_id=record_id,
                            dimension=rule_file.dimension,
                            category=category.id,
                            rule_id=category.id,
                            rule_version=rule_file.version,
                            confidence=category.confidence,
                            source="rule",
                        )
                    )

    override_fold = fold_override_events(overrides)
    human_reviewed = frozenset(override_fold.keys())

    final_assignments = [
        assignment
        for assignment in rule_rows
        if (assignment.record_id, assignment.dimension) not in human_reviewed
    ]
    # Kept, not dropped: the audit agreement rate compares an override
    # against the rule verdict it replaced (ADR 0023 Decision 5b).
    superseded = [
        assignment
        for assignment in rule_rows
        if (assignment.record_id, assignment.dimension) in human_reviewed
    ]
    superseded.sort(key=lambda a: (a.dimension, a.record_id, a.category, a.source))
    for (record_id, dimension_id), event in override_fold.items():
        for human_category in event.categories:
            final_assignments.append(
                Assignment(
                    record_id=record_id,
                    dimension=dimension_id,
                    category=human_category,
                    rule_id=None,
                    rule_version=None,
                    confidence=None,
                    source="human",
                )
            )

    # A total, explicit order -- never dict/set iteration order (§3.7.3) --
    # so two independently-computed CodingResults over identical inputs
    # compare equal, which is what "idempotent" and "deterministic" mean for
    # a pure function (S08-AC2).
    final_assignments.sort(key=lambda a: (a.dimension, a.record_id, a.category, a.source))

    return CodingResult(
        stage=stage,
        record_ids=record_ids,
        assignments=tuple(final_assignments),
        field_diagnostics=tuple(
            sorted(diagnostics, key=lambda d: (d.dimension, d.field, d.rule_path))
        ),
        human_reviewed=human_reviewed,
        superseded_rule_assignments=tuple(superseded),
    )


@dataclass(frozen=True)
class Distribution:
    """One dimension's coded distribution, in one declared :class:`CountingUnit`.

    Attributes:
        dimension: The dimension this distribution is over.
        counting_unit: The unit every count in :attr:`counts` is in.
        multi_label: Whether the dimension allows more than one category per record.
        counts: ``category -> count``, plus the two non-category buckets
            ``"uncoded"`` (no rule fired, nobody reviewed) and
            ``"reviewed_none"`` (a human override asserted ``categories: ()``
            -- ADR 0023 Decision 4's ``uncoded`` bucket, split so that "nobody
            looked" and "a human looked and found nothing" are never
            conflated into one number).
        corpus_size: ``|C|`` (or whichever stage was coded) -- the record
            count this distribution was built over.
    """

    dimension: str
    counting_unit: CountingUnit
    multi_label: bool
    counts: Mapping[str, int]
    corpus_size: int

    def caption(self) -> str:
        """A one-sentence caption stating ``n`` and, mandatorily, the counting unit.

        BUILD_PLAN: "Every taxonomy figure must state its unit in the
        auto-generated caption." :attr:`counting_unit` `KEYWORD_MENTIONS`
        gets an explicit "NOT a paper distribution" clause -- the caption
        text is the safeguard the source manuscript's taxonomy figure did
        not have.

        Returns:
            The caption text, ending in a period.
        """
        if self.counting_unit is CountingUnit.KEYWORD_MENTIONS:
            return (
                f"{self.dimension} keyword-mention counts (n={self.corpus_size} records "
                "considered); raw term frequency, NOT a paper distribution."
            )
        if self.counting_unit is CountingUnit.ASSIGNMENTS:
            return (
                f"{self.dimension} assignment counts, counting_unit=assignments "
                f"(n={self.corpus_size}; a record may contribute to more than one category)."
            )
        return (
            f"{self.dimension} paper distribution, counting_unit=papers "
            f"(n={self.corpus_size}); multi_label={'true' if self.multi_label else 'false'}."
        )


def distribution(
    result: CodingResult,
    schema: TaxonomySchema,
    dimension_id: str,
    counting_unit: CountingUnit,
) -> Distribution:
    """Build one dimension's counted distribution, enforcing the counting-unit invariant.

    ADR 0023 Decision 4: a ``multi_label: false`` dimension counted in
    :attr:`~prismabib.taxonomy.schema.CountingUnit.PAPERS` must sum to
    exactly ``|C|`` -- every record lands in exactly one bucket, the closed
    category set plus ``"uncoded"`` plus ``"reviewed_none"``. Rather than
    relax that assertion for a deliberately (or accidentally) over-assigned
    rule set, this function makes the violation impossible to sum silently:
    a record carrying more than one category under those conditions raises
    immediately, which is the check the source manuscript's taxonomy figure
    did not have.

    Args:
        result: A :func:`code` result.
        schema: The project's declared taxonomy dimensions.
        dimension_id: Which dimension to summarise.
        counting_unit: The counting unit to summarise in -- read from the
            relevant rule file's :attr:`~prismabib.taxonomy.rules.CompiledRuleFile.counting_unit`,
            not inferred here, since a caller may legitimately want a
            dimension's distribution under a unit no rule file for it has
            declared yet (e.g. auditing what ``PAPERS`` *would* look like).

    Returns:
        The distribution.

    Raises:
        ValidationError: If ``counting_unit`` is
            :attr:`~prismabib.taxonomy.schema.CountingUnit.PAPERS`,
            ``dimension_id``'s :attr:`~prismabib.taxonomy.schema.Dimension.multi_label`
            is ``False``, and some record carries more than one effective
            category -- the over-assignment BUILD_PLAN's Stage 8 acceptance
            criteria require to fail loudly.
    """
    dimension = schema.dimension(dimension_id)
    effective = result.effective_categories(dimension_id)

    counts: dict[str, int] = dict.fromkeys(dimension.categories, 0)
    counts["uncoded"] = 0
    counts["reviewed_none"] = 0

    for record_id in result.record_ids:
        categories = effective.get(record_id, ())
        reviewed = (record_id, dimension_id) in result.human_reviewed
        if not categories:
            counts["reviewed_none" if reviewed else "uncoded"] += 1
            continue
        if (
            counting_unit is CountingUnit.PAPERS
            and not dimension.multi_label
            and len(categories) > 1
        ):
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise ValidationError(
                f"dimension {dimension_id!r} is multi_label=false with counting_unit=papers, "
                f"but record {record_id!r} carries {len(categories)} categories "
                f"{sorted(categories)} -- a PAPERS distribution over a single-label dimension "
                "must assign at most one category per record; summing this would silently "
                "overstate every category's share (ADR 0023 Decision 4)."
            )
            # pragma: no mutate end
        for category in categories:
            counts[category] = counts.get(category, 0) + 1

    return Distribution(
        dimension=dimension_id,
        counting_unit=counting_unit,
        multi_label=dimension.multi_label,
        counts=counts,
        corpus_size=len(result.record_ids),
    )


__all__ = [
    "Assignment",
    "CodingResult",
    "Distribution",
    "FieldDiagnostic",
    "Source",
    "code",
    "distribution",
]
