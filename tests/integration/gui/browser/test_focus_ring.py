"""The focus ring of the built app, measured in a real browser (ticket #372, spec #371).

What this proves: with the real Tab key (and, for some controls, a mouse click) in a headless
Chromium-family browser, every kind of control on every screen shows a ring that covers at least
0.98 of each straight side, and a painted chip has the palette colours. The controls that work
today are plain tests (the regression guard). Each of the six defects of the follow-up is a strict
expected failure that records the values measured on main at af95bc6; the ticket that fixes a
defect removes its marker (#373 to #377).

What this does not prove: anything on another browser, operating system or font set than the one
running it; real Ctrl+plus zoom (200% is emulated with a 640 by 450 viewport and a device scale
factor of 2); what a screen reader announces.

It runs only when ``PRISMABIB_BROWSER_CHECK`` is set (the ``browser-focus`` job does; locally:
``PRISMABIB_BROWSER_CHECK=1 pytest tests/integration/gui/browser``). Once set, a missing browser
fails the run. An expected-failure marker accepts only an ``AssertionError`` about a measured
value: ``HarnessError`` (no browser, no server, a page that did not load) and Playwright's own
errors are not assertions, so they fail the build instead of passing as a hidden defect.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager

import pytest
from playwright.sync_api import Browser, Locator, Page, Playwright, expect, sync_playwright
from pytest_socket import enable_socket

from tests.integration.gui.browser import harness as h

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get(h.OPT_IN_VAR), reason=f"the browser check is opt-in: {h.OPT_IN_VAR}=1"
    ),
]

QUERY = h.Screen("Query")
CRITERIA = h.Screen("Criteria", "Criteria", "Criteria")
SOURCE_MODE = h.Screen("Source mode", "Source mode and keys", "Source mode and keys")
SCREENING = h.Screen("Screening", "Screening", "Screening")
FLOW = h.Screen("Flow counts", "Flow counts", "Flow counts")
NEW_PROJECT = h.Screen("New Project", where="new_project")
SETTINGS = h.Screen("Settings", where="settings")
SCREENS = [QUERY, CRITERIA, SOURCE_MODE, SCREENING, FLOW, NEW_PROJECT, SETTINGS]

#: Kinds that work today: ring on every side. The radio group is the Yes/No/Not sure buttons of
#: New Project, so it is covered as buttons there.
HEALTHY = {"button", "tab", "text", "password", "select", "checkbox"}


@pytest.fixture(autouse=True)
def _loopback() -> None:
    """The suite bans sockets; the browser driver and the app need a loopback."""
    enable_socket()


class Rig:
    """The app and the browser, plus every Tab walk already made (a walk is slow, its reading free)."""

    def __init__(self, app: h.App, browser: Browser) -> None:
        self.app, self.browser = app, browser
        self._walks: dict[tuple[str, str, frozenset[str] | None], list[h.Stop]] = {}

    def walk(
        self, screen: h.Screen, view: str = "100", only: frozenset[str] | None = None
    ) -> list[h.Stop]:
        """Tab through ``screen`` on a fresh Project; measure every control (or just ``only`` kinds)."""
        key = (screen.name, view, only)
        if key not in self._walks:
            with h.session(self.app, self.browser, screen, view) as page:
                counter = page.context.new_page()
                page.bring_to_front()
                self._walks[key] = h.tab_stops(
                    page,
                    counter,
                    screen.name,
                    view=view,
                    measure=lambda kind: only is None or kind in only,
                )
        return self._walks[key]

    def session(self, screen: h.Screen = QUERY, view: str = "100") -> AbstractContextManager[Page]:
        return h.session(self.app, self.browser, screen, view)


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Rig]:
    enable_socket()
    # Importing the GUI makes tornado select the selector event loop on Windows, which cannot
    # start Playwright's driver process. Its loop is created at start, so the policy is only
    # swapped for that call and put back: it is process-global state.
    policy = asyncio.get_event_loop_policy()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        playwright: Playwright = sync_playwright().start()
    finally:
        asyncio.set_event_loop_policy(policy)
    app: h.App | None = None
    browser: Browser | None = None
    try:
        browser = h.launch_browser(playwright)
        app = h.App(tmp_path_factory.mktemp("focus-app"))
        yield Rig(app, browser)
    finally:
        if browser:
            browser.close()
        if app:
            app.stop()
        playwright.stop()


def measured(stops: list[h.Stop], *kinds: str) -> list[h.Stop]:
    return [s for s in stops if s.kind in kinds]


def too_short(stops: list[h.Stop]) -> list[str]:
    """One line per side below the pass line, naming the screen, the control and the side."""
    return [
        f"{s.screen}: {s.label}: {side} {value}"
        for s in stops
        for side, value in h.short_of(s.coverage).items()
    ]


# -- the controls that work today: the regression guard --------------------------------------------


@pytest.mark.parametrize("screen", SCREENS, ids=lambda s: s.name)
def test_ring__controls_that_work__cover_every_side_after_tab(rig: Rig, screen: h.Screen) -> None:
    stops = rig.walk(screen)
    healthy = measured(stops, *HEALTHY)
    assert measured(stops, "tab"), f"{screen.name}: the walk never reached the tab row"
    assert not too_short(healthy), "ring below 0.98:\n" + "\n".join(too_short(healthy))


#: Clicks that make the page change (a new Project has no search terms, so Save refuses): wait for this text before measuring (the message pushes the
#: button down, and a ring measured at the old place would be measured on nothing).
AFTER_CLICK = {"Save button": "Not saved. Correct the fields marked above."}

#: What to click on each screen: (a description, how to find them). Every match is clicked in
#: turn. Only controls whose click is harmless (no network, no navigation away from the screen).
CLICKS: dict[str, list[tuple[str, Callable[[Page], Locator], int]]] = {
    "Query": [
        ("text input", lambda p: p.locator("input.bk-input[type=text]:visible").first, 1),
        ("Add button", lambda p: p.get_by_role("button", name="Add to: Search terms"), 1),
        ("Save button", lambda p: p.get_by_role("button", name="Save query"), 1),
        ("tab", lambda p: p.locator(".bk-tab").first, 1),
        ("chip box", lambda p: p.locator("input.choices__input--cloned:visible").last, 1),
    ],
    "Criteria": [
        ("text inputs", lambda p: p.locator("input.bk-input[type=text]:visible"), 3),
        ("text areas", lambda p: p.locator("textarea.bk-input:visible"), 6),
    ],
    "Source mode": [("password field", lambda p: p.locator("input[type=password]:visible"), 1)],
    "Flow counts": [
        ("text input", lambda p: p.locator("input.bk-input[type=text]:visible").first, 1),
        ("selects", lambda p: p.locator("select.bk-input:visible"), 3),
        ("text area", lambda p: p.locator("textarea.bk-input:visible"), 1),
    ],
    "New Project": [
        ("text inputs", lambda p: p.locator("input.bk-input[type=text]:visible"), 2),
        ("radio group", lambda p: p.get_by_role("button", name="No", exact=True), 1),
        ("checkbox", lambda p: p.locator("input[type=checkbox]:visible"), 1),
    ],
    "Settings": [("text input", lambda p: p.locator("input.bk-input[type=text]:visible").first, 1)],
}


@pytest.mark.parametrize("screen", [s for s in SCREENS if s.name in CLICKS], ids=lambda s: s.name)
def test_ring__controls_that_work__cover_every_side_after_a_click(
    rig: Rig, screen: h.Screen
) -> None:
    clicked: list[h.Stop] = []
    with rig.session(screen) as page:
        counter = page.context.new_page()
        page.bring_to_front()
        for what, find, count in CLICKS[screen.name]:
            targets = find(page)
            # Ready state: the screen has drawn all of its controls of this kind (it keeps
            # drawing after its heading shows). A miss fails the run, naming the screen.
            try:
                expect(targets).to_have_count(count, timeout=10_000)
            except AssertionError as exc:
                raise h.HarnessError(f"{screen.name}: expected {count} {what}: {exc}") from exc
            message = AFTER_CLICK.get(what)
            settled = (lambda m=message: page.get_by_text(m).wait_for()) if message else None
            for n in range(count):
                stop = h.click_and_measure(
                    page, counter, targets.nth(n), settled, f"{screen.name}: {what} {n + 1}"
                )
                stop.screen = screen.name
                clicked.append(stop)
    assert not too_short(clicked), "ring below 0.98 after a click:\n" + "\n".join(
        too_short(clicked)
    )


# -- what the walk finds: the check must not pass because it found nothing -------------------------

EXPECTED_KINDS = {
    "Query": {"chip": 3, "number": 2, "text": 2},
    "Criteria": {"text": 3, "textarea": 6},
    "Source mode": {"password": 1},
    "Screening": {},
    "Flow counts": {"text": 1, "textarea": 1, "select": 3},
    "New Project": {"text": 2, "checkbox": 1},
    "Settings": {"text": 1},
}


@pytest.mark.parametrize("screen", SCREENS, ids=lambda s: s.name)
def test_walk__every_screen__finds_the_controls_the_measure_is_about(
    rig: Rig, screen: h.Screen
) -> None:
    found = Counter(s.kind for s in rig.walk(screen) if s.kind not in {"button", "tab", "body"})
    assert dict(found) == EXPECTED_KINDS[screen.name], screen.name


# -- the six defects, strict expected failures at af95bc6 (D5 is fixed by #376 and its marker is gone) ------------------------------------------
# A marker passes only while the measured value is bad. When the defect is fixed it passes for
# real, the strict marker turns that into a failure, and the fixing ticket deletes the marker.

ASSERTION_ONLY = {"strict": True, "raises": AssertionError}


def test_defect_d1__text_areas__have_a_ring_on_every_side(rig: Rig) -> None:
    areas = measured(rig.walk(CRITERIA) + rig.walk(FLOW), "textarea")
    assert not too_short(areas), "text area ring below 0.98:\n" + "\n".join(too_short(areas))


def test_ring__number_fields__cover_every_side_after_tab(rig: Rig) -> None:
    fields = measured(rig.walk(QUERY), "number")
    assert not too_short(fields), "number field ring below 0.98:\n" + "\n".join(too_short(fields))


def test_ring__spin_arrows__are_not_tab_stops(rig: Rig) -> None:
    spins = [s.label for s in measured(rig.walk(QUERY), "spin")]
    assert not spins, f"{len(spins)} spin arrows are Tab stops: {spins}"


def test_ring__number_fields__cover_every_side_after_a_click(rig: Rig) -> None:
    clicked: list[h.Stop] = []
    with rig.session(QUERY) as page:
        counter = page.context.new_page()
        page.bring_to_front()
        fields = page.locator(".bk-spin-wrapper input:visible")
        expect(fields).to_have_count(2, timeout=10_000)
        for n in range(2):
            stop = h.click_and_measure(page, counter, fields.nth(n), None, f"year field {n + 1}")
            stop.screen = QUERY.name
            clicked.append(stop)
    assert not too_short(clicked), "ring below 0.98 after a click:\n" + "\n".join(
        too_short(clicked)
    )


def test_years__typing_the_keys_and_the_mouse__change_the_value(rig: Rig) -> None:
    """Typing changes a year; Up and Down change it (recorded, #374); a click on an arrow does too."""
    with rig.session(QUERY) as page:
        fields = page.locator(".bk-spin-wrapper input:visible")
        expect(fields).to_have_count(2, timeout=10_000)
        first = fields.nth(0)
        first.fill("1950")
        first.press("Enter")  # Bokeh's spinner commits a typed number on Enter or blur
        assert first.input_value() == "1950"
        first.press("ArrowUp")
        assert first.input_value() == "1951", "ArrowUp no longer changes the year"
        first.press("ArrowDown")
        assert first.input_value() == "1950", "ArrowDown no longer changes the year"
        up = page.locator(".bk-spin-wrapper").first.locator(".bk-spin-btn-up")
        box = up.bounding_box()
        assert box is not None
        # The arrow glyph sits in the middle of the button's upper half.
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_function(
            "e => e.value === '1951'", arg=first.element_handle(), timeout=10_000
        )



def test_ring__number_fields_at_200_percent__cover_every_side_after_tab(rig: Rig) -> None:
    fields = measured(rig.walk(QUERY, "200", frozenset({"number"})), "number")
    assert not too_short(fields), "number field ring below 0.98:\n" + "\n".join(too_short(fields))


#: view -> which way the Subject areas dropdown must open in it. The dropdown covers the box's
#: bottom edge when it opens below and its top edge when it flips above.
DROPDOWN_CASES = [
    pytest.param("100", "below", id="100%-below"),
    pytest.param("short", "above", id="100%-flipped"),
    pytest.param("200tall", "below", id="200%-below"),
    pytest.param("200", "above", id="200%-flipped"),
]


@pytest.mark.parametrize(("view", "opens"), DROPDOWN_CASES)
def test_ring__chip_box_with_dropdown_open__covers_every_side(
    rig: Rig, view: str, opens: str
) -> None:
    subject = measured(rig.walk(QUERY, view, frozenset({"chip"})), "chip")[-1]
    # The state the test claims to cover: a test must not pass on the other side's dropdown.
    assert subject.dropdown == opens, f"the dropdown opened {subject.dropdown}, not {opens}"
    assert not too_short([subject]), "chip box ring below 0.98:\n" + "\n".join(too_short([subject]))


def test_tab__subject_areas_to_save_query__is_one_step_both_ways(rig: Rig) -> None:
    with rig.session(QUERY) as page:
        chips = 0
        for _ in range(40):
            page.keyboard.press("Tab")
            info = page.evaluate(h.FOCUS_INFO)
            chips += bool(info) and h.kind(info) == "chip" and not info["body"]
            if chips == 3:
                break
        else:
            raise h.HarnessError("the Subject areas input was not reached")
        page.wait_for_function(h.SETTLED, timeout=10_000)  # its dropdown has opened
        page.keyboard.press("Tab")
        page.wait_for_function(h.LEFT_CHIPS, timeout=10_000)  # and has closed again
        after = page.evaluate(h.FOCUS_INFO)
        page.keyboard.press("Shift+Tab")
        back = page.evaluate(h.FOCUS_INFO)
    assert after and not after["body"], "Tab after the Subject areas input leaves focus on the body"
    assert after["name"] == "Save query", after["active"]
    assert back and h.kind(back) == "chip", back and back["active"]


def test_tab__walk_of_query__never_rests_on_the_page_body(rig: Rig) -> None:
    # Not the measured walk: measuring blurs each chip box, which closes its dropdown and removes
    # the stop the defect lives in. This walk presses Tab only, waiting for each chip box's
    # dropdown to be open before the Tab and for focus to settle after it.
    with rig.session(QUERY) as page:
        stops = h.tab_stops(page, page, QUERY.name, measure=lambda _kind: False)
    resting = [s.label for s in stops if s.kind == "body"]
    assert not resting, f"focus rested on the page body: {resting}"
    assert sum(s.kind == "chip" for s in stops) == 3, "the walk did not pass the three chip boxes"


def test_tab__every_stop_on_query__has_an_accessible_name(rig: Rig) -> None:
    with rig.session(QUERY) as page:
        stops = h.tab_stops(page, page, QUERY.name, measure=lambda _kind: False, names=True)
    unnamed = [f"{s.label}: {s.snapshot!r}" for s in stops if s.kind != "body" and not s.has_name]
    assert not unnamed, "Tab stops with an empty accessible name:\n" + "\n".join(unnamed)
    assert len(stops) >= 20, "the walk found too few stops to mean anything"


def test_defect_d6__painted_chip__has_the_palette_colours(rig: Rig) -> None:
    with rig.session(QUERY) as page:
        entry = page.get_by_placeholder("a phrase, then Enter").first
        entry.fill("carbon capture")
        entry.press("Enter")
        chip = page.locator(".choices__list--multiple .choices__item").first
        chip.wait_for()
        painted = chip.evaluate(
            "e=>{const c=getComputedStyle(e);return {background:c.backgroundColor,color:c.color}}"
        )
    assert painted == {"background": "rgb(207, 226, 255)", "color": "rgb(10, 88, 202)"}, painted
