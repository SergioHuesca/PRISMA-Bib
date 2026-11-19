"""No textbox, checkbox or combobox on any screen has the accessible name "", "null" or "undefined".

Ticket #384, spec #381. The sweep reads the whole accessibility tree of each screen of the
focus-ring enumeration (fresh Project each time) through the Chrome DevTools Protocol, which sees
inside the Choices.js shadow roots, and fails on every control of those three roles whose name is
bad. The older check reads only the Tab stops of Query and cannot see "null" (it is not empty).

Controls whose name cannot be set are in ``EXCEPTIONS``: one place, with the reason. An entry that
matches no control fails the run, so the list shrinks as causes are fixed. Issue #380 points here.

What this does not prove: names in another browser, or what a screen reader announces. The
Screening screen shows no content controls on a fresh Project, so it sweeps nothing there.

Opt-in like the rest of the folder: ``PRISMABIB_BROWSER_CHECK=1``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final

import pytest
from playwright.sync_api import Page

from tests.integration.gui.browser import harness as h
from tests.integration.gui.browser.harness import Rig
from tests.integration.gui.browser.test_focus_ring import SCREENS

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get(h.OPT_IN_VAR), reason=f"the browser check is opt-in: {h.OPT_IN_VAR}=1"
    ),
]

ROLES: Final = frozenset({"textbox", "checkbox", "combobox"})
BAD_NAMES: Final = frozenset({"", "null", "undefined"})
#: The visible label of a control: the label text of the Panel widget that holds it, looking out of
#: any shadow root on the way (the chip inputs and their containers sit inside one).
_LABEL = """function(){
  let e=this;
  while(e){const g=e.closest&&e.closest('.bk-input-group');
    if(g){const l=g.querySelector('label');return l?l.innerText.trim():''}
    const r=e.getRootNode&&e.getRootNode();e=r&&r.host}
  return ''}"""


@dataclass(frozen=True)
class Exception_:
    """A control whose name cannot be set, keyed by screen, role and visible label."""

    screen: str
    role: str
    label: str
    reason: str


_CHIP_REASON = (
    "Choices.js puts role=combobox on the box container and Bokeh passes no label id, so the "
    "container has no name; whether Panel can name it is UNVERIFIED. Tracked in #410."
)
#: Every control allowed a bad name. The sweep fails on an entry that matches no control.
EXCEPTIONS: Final = [
    Exception_("Query", "combobox", label, _CHIP_REASON)
    for label in ("Search terms", "Compound terms", "Subject areas")
]


@dataclass(frozen=True)
class Control:
    screen: str
    role: str
    label: str
    name: str

    def __str__(self) -> str:
        return f"{self.screen}: {self.role} {self.label!r} has name {self.name!r}"


def sweep(page: Page, screen: str) -> list[Control]:
    """Every textbox, checkbox and combobox in the accessibility tree of ``page`` with its name."""
    cdp = page.context.new_cdp_session(page)
    try:
        nodes: list[dict[str, Any]] = cdp.send("Accessibility.getFullAXTree")["nodes"]
        found = []
        for n in nodes:
            role = n.get("role", {}).get("value")
            if n.get("ignored") or role not in ROLES or "backendDOMNodeId" not in n:
                continue
            obj = cdp.send("DOM.resolveNode", {"backendNodeId": n["backendDOMNodeId"]})["object"]
            label = cdp.send(
                "Runtime.callFunctionOn",
                {"objectId": obj["objectId"], "functionDeclaration": _LABEL, "returnByValue": True},
            )["result"]["value"]
            name = n.get("name", {}).get("value", "")
            found.append(Control(screen, role, label, name))
        return found
    finally:
        cdp.detach()


def violations(controls: list[Control], exceptions: list[Exception_]) -> list[str]:
    """Bad names not excused by an exception, and exceptions that excuse nothing."""
    bad = [c for c in controls if c.name in BAD_NAMES]
    # An empty label identifies nothing, so no exception can excuse it (nor hide another control).
    excused = {(e.screen, e.role, e.label) for e in exceptions if e.label}
    out = [
        str(c) if c.label else f"{c.screen}: {c.role} with no visible label has name {c.name!r}"
        for c in bad
        if not c.label or (c.screen, c.role, c.label) not in excused
    ]
    out += [
        f"{e.screen}: exception for {e.role} has an empty label" for e in exceptions if not e.label
    ]
    seen = {(c.screen, c.role, c.label) for c in bad}
    out += [
        f"{e.screen}: exception for {e.role} {e.label!r} matches no control with a bad name"
        for e in exceptions
        if (e.screen, e.role, e.label) not in seen
    ]
    return out


@pytest.mark.parametrize("screen", SCREENS, ids=lambda s: s.name)
def test_sweep__every_screen__no_control_has_an_empty_null_or_undefined_name(
    rig: Rig,
    screen: h.Screen,
) -> None:
    with rig.session(screen) as page:
        controls = sweep(page, screen.name)
    mine = [e for e in EXCEPTIONS if e.screen == screen.name]
    assert violations(controls, mine) == []
