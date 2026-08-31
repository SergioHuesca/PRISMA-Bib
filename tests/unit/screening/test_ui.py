"""The screening view's pure surface: view model, pace, palette, key map.

BUILD_PLAN sets ``screening/ui.py``'s line gate to 60% and says why in as many
words: *"Resist the temptation to write assertion-free render tests to lift the
coverage number."* Every test in this file therefore asserts on a plain
structure -- a dict, a string, a mapping -- returned by a pure function. None
of them constructs a widget, and none of them is here to move a percentage.

The parts that need a real queue (and therefore a real store and log) are in
``tests/integration/screening/test_ui.py``: the key map's dispatch, the
keyboard path end to end, and the latency criterion.
"""

from __future__ import annotations

import pytest

from prismabib.errors import ValidationError
from prismabib.project import (
    Criteria,
    DocTypeCriteria,
    ManualScreeningCriteria,
    TemporalCriteria,
)
from prismabib.screening import ui
from prismabib.stage import PrismaStage

#: A record that carries everything blinding is supposed to take away. The
#: blinding tests below would pass against a record with no authors and no
#: citation count, and would be asserting nothing at all.
RECORD = ui.ScreeningRecord(
    record_id="scopus:2-s2.0-900000000001",
    title="Pose Estimation for Baseball Pitching",
    abstract="A synthetic abstract.",
    venue="Journal of Synthetic Testing",
    year=2020,
    doc_type="Article",
    keywords=("pose estimation", "baseball"),
    authors=("Alvarez, Rosa", "Novak, Petr"),
    cited_by=417,
)


def criteria(*, abstract_codes: tuple[str, ...], fulltext_codes: tuple[str, ...] = ()) -> Criteria:
    """A minimal :class:`~prismabib.project.Criteria` with the given vocabularies.

    Args:
        abstract_codes: ``manual_abstract.exclude_reason_codes``.
        fulltext_codes: ``manual_fulltext.exclude_reason_codes``.

    Returns:
        The parsed criteria object, built in memory: the palette is a pure
        function of it, and reading a file to prove that would only make the
        test slower and less specific. That a code written into
        ``criteria.yaml`` reaches the palette is asserted separately, against
        a real file, in the integration suite.
    """
    return Criteria(
        version="1.0.0",
        temporal=TemporalCriteria(year_start=2000, year_end=2030),
        subject_areas=[],
        doc_types=DocTypeCriteria(include=[], conference_whitelist=[]),
        languages=[],
        manual_abstract=ManualScreeningCriteria(exclude_reason_codes=list(abstract_codes)),
        manual_fulltext=ManualScreeningCriteria(exclude_reason_codes=list(fulltext_codes)),
    )


# ---------------------------------------------------------------------------
# Blinding (BUILD_PLAN line 1074)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "expected"),
    [("authors", ["Alvarez, Rosa", "Novak, Petr"]), ("cited_by", 417)],
)
def test_screener__blind_mode__omits_authors_and_citations_from_view_model(
    field: str, expected: object
) -> None:
    """Blinding removes the field; it does not hide it.

    Author names carry institutional and seniority signal, citation counts
    carry the field's own prior about which papers matter. Neither is an
    eligibility criterion and both are known to move human judgement.

    Asserted on the **view model dict**, not on rendered HTML, exactly as
    BUILD_PLAN's test table requires: a field merely styled away is one
    inspect-element from being seen, and would pass a test that searched the
    visible text for it.
    """
    blind = ui.record_view_model(RECORD, blind=True)
    unblinded = ui.record_view_model(RECORD, blind=False)

    assert field not in blind
    assert unblinded[field] == expected


@pytest.mark.unit
def test_record_view_model__blind__carries_exactly_the_minimal_field_set() -> None:
    """ "Title, abstract, venue, year, doc type, author keywords. Nothing else."

    BUILD_PLAN line 1074 fixes the list, and the restriction is the feature:
    every extra field is another cue that is not an eligibility criterion. An
    equality on the key set is what makes adding one a failing test rather
    than a judgement call in review.
    """
    model = ui.record_view_model(RECORD, blind=True)

    assert set(model) == set(ui.VISIBLE_FIELDS)


