"""The screening view against a real store, a real log, and a real Panel object.

What the unit suite cannot reach: the key map's *dispatch* (which needs a
screener, which needs a queue, which needs a store), the keyboard path from a
keystroke to a line in ``decisions.jsonl``, and the claim that the whole thing
constructs headlessly.

BUILD_PLAN's table calls ``test_keymap__every_binding__maps_to_a_handler`` a
unit test. Written as one it can only compare the map's action names against a
list of names written in the test -- which passes just as happily when no
handler exists, and is the exact shape of vacuous test §3.7.6 warns this
module's 60% gate exists to make unnecessary. Asserted here instead against the
handler table a *real* screener exposes, where a missing handler cannot be
spelled around.

Browser interaction, keyboard event *delivery* and visual layout stay untested
(BUILD_PLAN's "Deliberately not tested here"). Everything downstream of the
browser event is ordinary Python, and that is what these tests drive.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import panel as pn
import pytest
from bokeh.model import Model

from prismabib.errors import LogError, ValidationError
from prismabib.screening import ui
from prismabib.screening.queue import screening_queue
from prismabib.stage import PrismaStage
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project, sidecar_path

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

#: The project slug this module screens under -- also the queue's ordering
#: seed, so "the current record" is whatever the seeded order puts first, not
#: record 1. Every assertion below reads it from the queue rather than
#: assuming it.
SLUG = "screening-ui"

REVIEWER = "kp"

#: The two exclusion codes ``criteria.yaml`` declares here, in order: digit 1
#: and digit 2 of the palette.
REASON_CODES = ("OFF_TOPIC", "REVIEW_OR_SURVEY")

#: Enough records for the 20-decision latency run, with room to spare.
RECORD_COUNT = 24

#: Every record carries the same author and citation count, so the blinding
#: assertions hold whichever record the seeded order presents first -- and so
#: they are asserting about blinding rather than about an empty column.
AUTHOR_SURNAME = "Alvarez"
CITED_BY = 417

#: A second author with no given name, which Scopus omits often enough that
#: the display rule for it is worth pinning: "Surname, Given" when both are
#: known, the surname alone when it is not -- never a dangling comma.
SURNAME_ONLY = "Solo"

CORPUS = CorpusSpec(
    records=[
        RecordSpec(
            number=number,
            authors=((AUTHOR_SURNAME, "Rosa"), (SURNAME_ONLY, "")),
            cited_by_count=CITED_BY,
        )
        for number in range(1, RECORD_COUNT + 1)
    ],
    criteria=CriteriaSpec(abstract_reason_codes=REASON_CODES),
)


@dataclass
class FakeClock:
    """A settable monotonic clock (§3.7.3 rule 1 permits doubling the clock).

    Settable rather than a scripted sequence: the view reads the clock however
    many times a repaint happens to need, and a test that had to know that
    number would be pinned to the rendering code it is not about.
    """

    now: float = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """One Layer 1 store for the module; the log is what each test needs fresh."""
    root = tmp_path_factory.mktemp("screening-ui")
    return build_project(root, CORPUS, slug=SLUG)


@pytest.fixture(autouse=True)
def _reset_log(project: Project) -> None:
    """Return the decision log to empty before every test."""
    project.decisions_path.write_bytes(b"")
    sidecar_path(project).unlink(missing_ok=True)


def build_screener(
    project: Project,
    *,
    reviewer: str = REVIEWER,
    blind: bool = True,
    clock: Any = time.monotonic,
) -> ui.Screener:
    """A screener over a freshly folded title/abstract queue -- what re-opening gives."""
    queue = screening_queue(project, PrismaStage.TITLE_ABSTRACT, reviewer)
    return ui.Screener(queue, ui.load_records(project, queue.pending), blind=blind, clock=clock)


def logged_events(project: Project) -> list[dict[str, Any]]:
    """Every event in ``decisions.jsonl``, read back off disk."""
    text = project.decisions_path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# Construction (S05-AC1)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC1")
def test_screener__construction__returns_a_viewable(project: Project) -> None:
    """The same object must render in a notebook cell and serve under ``panel serve``.

    Two render paths are driven, not just the constructor.
    ``_repr_mimebundle_`` is literally what a notebook cell calls to display
    the object, and ``get_root`` is where Panel turns the component tree into
    the Bokeh model that a notebook comm or a server session sends to the
    browser -- a widget given a parameter or a value Panel does not accept
    fails there rather than in front of the reviewer.

    The *serving* half of the criterion rests on ``screener`` calling
    ``.servable()`` on the object it returns, which only registers a root
    inside a live server session; a headless test cannot observe it without
    faking a Bokeh session context, and §8's deferred Playwright smoke test is
    where that belongs. What is asserted here is everything that can be
    asserted without a browser.
    """
    view = ui.screener(project, stage="title_abstract", reviewer=REVIEWER)

    bundle = view._repr_mimebundle_()
    rendered = bundle[0] if isinstance(bundle, tuple) else bundle

    assert isinstance(view, pn.viewable.Viewable)
    assert isinstance(view.get_root(), Model)
    assert "text/html" in rendered


@pytest.mark.integration
def test_screener__unknown_stage__raises_validation_error(project: Project) -> None:
    """``stage`` is a string in the frozen contract, so it is the argument most likely mistyped.

    ``PrismaStage("titel_abstract")`` raises ``ValueError``, which is outside
    §3.3's taxonomy and does not tell the reviewer what to type instead.
    """
    with pytest.raises(ValidationError, match="not a PRISMA stage"):
        ui.screener(project, stage="titel_abstract", reviewer=REVIEWER)


# ---------------------------------------------------------------------------
# Blinding, through the default path (BUILD_PLAN line 1074)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("secret", [AUTHOR_SURNAME, str(CITED_BY)])
def test_screener__default_construction__shows_no_author_or_citation(
    project: Project, secret: str
) -> None:
    """The bias control has to be the *default*, not an option a reviewer remembers.

    Driven through ``screener(...)`` with no ``blind`` argument and asserted on
    every piece of text the assembled view actually holds -- the one place the
    default, the view model and the renderer are all in play at once. The
    fixture's records really do carry this author and this citation count, so
    the assertion fails if any of the three stops doing its job.
    """
    view = ui.screener(project, stage="title_abstract", reviewer=REVIEWER)

    rendered = "\n".join(str(pane.object) for pane in view.select(pn.pane.Markdown))

    assert secret not in rendered


@pytest.mark.integration
def test_load_records__real_store__carries_keywords_authors_and_citations(
    project: Project,
) -> None:
    """The blinding tests presume these fields arrive; this is what proves they do.

    A loader that silently returned no authors would make every blinding
    assertion in this repository pass while asserting nothing -- the failure
    mode BUILD_PLAN's 60% gate exists to keep out of this module.
    """
    spec = CORPUS.records[0]

    records = ui.load_records(project, [spec.record_id])
    record = records[spec.record_id]

    assert record.authors == (f"{AUTHOR_SURNAME}, Rosa", SURNAME_ONLY)
    assert record.cited_by == CITED_BY
    assert record.keywords == (f"record {spec.number}", "synthetic")
    assert (record.title, record.year, record.venue) == (
        f"Synthetic Record {spec.number}",
        spec.year,
        spec.venue_name,
    )


# ---------------------------------------------------------------------------
# The key map (S05-AC4)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC4")
def test_keymap__every_binding__maps_to_a_handler(project: Project) -> None:
    """No dead keys, and no unreachable handlers.

    BUILD_PLAN asks for this specifically and gives the reason the map is a
    tuple of records rather than a chain of ``if`` statements: *"the map is
    data, so it is assertable"*. A binding whose action has no handler is a key
    that does nothing when pressed, with nothing saying why; a handler no
    binding names is behaviour the keyboard cannot reach. The equality is
    asserted in both directions because those are two different bugs.
    """
    screener = build_screener(project)

    assert {binding.action for binding in ui.KEY_MAP} == set(screener.handlers)
    assert all(callable(handler) for handler in screener.handlers.values())


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC4")
def test_keyboard__i__appends_an_include_for_the_current_record(project: Project) -> None:
    """One keystroke, one decision, on disk before the view moves on.

    This is the whole keyboard path minus the browser: the key, the map, the
    handler, the queue, the log. BUILD_PLAN puts event *delivery* out of scope
    here, and a Playwright test in §8; everything on this side of the wire is
    ordinary Python and is asserted as such.
    """
    screener = build_screener(project)
    current = screener.queue.current

    screener.handle_key("i")

    events = logged_events(project)
    assert [(event["decision"], event["record_id"]) for event in events] == [("include", current)]


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC4")
@pytest.mark.parametrize(("digit", "code"), [("1", REASON_CODES[0]), ("2", REASON_CODES[1])])
def test_keyboard__e_then_digit__appends_an_exclude_with_that_reason_code(
    project: Project, digit: str, code: str
) -> None:
    """``e`` then a digit files the exclusion under the digit's code, not the first one.

    Both digits are exercised because the classic defect here -- one closure
    capturing the loop variable -- gives every digit the *last* code and is
    invisible until the PRISMA exclusion breakdown is published under reasons
    nobody chose.
    """
    screener = build_screener(project)

    screener.handle_key("e")
    screener.handle_key(digit)

    events = logged_events(project)
    assert [(event["decision"], event["reason_code"]) for event in events] == [("exclude", code)]


@pytest.mark.integration
def test_keyboard__digit_without_e__appends_nothing(project: Project) -> None:
    """A stray digit must not exclude a record.

    ``e`` first is what makes an exclusion two deliberate keystrokes. Without
    this, a reviewer resting a hand on the number row would file exclusions
    they never made, under codes they never chose, and the queue would move on.
    """
    screener = build_screener(project)

    screener.handle_key("1")

    assert logged_events(project) == []
    assert "press e first" in screener.status


@pytest.mark.integration
def test_keyboard__z__appends_a_reversal_and_steps_back(project: Project) -> None:
    """Undo supersedes by appending; the log is append-only (BUILD_PLAN §2.2).

    Two events afterwards, not one edited: a replay must be able to show a
    reviewer who changed their mind, rather than a decision that was never
    made.
    """
    screener = build_screener(project)
    first = screener.queue.current

    screener.handle_key("i")
    screener.handle_key("z")

    events = logged_events(project)
    assert [event["decision"] for event in events] == ["include", "unsure"]
    assert screener.queue.current == first


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC4")
def test_keyboard__browser_payload__reaches_the_decision_log(project: Project) -> None:
    """The seam between the browser and the kernel, driven the way the browser drives it.

    ``handle_key`` proves the map dispatches; this proves the value the
    JavaScript listener writes actually arrives -- that the watcher is
    registered, on the parameter the script sets, and that the counter the
    script appends (so that two identical keys are two distinct values) is
    stripped rather than treated as part of the key. Event *delivery* inside
    the browser stays out of scope per BUILD_PLAN; everything after it is
    here.
    """
    screener = build_screener(project)
    current = screener.queue.current

    screener.bridge.keystroke = f"i{ui.KEYSTROKE_SEPARATOR}1"

    events = logged_events(project)
    assert [(event["decision"], event["record_id"]) for event in events] == [("include", current)]


@pytest.mark.integration
def test_keyboard__u__logs_unsure_and_leaves_the_record_unresolved(project: Project) -> None:
    """``unsure`` is a real, logged decision that does not resolve the record.

    BUILD_PLAN keeps it out of ``include``/``exclude`` deliberately: an unsure
    record comes back around instead of disappearing into a set nobody
    revisits. Counting it as progress would overstate how much of the review is
    finished -- and the pace and ETA are built on that count.
    """
    screener = build_screener(project)
    current = screener.queue.current

    screener.handle_key("u")

    events = logged_events(project)
    assert [event["decision"] for event in events] == ["unsure"]
    assert screener.queue.is_resolved(str(current)) is False
    assert screener.progress()["decided"] == 0
    assert screener.session_decisions == 0


@pytest.mark.integration
def test_keyboard__after_an_exclusion__the_next_bare_digit_does_nothing(project: Project) -> None:
    """The exclusion is disarmed once it is filed, not left armed for the next record.

    If arming survived the decision, every digit typed afterwards would
    exclude whatever record was on screen, under whichever code that digit
    names, with no ``e`` and no intent. Nothing surfaces it: the queue moves
    on, the log looks ordinary, and the error appears as a reason breakdown
    nobody can explain.
    """
    screener = build_screener(project)
    screener.handle_key("e")
    screener.handle_key("1")

    screener.handle_key("2")

    assert [event["decision"] for event in logged_events(project)] == ["exclude"]
    assert "press e first" in screener.status


@pytest.mark.integration
def test_keyboard__e_then_a_digit_the_palette_lacks__appends_nothing(project: Project) -> None:
    """A digit past the end of the palette must not fall through to a code.

    The obvious implementations -- index into the list, or wrap -- both file
    the exclusion under a reason the reviewer did not choose, and the mistake
    is invisible until the PRISMA breakdown is published.
    """
    screener = build_screener(project)

    screener.handle_key("e")
    screener.handle_key("9")

    assert logged_events(project) == []
    assert "no reason code 9" in screener.status


@pytest.mark.integration
def test_screener__deciding_past_the_end__warns_instead_of_raising(project: Project) -> None:
    """The last keystroke of a review must not end it with a traceback.

    A reviewer who presses ``i`` once more than there are records has done
    nothing wrong, and Panel would swallow the exception into a kernel log
    they will never open -- leaving a view that had simply stopped responding.
    """
    screener = build_screener(project)
    for _ in range(RECORD_COUNT):
        screener.handle_key("i")

    screener.handle_key("i")

    assert len(logged_events(project)) == RECORD_COUNT
    assert screener.status.startswith("⚠")


@pytest.mark.integration
def test_dispatch__an_action_no_handler_implements__raises(project: Project) -> None:
    """A key map that has outrun its handlers is a bug, and must be loud.

    Not a reviewer error and not a status line: silently ignoring it is how a
    key comes to do nothing while every test still passes.
    """
    screener = build_screener(project)

    with pytest.raises(ValidationError, match="no handler named"):
        screener.dispatch("not_an_action")


@pytest.mark.integration
def test_keyboard__n_and_p__move_the_cursor_without_deciding(project: Project) -> None:
    """Navigation is not a decision, and must never become one.

    A reviewer who wants to look ahead -- or back at the record they just
    passed -- has to be able to, without that reading being logged as a
    judgement. ``n`` on an undecided record is exactly the "I'll come back to
    this" gesture, and it is only safe while it writes nothing.
    """
    screener = build_screener(project)
    first = screener.queue.current

    screener.handle_key("n")
    second = screener.queue.current
    screener.handle_key("p")

    assert second != first
    assert screener.queue.current == first
    assert logged_events(project) == []


@pytest.mark.integration
def test_screener__every_record_decided__says_screening_is_complete(project: Project) -> None:
    """The end of the queue has to say so, not show the last record again.

    An exhausted queue has no current record, and a view that fell back to the
    previous one would invite a reviewer to decide it twice -- appending a
    superseding event over a decision they had already made and meant.
    """
    screener = build_screener(project)

    for _ in range(RECORD_COUNT):
        screener.handle_key("i")

    assert screener.queue.current is None
    assert "Screening complete" in str(screener.record_pane.object)
    assert screener.progress()["remaining"] == 0


@pytest.mark.integration
def test_keyboard__unbound_key__is_ignored(project: Project) -> None:
    """A reviewer who presses ``q`` should not be told off, or crash the kernel.

    The browser listener filters to the map's keys, but the kernel side must
    not depend on that: a stale listener from a re-executed cell is exactly the
    thing that delivers a key the current map does not know.
    """
    screener = build_screener(project)
    before = screener.status

    screener.handle_key("q")

    assert logged_events(project) == []
    assert screener.status == before


# ---------------------------------------------------------------------------
# The reason palette (BUILD_PLAN line 1077)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_screener__reason_palette__is_built_from_criteria_yaml(tmp_path: Path) -> None:
    """A code added to the protocol file must surface with no code change.

    The palette is the reviewer's exclusion vocabulary, and the log refuses an
    exclusion whose code ``criteria.yaml`` does not declare. A hard-coded
    palette would eventually offer a button that cannot be used and omit the
    code that must be -- and a protocol amendment would silently disagree with
    the UI enforcing it.
    """
    added = "NOT_PEER_REVIEWED"
    amended = build_project(
        tmp_path,
        CorpusSpec(
            records=[RecordSpec(number=1)],
            criteria=CriteriaSpec(abstract_reason_codes=(*REASON_CODES, added)),
        ),
        slug="palette",
    )

    screener = build_screener(amended)

    assert screener.palette == {"1": REASON_CODES[0], "2": REASON_CODES[1], "3": added}


# ---------------------------------------------------------------------------
# Progress and pace (BUILD_PLAN line 1073)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_screener__pace__counts_this_session_not_the_whole_log(project: Project) -> None:
    """Pace is decisions per minute *of this session*, over an injected clock.

    The defect this pins is the natural implementation: dividing the queue's
    total ``decided`` -- which folds every decision the reviewer has ever made
    at this stage -- by the time the notebook has been open. Resuming at record
    900 of 1110 would then claim hundreds of decisions a minute and an ETA of
    seconds. BUILD_PLAN calls this display "what sustains a multi-hour task";
    a number a reviewer catches out once is a number they stop reading.

    Three decisions are pre-logged as a previous session precisely so the two
    readings differ: 5 decided in total, 2 of them here, one minute elapsed.
    """
    previous = screening_queue(project, PrismaStage.TITLE_ABSTRACT, REVIEWER)
    previous.decide("include")
    previous.decide("include")
    previous.decide("include")

    clock = FakeClock()
    screener = build_screener(project, clock=clock)
    screener.handle_key("i")
    clock.now = 60.0
    screener.handle_key("i")

    progress = screener.progress()
    assert (progress["decided"], progress["remaining"]) == (5, RECORD_COUNT - 5)
    assert progress["per_minute"] == pytest.approx(2.0)
    assert progress["eta_minutes"] == pytest.approx((RECORD_COUNT - 5) / 2.0)


@pytest.mark.integration
def test_screener__pace__is_not_reported_before_it_can_be_measured(project: Project) -> None:
    """One decision a fraction of a second in is not a rate of thousands per minute.

    The defect this pins divided the session's decisions by the time since the
    *first* of them, which after exactly one decision is microseconds: a
    reviewer opening a 1,110-record session read "304051.5/min · ~0 min left"
    immediately after their very first keystroke -- the moment they have least
    evidence of their own to contradict it. Measuring from the session's start
    removes the microseconds but not the problem, because a fraction of a
    second is still a denominator a single fast keystroke dominates. Nothing
    is reported until :data:`~prismabib.screening.ui.MIN_PACE_SECONDS`.

    The clock is advanced *after* the decision as well as before it, and that
    is the whole test rather than a detail: read at the same instant the mark
    was taken, the defect divides by exactly zero and declines to report a
    pace, so a frozen clock would let it pass. Real time moves between the
    keystroke and the repaint. Here twelve seconds of reading, then a
    twentieth of a second, which the defect reports as 1,200 a minute.
    """
    clock = FakeClock()
    screener = build_screener(project, clock=clock)

    clock.now = 12.0
    screener.handle_key("i")
    clock.now = 12.05

    progress = screener.progress()
    assert progress["decided"] == 1
    assert (progress["per_minute"], progress["eta_minutes"]) == (None, None)
    assert "pace —" in ui.progress_markdown(progress)


@pytest.mark.integration
def test_screener__pace__is_measured_from_the_session_start(project: Project) -> None:
    """The first record's reading time is screening work and counts against the rate.

    Measured from the first *decision*, N decisions span only N-1 gaps, so the
    rate is overstated by N/(N-1) forever -- 2x at two decisions. Here two
    decisions one minute apart, in a session two minutes old, are 1.0/min and
    not 2.0/min; the reviewer spent the first minute reading.
    """
    clock = FakeClock()
    screener = build_screener(project, clock=clock)

    clock.now = 60.0
    screener.handle_key("i")
    clock.now = 120.0
    screener.handle_key("i")

    assert screener.progress()["per_minute"] == pytest.approx(1.0)


@pytest.mark.integration
def test_screener__changing_a_decision__does_not_count_as_session_progress(
    project: Project,
) -> None:
    """Revisiting a record is one record's progress, however many times you decide it.

    Step back onto something already included and exclude it instead: the log
    correctly holds two events and the queue still counts one record decided,
    but the session's pace counted the correction as a second decision. That
    inflates the rate with exactly the work spent *not* getting through the
    queue -- and it is the same principle ``z`` already honours by dropping
    its mark.
    """
    clock = FakeClock()
    screener = build_screener(project, clock=clock)
    screener.handle_key("i")
    screener.handle_key("i")
    screener.handle_key("p")

    screener.handle_key("e")
    screener.handle_key("1")

    assert len(logged_events(project)) == 3
    assert screener.queue.decided == 2
    assert screener.session_decisions == 2


@pytest.mark.integration
def test_screener__undo__does_not_count_as_session_progress(project: Project) -> None:
    """A correction is not a decision; counting it would inflate the pace it corrects."""
    clock = FakeClock()
    screener = build_screener(project, clock=clock)

    screener.handle_key("i")
    clock.now = 30.0
    screener.handle_key("z")

    assert screener.session_decisions == 0
    assert screener.progress()["per_minute"] is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "navigation_key", [pytest.param("n", id="next"), pytest.param("p", id="previous")]
)
def test_screener__exclusion_armed_then_navigated__does_not_file_against_the_new_record(
    project: Project, navigation_key: str
) -> None:
    """Arming is a statement about *this* record; it must not outlive it.

    ``e`` then ``n`` then a digit used to file the exclusion against the record
    the reviewer navigated *to*, under a reason code they chose while reading a
    different abstract. Nothing surfaces that: the log is well-formed, the
    counts add up, and the mistake is invisible until the PRISMA breakdown is
    published with an exclusion attributed to the wrong paper.

    ``begin_exclude``'s docstring promises that "``e`` pressed by accident
    costs nothing", which was false the moment the reviewer moved.
    """
    queue = screening_queue(project, PrismaStage.TITLE_ABSTRACT, "kp")
    screener_session = ui.Screener(queue, ui.load_records(project, queue.order))

    screener_session.handle_key("e")
    screener_session.handle_key(navigation_key)
    screener_session.handle_key("1")

    assert queue.log.load() == []


@pytest.mark.integration
def test_screener__exclusion_armed_then_digit__files_against_the_armed_record(
    project: Project,
) -> None:
    """The ordinary path must still work -- a guard that blocks it is worse.

    Asserted alongside the disarming test so neither can be satisfied by a
    change that simply stops filing exclusions.
    """
    queue = screening_queue(project, PrismaStage.TITLE_ABSTRACT, "kp")
    screener_session = ui.Screener(queue, ui.load_records(project, queue.order))
    armed_on = queue.current

    screener_session.handle_key("e")
    screener_session.handle_key("1")

    events = queue.log.load()
    assert [(e.record_id, e.decision, e.reason_code) for e in events] == [
        (armed_on, "exclude", "OFF_TOPIC")
    ]


@pytest.mark.integration
def test_screener__z_after_p__reverses_the_record_on_screen(project: Project) -> None:
    """End to end at the keyboard: ``p`` then ``z`` must not misfile the reversal.

    The queue-level test pins the rule; this pins the keystrokes a reviewer
    actually presses, and that the status line does not claim otherwise. Under
    the defect this read "undone" while the record on screen kept its decision
    and a record two places back silently lost one.
    """
    screener = build_screener(project)
    for _ in range(3):
        screener.handle_key("i")
    screener.handle_key("p")
    on_screen = screener.queue.current

    screener.handle_key("z")

    reversals = [event for event in logged_events(project) if event["decision"] == "unsure"]
    assert [event["record_id"] for event in reversals] == [on_screen]
    assert screener.queue.decision_for(on_screen) == "unsure"
    assert screener.status == "undone"


@pytest.mark.integration
def test_screener__stepping_back_onto_a_decided_record__shows_its_decision(
    project: Project,
) -> None:
    """Navigating backwards must not be blind.

    A reviewer who steps back to re-read a paper has to be able to see what
    they already decided about it -- otherwise ``z`` is a keystroke whose
    effect they cannot check, which is the condition the misfiling defect
    hid behind. The mark is asserted through the rendered status pane rather
    than the ``status`` string, because it is the pane the reviewer reads.
    """
    screener = build_screener(project)
    screener.handle_key("i")
    screener.handle_key("i")
    screener.handle_key("p")
    on_screen = screener.queue.current

    pane_text = str(screener.status_pane_text)

    assert on_screen is not None
    assert on_screen in pane_text
    assert "include" in pane_text

    screener.handle_key("z")

    assert "unsure" in str(screener.status_pane_text)


@pytest.mark.integration
def test_screener__a_refused_exclusion__disarms_instead_of_staying_armed(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refusal must not leave a loaded exclusion waiting for the next digit.

    ``select_reason`` used to clear the arm only *after* the append returned,
    so a transient refusal -- a lock the log could not take, a stale checksum
    sidecar -- left the screener armed with nothing on screen to say so. The
    reviewer's next digit, pressed to retry or by reflex, was then a live
    exclusion they had not re-armed. Re-arming has to be deliberate.

    The refusal is injected at the log's own boundary rather than by
    corrupting a file, so the test is about what happens *after* a refusal
    rather than about which refusals exist.
    """
    screener = build_screener(project)

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise LogError("injected refusal")

    monkeypatch.setattr(screener.queue.log, "append", refuse)
    screener.handle_key("e")
    assert screener.awaiting_reason

    screener.handle_key("1")

    assert screener.status.startswith("⚠")
    assert not screener.awaiting_reason, "a refused exclusion must not stay armed"
    assert logged_events(project) == []


