"""The three chip inputs on Query carry their visible label as accessible name (ticket #382).

What this proves: in a headless Chromium-family browser, the search input of each chip box (Search
terms, Compound terms, Subject areas) has the visible label as accessible name, also once a chip
is added; the hint shown in the empty box is that label; and what the hint does once a chip is
added or text is typed is asserted by the last test: the entry is empty after a chip, so the hint shows. What this does not prove: what a screen reader
announces, or any other browser.

Opt-in like the rest of the folder: ``PRISMABIB_BROWSER_CHECK=1``.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Locator, Page, expect

from tests.integration.gui.browser import harness as h
from tests.integration.gui.browser.harness import Rig
from tests.integration.gui.browser.test_focus_ring import QUERY

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get(h.OPT_IN_VAR), reason=f"the browser check is opt-in: {h.OPT_IN_VAR}=1"
    ),
]

LABELS = ["Search terms", "Compound terms", "Subject areas"]
_WAIT = h._STATE_WAIT


def box(page: Page, label: str) -> Locator:
    """The chip box whose visible label is ``label``."""
    return page.locator(".bk-input-group").filter(has=page.get_by_text(label, exact=True))


def search_input(page: Page, label: str) -> Locator:
    return box(page, label).locator("input.choices__input--cloned")


def add_chip(page: Page, label: str) -> None:
    """Add one chip the way a researcher does: type into the entry (or pick a subject area)."""
    if label == "Subject areas":
        search_input(page, label).click()
        box(page, label).locator(".choices__item--choice").first.click()
    else:
        hint = {"Search terms": "a phrase, then Enter"}.get(label, "parts that must all appear")
        entry = page.get_by_placeholder(hint, exact=False).first
        entry.fill("alpha + beta" if label == "Compound terms" else "alpha")
        entry.press("Enter")
    box(page, label).locator(".choices__list--multiple .choices__item").first.wait_for()


@pytest.mark.parametrize("label", LABELS)
def test_chip_input__accessible_name__is_the_visible_label(rig: Rig, label: str) -> None:
    with rig.session(QUERY) as page:
        expect(box(page, label).locator("label")).to_be_visible()
        expect(search_input(page, label)).to_have_accessible_name(label, timeout=_WAIT)


@pytest.mark.parametrize("label", LABELS)
def test_chip_input__accessible_name__survives_adding_a_chip(rig: Rig, label: str) -> None:
    with rig.session(QUERY) as page:
        add_chip(page, label)
        expect(search_input(page, label)).to_have_accessible_name(label, timeout=_WAIT)


@pytest.mark.parametrize("label", LABELS)
def test_chip_input__empty_box__shows_the_label_as_hint(rig: Rig, label: str) -> None:
    with rig.session(QUERY) as page:
        expect(search_input(page, label)).to_have_attribute("placeholder", label, timeout=_WAIT)


@pytest.mark.parametrize("label", LABELS)
def test_chip_input__after_a_chip_and_while_typing__the_hint_is_the_label(
    rig: Rig, label: str
) -> None:
    """The entry is empty after a chip (so the browser shows the hint) and holds what is typed."""
    with rig.session(QUERY) as page:
        field = search_input(page, label)
        add_chip(page, label)
        expect(field).to_have_value("", timeout=_WAIT)
        expect(field).to_have_attribute("placeholder", label, timeout=_WAIT)
        field.fill("x")
        expect(field).to_have_value("x", timeout=_WAIT)
        expect(field).to_have_attribute("placeholder", label, timeout=_WAIT)