@pytest.mark.unit
def test_record_markdown__blind_view_model__renders_every_visible_field() -> None:
    """A field kept out of the markdown is a field the reviewer screens without.

    The view model is the contract; this asserts the renderer honours all of
    it, since a value silently dropped here looks identical to a record that
    never had one.
    """
    markdown = ui.record_markdown(ui.record_view_model(RECORD, blind=True))

    assert all(
        str(value) in markdown
        for value in (RECORD.title, RECORD.abstract, RECORD.venue, RECORD.year, RECORD.doc_type)
    )
    assert all(term in markdown for term in RECORD.keywords)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["Alvarez", "417"])
def test_record_markdown__blind_view_model__renders_no_author_or_citation(name: str) -> None:
    """The rendered end of the blind path, not just its model.

    The model is where blinding is decided, but the renderer is what the
    reviewer reads; a renderer that reached past the model to the record --
    the natural refactor when someone wants a subtitle line -- would restore
    exactly the cue the default exists to remove.
    """
    markdown = ui.record_markdown(ui.record_view_model(RECORD, blind=True))

    assert name not in markdown


# ---------------------------------------------------------------------------
# Progress and pace (BUILD_PLAN line 1073)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("session_decisions", "session_seconds"),
    [(0, 120.0), (0, 0.0), (3, 0.0)],
)
def test_progress_view_model__nothing_to_measure__reports_no_pace(
    session_decisions: int, session_seconds: float
) -> None:
    """A rate over zero decisions is not a slow rate; it is no rate.

    ``0.0/min`` beside an ETA computed from it reads as a measurement, and
    would tell a reviewer opening a fresh session that they will never finish.
    Reporting nothing is honest; reporting zero is wrong.
    """
    model = ui.progress_view_model(
        decided=0,
        total=1110,
        session_decisions=session_decisions,
        session_seconds=session_seconds,
    )

    assert (model["per_minute"], model["eta_minutes"]) == (None, None)
    assert (model["decided"], model["remaining"]) == (0, 1110)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("session_decisions", "session_seconds", "per_minute", "eta_minutes"),
    [
        (5, 60.0, 5.0, 200.0),
        (10, 120.0, 5.0, 200.0),
        (1, 30.0, 2.0, 500.0),
    ],
)
def test_progress_view_model__session_decisions__yield_pace_and_eta(
    session_decisions: int, session_seconds: float, per_minute: float, eta_minutes: float
) -> None:
    """Pace is this session's decisions over this session's minutes, and the ETA follows.

    BUILD_PLAN line 1073 calls this "what sustains a multi-hour task", which
    only holds while the numbers are true: an ETA a reviewer catches out once
    is an ETA they stop reading.
    """
    model = ui.progress_view_model(
        decided=110,
        total=1110,
        session_decisions=session_decisions,
        session_seconds=session_seconds,
    )

    assert model["per_minute"] == pytest.approx(per_minute)
    assert model["eta_minutes"] == pytest.approx(eta_minutes)


@pytest.mark.unit
def test_progress_markdown__no_pace_yet__still_reports_the_counts() -> None:
    """Progress must show from the first record, before any pace exists."""
    line = ui.progress_markdown(
        ui.progress_view_model(decided=7, total=30, session_decisions=0, session_seconds=0.0)
    )

    assert "7 / 30" in line
    assert "23 to go" in line


# ---------------------------------------------------------------------------
# The reason palette (BUILD_PLAN line 1077)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reason_palette__criteria_codes__are_numbered_from_one_in_declaration_order() -> None:
    """The digit is the keystroke, so the numbering is part of the contract.

    Ordering by anything but declaration order -- alphabetically, say -- would
    silently renumber a reviewer's muscle memory the day a code was added.
    """
    palette = ui.reason_palette(
        criteria(abstract_codes=("WRONG_POPULATION", "OFF_TOPIC", "REVIEW_OR_SURVEY")),
        PrismaStage.TITLE_ABSTRACT,
    )

    assert palette == {"1": "WRONG_POPULATION", "2": "OFF_TOPIC", "3": "REVIEW_OR_SURVEY"}


@pytest.mark.unit
def test_reason_palette__fulltext_stage__uses_the_fulltext_vocabulary() -> None:
    """The two stages exclude for different reasons, and the log enforces that.

    Offering the abstract stage's codes at full text would produce exclusions
    the log refuses -- or, worse, accepts under a code the protocol meant for
    a different question.
    """
    palette = ui.reason_palette(
        criteria(abstract_codes=("OFF_TOPIC",), fulltext_codes=("NO_FULL_TEXT", "DUPLICATE")),
        PrismaStage.FULLTEXT,
    )

    assert palette == {"1": "NO_FULL_TEXT", "2": "DUPLICATE"}


