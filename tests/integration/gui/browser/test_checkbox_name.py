"""The New Project checkbox has an accessible name and keeps its ring (ticket #383, spec #381).

Bokeh's ``Checkbox`` does not tie its label to the input, so a screen reader heard no name. The
screen now uses a one-option ``CheckBoxGroup``, which wraps the input in a ``<label>``. This
module asserts the name equals the visible label, that the ring still covers every side, and that
the Tab order of the screen is the one it had with the plain checkbox.

Opt-in like the rest of the browser check: ``PRISMABIB_BROWSER_CHECK=1``.
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect
from pytest_socket import enable_socket

from tests.integration.gui.browser import harness as h

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get(h.OPT_IN_VAR), reason=f"the browser check is opt-in: {h.OPT_IN_VAR}=1"
    ),
]

NEW_PROJECT = h.Screen("New Project", where="new_project", fields=3)
LABEL = "Also fill missing fields of Scopus records from OpenAlex"
#: The Tab order of New Project: the Back button, the two text fields, the three Scopus answer
#: buttons, the checkbox, Create Project, the tab row. Measured with the plain Checkbox it replaced.
TAB_ORDER = ["button", "text", "text", "button", "button", "button", "checkbox", "button", "tab"]


@pytest.fixture(autouse=True)
def _loopback() -> None:
    enable_socket()


def test_checkbox__new_project__accessible_name_is_the_visible_label(
    rig: h.Rig,
) -> None:
    with rig.session(NEW_PROJECT) as page:
        box = page.get_by_role("checkbox")
        expect(box).to_have_count(1, timeout=10_000)
        expect(box).to_have_accessible_name(LABEL)
        expect(page.get_by_text(LABEL, exact=True)).to_be_visible()


def test_checkbox__new_project__tab_order_and_ring_are_unchanged(
    rig: h.Rig,
) -> None:
    with rig.session(NEW_PROJECT) as page:
        counter = page.context.new_page()
        page.bring_to_front()
        stops = h.tab_stops(page, counter, NEW_PROJECT.name, names=True)
    assert [s.kind for s in stops] == TAB_ORDER
    checkbox = stops[TAB_ORDER.index("checkbox")]
    assert checkbox.has_name, checkbox.snapshot
    assert h.short_of(checkbox.coverage) == {}, f"ring below {h.PASS_LINE}: {checkbox.coverage}"