@pytest.mark.integration
def test_reason_button__a_refused_exclusion__reports_it_instead_of_raising(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mouse path must surface a refusal the same way the keyboard does.

    The reason buttons called ``begin_exclude`` and ``select_reason`` directly
    rather than through :meth:`~prismabib.screening.ui.Screener.dispatch`, so
    a ``LogError`` raised straight out of the Panel click callback: under
    ``panel serve`` the traceback goes to a server log, and the reviewer sees
    an unchanged record and no message at all -- a decision they believe they
    made and did not.

    Driven through the real button the view builds, because the defect was
    precisely that the button's wiring differed from the handler table, and a
    test that called the handler directly would have passed throughout.
    """
    screener = build_screener(project)
    screener.view()
    button = next(
        widget
        for widget in screener.view().select(pn.widgets.Button)
        if widget.name.startswith("1: ")
    )

    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise LogError("injected refusal")

    monkeypatch.setattr(screener.queue.log, "append", refuse)

    button.clicks += 1

    assert screener.status.startswith("⚠")
    assert not screener.awaiting_reason
    assert logged_events(project) == []


@pytest.mark.integration
def test_reason_button__an_accepted_exclusion__files_it_under_that_code(
    project: Project,
) -> None:
    """The positive control for the button path: one click is the whole gesture.

    Without this, the test above is satisfied by a button that does nothing at
    all -- and a mouse-only reviewer would record no decisions while every
    other test stayed green.
    """
    screener = build_screener(project)
    on_screen = screener.queue.current
    button = next(
        widget
        for widget in screener.view().select(pn.widgets.Button)
        if widget.name.startswith("2: ")
    )

    button.clicks += 1

    events = logged_events(project)
    assert [(event["record_id"], event["decision"], event["reason_code"]) for event in events] == [
        (on_screen, "exclude", REASON_CODES[1])
    ]
    assert not screener.awaiting_reason
