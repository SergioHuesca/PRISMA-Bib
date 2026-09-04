"""Assisted manual fetch: identification and filing (ADR 0020 Decision 4).

BUILD_PLAN's resolver chain (:mod:`prismabib.fulltext.resolve`) gets what is
legally and technically reachable without credentials. What remains --
paywalled content, and hosts that refuse automated clients outright -- has no
machine-readable route at all: a human must open the paper and download it.
ADR 0020 accepts that "human only at screening" cannot be achieved for that
residue, and removes what *can* be removed instead: the bookkeeping.
``manual_drop_path``'s sanitisation is lossy and non-reversible
(``scopus:2-s2.0-...`` becomes ``scopus_2-s2.0-....pdf``), so a reviewer who
saves a download under the record id verbatim produces a file the resolver
chain never looks for -- and nothing reports the mistake; the record simply
stays unresolved.

This module is the *testable* half of that removal -- identification and
filing, both pure enough to run under ``mypy --strict`` and the ``fulltext``
coverage gate. The interactive half (opening a browser tab per DOI, watching
a download directory, prompting when identification is unsure) is
``scripts/fetch_assist.py``, deliberately outside this package: ADR 0020
Decision 4 explains why the split falls exactly here, and it is not
incidental -- a defect in filing attaches one paper's full text to another
record, which is why *this* half is held to full test discipline while the
driver's browser-launching and filesystem-polling are not.

**Identification, in ADR 0020's own measured order.** :func:`identify_pdf`
tries the downloaded PDF's own DOI (printed on page 1 of nearly every
publisher's PDF) before ever looking at the title: a DOI match is
unambiguous by construction, and reusing
:func:`prismabib.models.normalise_doi` means the same casing/URL-form
tolerance the rest of this codebase already gives a DOI is given here too.
Only when no single candidate's DOI is found on the page does it fall back
to title token containment -- and even then, it never returns a *confident*
match without a **margin** over the runner-up: two near-identical titles (the
real case ADR 0020 measured -- two baseball-video papers, runner-up
containment 0.92) must not resolve as a coin flip. A wrong match here is
silent, durable, and produces a review whose full-text assessment was
performed on the wrong paper -- worse than asking, which is why
:func:`identify_pdf` never lowers its own bar to avoid a prompt.

**Filing.** :func:`file_manual_drop` is the write side: it never moves the
operator's downloaded file (a browser download the reviewer may still want
in their own Downloads folder), never overwrites an existing drop (silently
replacing a file a previous run already placed there is exactly the kind of
mistake this module exists to prevent, not commit itself), and its
destination path is always computed by
:func:`prismabib.fulltext.resolve.manual_drop_path` -- never reconstructed by
hand, since that path's sanitisation is exactly the lossy, non-reversible
step this module exists to stop a human from getting wrong.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from prismabib.errors import PrismabibError
from prismabib.fulltext.resolve import manual_drop_path
from prismabib.models import normalise_doi
from prismabib.sources.unpaywall import looks_like_pdf

#: A DOI as it typically appears in a PDF's own running text or header --
#: `10.NNNN(N...)/<suffix>`, per the DOI Handbook's registrant-prefix shape
#: (`10.` followed by 4-9 digits). The suffix character class allows the
#: punctuation DOI suffixes commonly contain (`.`, `-`, `_`, `;`, `(`, `)`,
#: `/`, `:`) without also swallowing trailing sentence punctuation a PDF's
#: prose puts immediately after one -- see `_strip_trailing_punctuation`.
_DOI_IN_TEXT_RE: Final[re.Pattern[str]] = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

#: Trailing characters a DOI never legitimately ends with but sentence prose
#: routinely puts immediately after one -- a full stop closing a sentence, a
#: comma before "et al.", a closing bracket around a citation. Stripped from
#: the *end* of a raw regex match only, never from the middle.
_TRAILING_PUNCTUATION: Final = ".,;:]}"

#: Common English words a title-containment comparison should ignore, so
#: "Detection of X" and "X Detection" score on their substance rather than on
#: sharing three prepositions. Deliberately small and hand-picked rather than
#: a general-purpose stopword list -- see the module docstring: this is a
#: measured threshold tuned against a real corpus, not a general NLP tool.
_STOPWORDS: Final[frozenset[str]] = frozenset(
    ["the", "a", "an", "of", "for", "and", "in", "on", "to", "with", "using", "from", "by", "via"]
)

#: A word, for tokenisation purposes: a maximal run of letters/digits.
#: Deliberately ASCII-narrow (`[a-z0-9]+` against a case-folded string, not a
#: Unicode-aware `\w+`) -- titles compared here are already the record's own
#: English-language `title` field and a PDF's extracted running text, and a
#: narrower class is easier to reason about than a Unicode class that would
#: also swallow combining marks and script-specific punctuation this module
#: has never been measured against.
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")

#: Measured against the six PDFs already fetched for `Baseball-CVPR`
#: (ADR 0020 Decision 5): title containment matched 5 of 6 confidently at
#: these thresholds, and the sixth -- two near-identical titles, runner-up at
#: 0.92 -- was correctly refused. Both are load-bearing: lowering either one
#: is precisely what would have let that sixth PDF file silently under the
#: wrong record.
_CONTAINMENT_THRESHOLD: Final = 0.80
_MARGIN_THRESHOLD: Final = 0.25


@dataclass(frozen=True)
class Candidate:
    """One unresolved record :func:`identify_pdf` may match a downloaded PDF against.

    Attributes:
        record_id: The record this candidate stands for.
        title: The record's title, as stored in Layer 1.
        doi: The record's DOI (any form :func:`~prismabib.models.normalise_doi`
            accepts), or ``None`` if the record has none.
    """

    record_id: str
    title: str
    doi: str | None = None


@dataclass(frozen=True)
class Identification:
    """The result of matching one downloaded PDF against a batch of candidates.

    Attributes:
        record_id: The best-matching candidate's record id, or ``None`` when
            ``candidates`` was empty. **Not, by itself, a licence to file:**
            check :attr:`confident` first -- a low-margin best guess is
            reported here for a human to see, never for a caller to act on
            unattended (ADR 0020 Decision 5, Constraints).
        method: ``"doi"`` when a single candidate's DOI was found verbatim on
            the page; ``"title"`` when the match came from token containment
            instead; ``"none"`` when there were no candidates to match
            against at all.
        score: The winning candidate's score -- ``1.0`` for a DOI match,
            otherwise the title containment fraction in ``[0.0, 1.0]``.
        runner_up_record_id: The second-best candidate's record id, or
            ``None`` when there was no second candidate (a DOI match, or a
            title comparison against a single-candidate batch).
        runner_up_score: The runner-up's score, ``0.0`` when there is none.
        margin: ``score - runner_up_score``. The quantity ADR 0020 requires a
            threshold on, not ``score`` alone -- a title that merely clears
            the containment bar while a near-identical rival clears it
            almost as well is exactly the ambiguity a bare containment check
            would miss.
        confident: Whether this match may be filed without asking a human.
            ``True`` only for a DOI match, or a title match whose ``score``
            and ``margin`` both clear their thresholds. **Never** set by
            lowering either threshold to avoid a prompt -- see the module
            docstring.
    """

    record_id: str | None
    method: Literal["doi", "title", "none"]
    score: float
    runner_up_record_id: str | None
    runner_up_score: float
    margin: float
    confident: bool


def _strip_trailing_punctuation(candidate: str) -> str:
    """Trim sentence punctuation a regex match on free text can pick up after a DOI.

    Args:
        candidate: A raw :data:`_DOI_IN_TEXT_RE` match.

    Returns:
        ``candidate`` with any characters in :data:`_TRAILING_PUNCTUATION`
        stripped from the end, and -- since DOI suffixes legitimately contain
        parentheses (``10.1000/xyz123(4)``) -- with a trailing ``)`` stripped
        only when it is unbalanced by an opening ``(`` earlier in the match,
        so a citation's closing bracket is removed without also removing a
        DOI's own balanced one.
    """
    stripped = candidate.rstrip(_TRAILING_PUNCTUATION)
    if stripped.count("(") < stripped.count(")"):
        last_open = stripped.rfind(")")
        stripped = stripped[:last_open]
    return stripped


def _extract_page_dois(page_one_text: str) -> frozenset[str]:
    """Every DOI-shaped substring on a PDF's first page, normalised.

    Args:
        page_one_text: The extracted text of a PDF's first page.

    Returns:
        Every match of :data:`_DOI_IN_TEXT_RE`, trailing-punctuation-trimmed
        and passed through :func:`~prismabib.models.normalise_doi`, as a set
        -- duplicates (the DOI often appears twice: once in a running header,
        once in a footer) collapse to one entry.
    """
    found: set[str] = set()
    for match in _DOI_IN_TEXT_RE.finditer(page_one_text):
        trimmed = _strip_trailing_punctuation(match.group(0))
        if trimmed:
            found.add(normalise_doi(trimmed))
    return frozenset(found)


def _title_tokens(text: str) -> frozenset[str]:
    """Case-folded, stopword-filtered words in ``text``.

    Args:
        text: Free text -- a record title, or a PDF page's extracted text.

    Returns:
        The distinct words :data:`_WORD_RE` finds in ``text.casefold()``,
        with every entry in :data:`_STOPWORDS` removed. A ``frozenset``, not
        a list: containment scoring below counts each candidate title word
        once regardless of how many times it repeats on the page, so a word
        printed twice on page 1 cannot inflate a score past what the title
        actually shares with the page.
    """
    return frozenset(word for word in _WORD_RE.findall(text.casefold()) if word not in _STOPWORDS)


def _containment_score(candidate_tokens: frozenset[str], page_tokens: frozenset[str]) -> float:
    """The fraction of a candidate title's (non-stopword) words found on the page.

    Args:
        candidate_tokens: :func:`_title_tokens` of one candidate's title.
        page_tokens: :func:`_title_tokens` of the PDF page's extracted text.

    Returns:
        ``len(candidate_tokens & page_tokens) / len(candidate_tokens)``, or
        ``0.0`` when ``candidate_tokens`` is empty (a title that is entirely
        stopwords, or blank) -- never a ``ZeroDivisionError``, and never a
        score that credits an empty title with matching everything.
    """
    if not candidate_tokens:
        return 0.0
    return len(candidate_tokens & page_tokens) / len(candidate_tokens)


def identify_pdf(page_one_text: str, candidates: Sequence[Candidate]) -> Identification:
    """Match a downloaded PDF's first page against a batch of unresolved records.

    Args:
        page_one_text: The extracted text of the PDF's first page only --
            deliberately not the whole document (see
            :mod:`prismabib.fulltext.extract`'s ``extract_pdf``, which walks
            every page and is the wrong tool for this: identification needs
            only the DOI/title a paper's own first page already carries).
        candidates: The batch this download might belong to -- ordinarily
            every record a caller is currently trying to fetch by hand, not
            the whole corpus.

    Returns:
        An :class:`Identification`. DOI matching is tried first
        (:func:`_extract_page_dois` against each candidate's own DOI,
        normalised): when the page names the DOI of **exactly one**
        candidate, that match is returned immediately with
        ``method="doi"``, ``score=1.0``, ``confident=True``, and no runner-up
        (a literal DOI match is unambiguous by construction, so a margin
        over a second candidate is not a meaningful question to ask of it).
        A page whose DOI matches zero or more-than-one candidate falls
        through to title token containment, scored per
        :func:`_containment_score`, ranked best-first with ties broken by
        input order (stable sort): the top scorer is ``record_id``, the
        second is the runner-up, and ``confident`` requires **both**
        ``score >= 0.80`` and ``margin >= 0.25`` (ADR 0020's measured
        thresholds). With zero candidates, returns ``method="none"``,
        ``record_id=None``, ``confident=False``.
    """
    if not candidates:
        return Identification(
            record_id=None,
            method="none",
            score=0.0,
            runner_up_record_id=None,
            runner_up_score=0.0,
            margin=0.0,
            confident=False,
        )

    page_dois = _extract_page_dois(page_one_text)
    if page_dois:
        doi_matches = [
            candidate
            for candidate in candidates
            if candidate.doi and normalise_doi(candidate.doi) in page_dois
        ]
        if len(doi_matches) == 1:
            return Identification(
                record_id=doi_matches[0].record_id,
                method="doi",
                score=1.0,
                runner_up_record_id=None,
                runner_up_score=0.0,
                margin=1.0,
                confident=True,
            )

    page_tokens = _title_tokens(page_one_text)
    scored = sorted(
        (
            (candidate, _containment_score(_title_tokens(candidate.title), page_tokens))
            for candidate in candidates
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )
    best_candidate, best_score = scored[0]
    if len(scored) > 1:
        runner_up_candidate, runner_up_score = scored[1]
        runner_up_record_id: str | None = runner_up_candidate.record_id
    else:
        runner_up_record_id = None
        runner_up_score = 0.0

    margin = best_score - runner_up_score
    confident = best_score >= _CONTAINMENT_THRESHOLD and margin >= _MARGIN_THRESHOLD
    return Identification(
        record_id=best_candidate.record_id,
        method="title",
        score=best_score,
        runner_up_record_id=runner_up_record_id,
        runner_up_score=runner_up_score,
        margin=margin,
        confident=confident,
    )


class ManualDropFilingError(PrismabibError):
    """A manual drop was refused: an existing drop, or a source that is not a PDF.

    Not one of the named leaves in BUILD_PLAN §3.3's error tree (that tree
    predates this module, the same way
    :class:`~prismabib.capture.layout.SealedRunError` and
    :class:`~prismabib.fulltext.resolve.FullTextResolutionError` are direct
    :class:`~prismabib.errors.PrismabibError` subclasses outside it): filing
    is a local filesystem operation with no upstream source, key, or
    entitlement involved, so nothing in the §3.3 taxonomy fits it.
    """


def file_manual_drop(fulltext_dir: Path, record_id: str, source: Path) -> Path:
    """Copy a downloaded PDF into ``record_id``'s manual drop-box slot.

    Args:
        fulltext_dir: ``project.fulltext_dir``.
        record_id: The record this download has been identified as.
        source: The downloaded file (e.g. from the operator's own downloads
            directory) to file. Left untouched -- see below.

    Returns:
        The path the file was copied to --
        :func:`~prismabib.fulltext.resolve.manual_drop_path`'s own return
        value for ``(fulltext_dir, record_id)``, always: this function never
        derives a destination filename any other way, because that path's
        sanitisation is lossy and non-reversible (that module's own
        docstring), and reconstructing it here would risk drifting from the
        one place :class:`~prismabib.fulltext.resolve.ManualDropResolver`
        actually looks.

    Raises:
        ManualDropFilingError: If the destination already exists (an earlier
            drop for this record -- refused, never silently overwritten), or
            if ``source``'s content does not sniff as a PDF
            (:func:`~prismabib.sources.unpaywall.looks_like_pdf`) -- the same
            check :class:`~prismabib.fulltext.resolve.ManualDropResolver`
            itself applies on read, applied here on write so a mistaken file
            is refused at filing time rather than silently accepted and only
            discovered as "zero sections extracted" much later.
        OSError: Propagates from the filesystem (e.g. ``source`` does not
            exist, or is not readable) -- an operator-visible failure with
            no domain-specific translation this module can usefully add.

    This copies ``source``'s bytes; it never moves or deletes it. The
    downloaded file may still be exactly where the operator's browser put
    it, in their own Downloads directory, and this function has no business
    deciding they are done with it.
    """
    destination = manual_drop_path(fulltext_dir, record_id)
    if destination.exists():
        raise ManualDropFilingError(
            f"a manual drop already exists at {destination} for {record_id!r}; "
            "refusing to overwrite it. Remove it yourself first if it should be replaced."
        )

    content = source.read_bytes()
    if not looks_like_pdf(content, None):
        raise ManualDropFilingError(
            f"{source} does not look like a PDF (no %PDF- magic bytes found); "
            f"refusing to file it as {destination.name}."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return destination


__all__ = [
    "Candidate",
    "Identification",
    "ManualDropFilingError",
    "file_manual_drop",
    "identify_pdf",
]
