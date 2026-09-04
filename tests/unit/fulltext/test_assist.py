"""Tests for assisted manual fetch: identification and filing (ADR 0020 Decision 4).

Both halves of :mod:`prismabib.fulltext.assist` are held to full test
discipline (ADR 0020: "this is where a defect attaches one paper's full text
to another record"). Scores and margins are asserted exactly, never with a
loose ``>=`` -- an earlier draft of this stage's threshold check passed with
a count inflated by three, and a range assertion would have hidden it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.fulltext.assist import (
    Candidate,
    ManualDropFilingError,
    file_manual_drop,
    identify_pdf,
)
from prismabib.fulltext.resolve import ManualDropResolver, manual_drop_path

_RECORD_A = "scopus:2-s2.0-85100000001"
_RECORD_B = "scopus:2-s2.0-85100000002"
_MINIMAL_PDF = b"%PDF-1.4\n%%EOF"


# ---------------------------------------------------------------------------
# identify_pdf
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_identify_pdf__doi_on_page_matches_one_candidate__is_confident() -> None:
    """The DOI is tried first, and a single match is unambiguous by construction."""
    candidates = [
        Candidate(record_id=_RECORD_A, title="Irrelevant Title One", doi="10.1109/cvpr.2024.00123"),
        Candidate(record_id=_RECORD_B, title="Irrelevant Title Two", doi="10.1109/cvpr.2024.00456"),
    ]
    page_text = "Proceedings of CVPR 2024\nDOI: 10.1109/CVPR.2024.00123.\nAbstract: ..."

    result = identify_pdf(page_text, candidates)

    assert result.record_id == _RECORD_A
    assert result.method == "doi"
    assert result.score == 1.0
    assert result.runner_up_record_id is None
    assert result.runner_up_score == 0.0
    assert result.margin == 1.0
    assert result.confident is True


@pytest.mark.unit
def test_identify_pdf__no_doi_on_page__title_containment_is_confident() -> None:
    """DOI absent (from the page); title containment alone clears both thresholds."""
    candidates = [
        Candidate(record_id=_RECORD_A, title="Baseball Swing Detection Using Deep Learning"),
        Candidate(record_id=_RECORD_B, title="Underwater Robot Navigation System"),
    ]
    page_text = (
        "Baseball Swing Detection Using Deep Learning\n"
        "A. Author, B. Author\n\nAbstract -- we present a method for analysing broadcast "
        "footage of baseball games using a convolutional network."
    )

    result = identify_pdf(page_text, candidates)

    assert result.record_id == _RECORD_A
    assert result.method == "title"
    # 5 non-stopword title tokens (baseball, swing, detection, deep, learning --
    # "using" is a stopword), all present on the page.
    assert result.score == 1.0
    assert result.runner_up_record_id == _RECORD_B
    assert result.runner_up_score == 0.0
    assert result.margin == 1.0
    assert result.confident is True


@pytest.mark.unit
def test_identify_pdf__doi_and_title_both_inconclusive__is_not_confident() -> None:
    """Neither identification strategy succeeds: no DOI recorded, and a weak title overlap."""
    candidates = [
        Candidate(
            record_id=_RECORD_A,
            title="Deep Learning Baseball Swing Classification",
            doi=None,
        ),
        Candidate(record_id=_RECORD_B, title="Underwater Robot Navigation System", doi=None),
    ]
    page_text = (
        "This paper explores deep learning techniques for the classification "
        "of general audio signals recorded outdoors."
    )

    result = identify_pdf(page_text, candidates)

    assert result.method == "title"
    # 5 non-stopword title tokens (deep, learning, baseball, swing, classification);
    # only "deep", "learning" and "classification" appear on the page: 3/5.
    assert result.score == 3 / 5
    assert result.record_id == _RECORD_A
    assert result.runner_up_score == 0.0
    assert result.confident is False


@pytest.mark.unit
def test_identify_pdf__page_matches_no_candidate__is_not_confident() -> None:
    """A PDF belonging to neither candidate scores zero against both -- and must not be filed."""
    candidates = [
        Candidate(record_id=_RECORD_A, title="Baseball Swing Detection Using Deep Learning"),
        Candidate(record_id=_RECORD_B, title="Underwater Robot Navigation System"),
    ]
    page_text = "A Survey of Household Gardening Techniques and Seasonal Recipes"

    result = identify_pdf(page_text, candidates)

    assert result.score == 0.0
    assert result.runner_up_score == 0.0
    assert result.margin == 0.0
    assert result.confident is False
    # `identify_pdf` still names a "best" candidate (for a human prompt to
    # show), but `confident is False` is the caller's whole signal never to
    # act on it unattended -- nothing here files anything.


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC2")
def test_identify_pdf__two_near_identical_titles__margin_too_small__is_not_confident() -> None:
    """The ambiguity case ADR 0020 measured: two baseball-video titles, runner-up close behind.

    Both titles score highly (the top scorer even reaches 1.0), but the
    margin between them is the load-bearing quantity, not either score in
    isolation -- exactly the real defect this pins: a wrong match here is
    silent and durable.
    """
    candidate_pitch = Candidate(
        record_id=_RECORD_A, title="Baseball Pitch Detection and Tracking in Broadcast Video"
    )
    candidate_swing = Candidate(
        record_id=_RECORD_B, title="Baseball Swing Detection and Tracking in Broadcast Video"
    )
    # Contains every non-stopword token of `candidate_swing`'s title (baseball,
    # swing, detection, tracking, broadcast, video) and every one of
    # `candidate_pitch`'s except "pitch".
    page_text = (
        "This baseball video demonstrates swing detection and tracking of broadcast footage."
    )

    result = identify_pdf(page_text, [candidate_pitch, candidate_swing])

    assert result.record_id == _RECORD_B
    assert result.score == 1.0
    assert result.runner_up_record_id == _RECORD_A
    assert result.runner_up_score == 5 / 6
    assert result.margin == 1.0 - 5 / 6
    assert result.confident is False


@pytest.mark.unit
def test_identify_pdf__no_candidates__is_not_confident() -> None:
    result = identify_pdf("anything at all", [])

    assert result.record_id is None
    assert result.method == "none"
    assert result.score == 0.0
    assert result.confident is False


@pytest.mark.unit
def test_identify_pdf__doi_matches_more_than_one_candidate__falls_back_to_title() -> None:
    """A DOI match must be unambiguous to be trusted; two matches are not a match at all."""
    candidates = [
        Candidate(
            record_id=_RECORD_A, title="Baseball Swing Detection Using Deep Learning", doi="10.1/x"
        ),
        Candidate(record_id=_RECORD_B, title="Underwater Robot Navigation System", doi="10.1/x"),
    ]
    page_text = "10.1/x\n\nBaseball Swing Detection Using Deep Learning"

    result = identify_pdf(page_text, candidates)

    assert result.method == "title"
    assert result.record_id == _RECORD_A


# ---------------------------------------------------------------------------
# file_manual_drop
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_file_manual_drop__parent_missing__is_created(tmp_path: Path) -> None:
    fulltext_dir = tmp_path / "fulltext"
    source = tmp_path / "downloaded.pdf"
    source.write_bytes(_MINIMAL_PDF)

    destination = file_manual_drop(fulltext_dir, _RECORD_A, source)

    assert destination == manual_drop_path(fulltext_dir, _RECORD_A)
    assert destination.is_file()
    assert destination.read_bytes() == _MINIMAL_PDF
    # The source is untouched -- a copy, never a move.
    assert source.is_file()
    assert source.read_bytes() == _MINIMAL_PDF


@pytest.mark.unit
def test_file_manual_drop__target_exists__is_refused_not_overwritten(tmp_path: Path) -> None:
    fulltext_dir = tmp_path / "fulltext"
    existing = manual_drop_path(fulltext_dir, _RECORD_A)
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"%PDF-1.4\nORIGINAL\n%%EOF")

    source = tmp_path / "downloaded.pdf"
    source.write_bytes(b"%PDF-1.4\nDIFFERENT\n%%EOF")

    with pytest.raises(ManualDropFilingError):
        file_manual_drop(fulltext_dir, _RECORD_A, source)

    assert existing.read_bytes() == b"%PDF-1.4\nORIGINAL\n%%EOF"


@pytest.mark.unit
def test_file_manual_drop__source_not_a_pdf__is_refused(tmp_path: Path) -> None:
    fulltext_dir = tmp_path / "fulltext"
    source = tmp_path / "downloaded.pdf"
    source.write_bytes(b"<html>this is not a pdf</html>")

    with pytest.raises(ManualDropFilingError):
        file_manual_drop(fulltext_dir, _RECORD_A, source)

    assert not manual_drop_path(fulltext_dir, _RECORD_A).exists()


@pytest.mark.unit
def test_file_manual_drop__then_manual_drop_resolver__resolves_it(tmp_path: Path) -> None:
    """The round-trip that matters: filing and lookup must agree, proved by calling both.

    Not by reading `manual_drop_path` on both sides (a restated constant
    agrees with itself and proves nothing about drift) -- by actually filing
    through `file_manual_drop` and then asking `ManualDropResolver` for the
    asset back.
    """
    fulltext_dir = tmp_path / "fulltext"
    source = tmp_path / "downloaded.pdf"
    source.write_bytes(_MINIMAL_PDF)

    file_manual_drop(fulltext_dir, _RECORD_A, source)

    asset = ManualDropResolver(fulltext_dir=fulltext_dir).resolve(record_id=_RECORD_A, doi=None)

    assert asset is not None
    assert asset.content == _MINIMAL_PDF
    assert asset.record_id == _RECORD_A


@pytest.mark.unit
def test_identify_pdf__one_candidate_left__is_never_confident_by_title_alone() -> None:
    """A lone candidate has no rival, so it has no margin, so it must ask.

    This was the blocking defect. With one candidate the runner-up score was
    hard-set to ``0.0`` and the margin became the score itself, so the margin
    test collapsed entirely into the containment test. A *different* paper's
    page one, and a page merely **citing** the title, both filed confidently.

    It is not an edge case: `run()` drops each filed record from the candidate
    set, so the **last** paper of every session is a batch of one.

    Title containment measures "are these words on this page", not "is this
    that paper" -- a references section carries whole titles verbatim. The
    margin over a rival is the only thing separating those two readings, so
    with no rival there is nothing to lean on.
    """
    candidates = [
        Candidate(record_id=_RECORD_A, title="Fine-grained activity recognition", doi=None)
    ]

    result = identify_pdf("Fine-grained activity recognition in baseball videos", candidates)

    # Perfect containment -- and still not confident, because nothing rivals it.
    assert result.score == 1.0
    assert result.runner_up_record_id is None
    assert result.margin == 0.0
    assert result.confident is False


@pytest.mark.unit
def test_identify_pdf__one_candidate_matched_by_doi__is_still_confident() -> None:
    """A DOI is an exact identifier, not a similarity score, so it needs no rival.

    The lone-candidate rule above must not disable DOI matching: refusing
    there would prompt on every single-record batch for no gain in safety.
    """
    doi = "10.1109/tvcg.2017.2745181"
    candidates = [Candidate(record_id=_RECORD_A, title="Something Else Entirely", doi=doi)]

    result = identify_pdf(f"Some heading\nhttps://doi.org/{doi}\nAbstract...", candidates)

    assert result.method == "doi"
    assert result.record_id == _RECORD_A
    assert result.confident is True


@pytest.mark.unit
def test_identify_pdf__containment_just_below_the_threshold__is_not_confident() -> None:
    """Pins `_CONTAINMENT_THRESHOLD` itself, not merely a value comfortably past it.

    Lowering the constant from 0.80 to 0.70 left the whole suite green: the
    fixtures bracketed containment only to `(0.6, 1.0]`, so the number ADR 0020
    calls the defence was free to drift downward -- the harmful direction.

    Four title tokens, three present: 0.75, which must fail an 0.80 bar.
    """
    candidates = [
        Candidate(record_id=_RECORD_A, title="Alpha Beta Gamma Delta", doi=None),
        Candidate(record_id=_RECORD_B, title="Zeta Eta Theta Iota", doi=None),
    ]

    result = identify_pdf("Alpha Beta Gamma unrelated words here", candidates)

    assert result.score == 0.75
    assert result.confident is False


@pytest.mark.unit
def test_identify_pdf__margin_just_below_the_threshold__is_not_confident() -> None:
    """Pins `_MARGIN_THRESHOLD` itself.

    Lowering it from 0.25 to 0.20 also left the suite green, and the obvious
    fixture cannot catch that: a 1.00/0.80 pair yields 0.19999... in binary
    floating point, so it fails a 0.20 bar too and discriminates nothing.

    This lands the margin **strictly between** the two: best 1.00 (5 of 5),
    runner-up 7 of 9 = 0.777..., margin 0.222... -- above 0.20, below 0.25.
    """
    candidates = [
        Candidate(record_id=_RECORD_A, title="Alpha Beta Gamma Delta Epsilon", doi=None),
        Candidate(
            record_id=_RECORD_B,
            title="Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota",
            doi=None,
        ),
    ]

    result = identify_pdf("Alpha Beta Gamma Delta Epsilon Zeta Eta", candidates)

    assert result.score == 1.0
    assert result.runner_up_score == pytest.approx(7 / 9)
    assert result.margin == pytest.approx(2 / 9)
    assert 0.20 < result.margin < 0.25, "fixture must discriminate the two thresholds"
    assert result.confident is False
