"""The measurable form of "no relevance ranking" (BUILD_PLAN §Stage 5, line 1085).

BUILD_PLAN calls this out specifically: "Spearman correlation over generated
corpora is within a tolerance band of zero. This is the test that enforces
'no relevance ranking' as a *measurable* property rather than a comment."
Requirement 1 (line 1070) is a bias control -- a reviewer who is shown the
most-cited papers first calibrates on them -- and a comment saying so
protects nobody. A correlation does.

**The experiment.** Twelve generated corpora of three hundred records each
(3,600 orderings). Corpus ``k`` has its own slug and its own record ids;
every record carries a citation count, and the counts are assigned
**strictly increasing along the id sequence**. Then, for each corpus, the
Spearman rank correlation between a record's position in the screening
queue and its citation count is computed.

Making the citation count monotone in the record id is what gives the
statistic its power, and it is deliberate: it makes one number detect *both*
ways this requirement can actually be broken.

- Ordering by citation count -- the ranking BUILD_PLAN forbids -- gives
  rho = +1 or -1.
- Not ordering at all (``tuple(sorted(eligible))``, the shape a hurried
  implementation takes, or worse, iterating the engine's ``frozenset``)
  gives rho = +1 here, because id order *is* citation order in these
  corpora.

**The tolerance band, and why it is these numbers.** Under the null
hypothesis that the queue is an arbitrary permutation with respect to
citation count, Spearman's rho has mean 0 and standard deviation
``1 / sqrt(n - 1)``; at n = 300 that is 0.0578. The bands are:

- ``PER_CORPUS_BAND = 0.30`` -- 5.2 standard deviations. An arbitrary
  ordering exceeds it with probability about 2e-7 per corpus, while any
  ranked ordering sits at 1.0, so the band separates the two hypotheses by a
  factor of more than three with room to spare in both directions.
- ``MEAN_ABSOLUTE_BAND = 0.12`` on the mean of ``|rho|`` across the twelve
  corpora. The per-corpus band is a maximum, and a maximum over twelve draws
  is a noisy statistic; the mean is the stable one. Its null expectation is
  ``0.0578 * sqrt(2/pi) = 0.046`` with a standard error of 0.010, so 0.12 is
  about seven standard errors out -- while a ranked ordering would put it at
  1.0.

Measured on the implementation as written: ``max|rho| = 0.1889``,
``mean|rho| = 0.0611``. Both bands therefore have real headroom over the
observed values *and* remain far from the values a ranking defect produces.
A band of, say, 0.05 would sit below the null distribution's own spread and
fail for a perfectly unbiased rule; a band of 0.9 would pass anything short
of a perfect sort.

**Why this is deterministic rather than Hypothesis-driven.** The ordering
rule is a pure function of ``(slug, record_id)``, so these 3,600 numbers are
a fixed measurement, not a sample: the test cannot flake, and a rule change
that pushed any corpus outside the band would fail on every machine at once
rather than on one in twenty runs. A correlation tolerance is also only
meaningful at a known sample size, and drawing ``n`` from a strategy would
make the band mean something different in every example.
``test_spearman__a_citation_ranked_order__exceeds_the_band`` keeps the
instrument honest by measuring a deliberately ranked order with the same
code and asserting that it lands outside the band.
"""

from __future__ import annotations

from statistics import fmean

import pytest

from prismabib.screening.queue import ordered_record_ids

#: How many generated corpora the correlation is measured over.
CORPORA = 12

#: How many records each generated corpus holds. Fixes the null distribution's
#: standard deviation at ``1 / sqrt(299) = 0.0578``, which is what makes the
#: bands below interpretable.
RECORDS_PER_CORPUS = 300

#: The largest ``|rho|`` any single corpus may show (5.2 null sigma).
PER_CORPUS_BAND = 0.30

#: The largest mean ``|rho|`` across all twelve corpora (about 7 null standard
#: errors above the null expectation of 0.046).
MEAN_ABSOLUTE_BAND = 0.12


def spearman(first: list[int], second: list[int]) -> float:
    """Spearman's rank correlation between two tie-free rankings.

    Both arguments here are permutations of ``range(n)`` -- queue positions
    and citation ranks -- so there are no ties and the closed form is exact;
    no dependency, and nothing to get subtly wrong in a tie correction that
    would never be exercised.

    Args:
        first: One ranking.
        second: The other, of equal length.

    Returns:
        ``1 - 6 * sum(d^2) / (n * (n^2 - 1))``, in ``[-1, 1]``.
    """
    n = len(first)
    sum_of_squared_differences = sum(
        (left - right) ** 2 for left, right in zip(first, second, strict=True)
    )
    return 1 - 6 * sum_of_squared_differences / (n * (n * n - 1))


def generated_corpus(index: int) -> tuple[str, dict[str, int]]:
    """Build generated corpus ``index``: a slug, and each record's citation count.

    Args:
        index: Which corpus, ``0 <= index < CORPORA``.

    Returns:
        The corpus slug, and a mapping from record id to citation count.
        Citation counts increase strictly along the id sequence -- see the
        module docstring for why that is the strongest choice rather than a
        lazy one.
    """
    record_ids = [
        f"scopus:2-s2.0-8{index:02d}{position:08d}" for position in range(RECORDS_PER_CORPUS)
    ]
    return f"generated-corpus-{index:02d}", {
        record_id: position for position, record_id in enumerate(record_ids)
    }


def citation_correlation(slug: str, citations: dict[str, int]) -> float:
    """Correlate queue position with citation count for one corpus.

    Args:
        slug: The corpus's project slug -- the ordering seed.
        citations: Every record id in the corpus, with its citation count.

    Returns:
        Spearman's rho between a record's position in
        :func:`~prismabib.screening.queue.ordered_record_ids`' output and its
        citation count.
    """
    order = ordered_record_ids(slug, citations)
    return spearman(list(range(len(order))), [citations[record_id] for record_id in order])


@pytest.mark.property
def test_queue__ordering__is_uncorrelated_with_citation_count() -> None:
    correlations = {
        slug: citation_correlation(slug, citations)
        for slug, citations in (generated_corpus(index) for index in range(CORPORA))
    }

    worst_slug, worst_rho = max(correlations.items(), key=lambda item: abs(item[1]))
    mean_absolute = fmean(abs(rho) for rho in correlations.values())

    assert abs(worst_rho) <= PER_CORPUS_BAND, (
        f"{worst_slug}: queue position correlates with citation count, rho={worst_rho:.4f}"
    )
    assert mean_absolute <= MEAN_ABSOLUTE_BAND, (
        f"mean |rho| over {CORPORA} corpora is {mean_absolute:.4f}: {correlations}"
    )


@pytest.mark.property
def test_spearman__a_citation_ranked_order__exceeds_the_band() -> None:
    _slug, citations = generated_corpus(0)
    ranked = sorted(citations, key=lambda record_id: -citations[record_id])

    rho = spearman(list(range(len(ranked))), [citations[record_id] for record_id in ranked])

    assert rho == pytest.approx(-1.0)
    assert abs(rho) > PER_CORPUS_BAND