@pytest.mark.unit
def test_reason_palette__more_than_nine_codes__numbers_only_the_first_nine() -> None:
    """There are nine digits, and a tenth code must not steal one of them.

    Silently wrapping onto ``0``, or reusing ``1``, would file exclusions
    under a code the reviewer did not choose -- invisible until the PRISMA
    breakdown is published.
    """
    palette = ui.reason_palette(
        criteria(abstract_codes=tuple(f"CODE_{index}" for index in range(1, 12))),
        PrismaStage.TITLE_ABSTRACT,
    )

    assert list(palette) == list(ui.REASON_DIGITS)
    assert palette["9"] == "CODE_9"


@pytest.mark.unit
@pytest.mark.parametrize(
    "stage", [PrismaStage.RAW, PrismaStage.AUTOMATED, PrismaStage.LANGUAGE, PrismaStage.INCLUDED]
)
def test_reason_palette__a_stage_no_human_screens__raises(stage: PrismaStage) -> None:
    """The computed stages have no exclusion vocabulary; asking for one is a bug.

    Returning an empty palette instead would present a screening UI with no
    way to exclude anything and no explanation.
    """
    with pytest.raises(ValidationError, match="not a screening stage"):
        ui.reason_palette(criteria(abstract_codes=("OFF_TOPIC",)), stage)


# ---------------------------------------------------------------------------
# The key map (BUILD_PLAN line 1071)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_keymap__bindings__are_unique_and_cover_the_documented_keys() -> None:
    """BUILD_PLAN line 1071 names the letters; two bindings on one key is a coin toss.

    A duplicated key does not fail loudly -- the first match wins and the
    second binding is simply unreachable, which is the same defect as a dead
    key wearing a different hat.
    """
    keys = [binding.key for binding in ui.KEY_MAP]

    assert len(keys) == len(set(keys))
    assert set("ieunpz?") | set(ui.REASON_DIGITS) == set(keys)
    assert all(binding.help for binding in ui.KEY_MAP)


@pytest.mark.unit
def test_keymap__javascript__routes_every_binding_to_its_action() -> None:
    """The browser half of the map is generated from the same data as the Python half.

    Hand-written JavaScript is where a keyboard map rots: the handler is
    added, the listener is not, and the key does nothing in the browser while
    every Python test still passes. Asserting the generated source carries
    each pair is the closest a headless test gets to the browser, and it fails
    the moment the two are written separately.
    """
    javascript = ui.keymap_javascript(ui.KEY_MAP)

    assert all(f'"{binding.key}": "{binding.action}"' in javascript for binding in ui.KEY_MAP), (
        javascript
    )
    assert "document.addEventListener('keydown'" in javascript


@pytest.mark.unit
@pytest.mark.parametrize("tag", ["input", "textarea", "select"])
def test_keymap__javascript__stands_down_inside_text_entry(tag: str) -> None:
    """Typing a note must be typing, not a sequence of screening decisions.

    A document-level listener is what makes the keys work without the mouse
    entering the widget; the price is that it also sees every keystroke typed
    into a field, and ``e`` in a note would otherwise arm an exclusion.
    """
    javascript = ui.keymap_javascript(ui.KEY_MAP)

    assert f"'{tag}'" in javascript


@pytest.mark.unit
def test_help_markdown__lists_every_binding_with_its_key() -> None:
    """``?`` is only useful while it documents the map it is generated from.

    The help text is built from :data:`KEY_MAP` rather than written out, so a
    binding cannot be added without appearing here and cannot be removed while
    its line lingers. An undocumented key is, for the reviewer, indistinguishable
    from a key that does not exist.
    """
    help_text = ui.help_markdown(ui.KEY_MAP)

    assert all(f"`{binding.key}`" in help_text for binding in ui.KEY_MAP)
    assert all(binding.help in help_text for binding in ui.KEY_MAP)


@pytest.mark.unit
@pytest.mark.parametrize("shown", ["Alvarez, Rosa", "417"])
def test_record_markdown__unblinded_view_model__shows_authors_and_citations(shown: str) -> None:
    """``blind=False`` has to actually show them, or the toggle is theatre.

    A protocol that has decided author names are legitimate to see -- an update
    of a known author's earlier work, say -- gets nothing from a flag that only
    changes a dict nobody renders.
    """
    markdown = ui.record_markdown(ui.record_view_model(RECORD, blind=False))

    assert shown in markdown
