"""What the real-browser focus check shares: the app on its own port, a browser, the ring measure.

The method (ticket #372, facts in ``.claude/gui-312/drafts/focus-followup-facts.md``): start the
built app as a child process on a port the OS picks, with its own Projects folder and user
settings file; drive a headless Chromium-family browser; press the real Tab key; take a screenshot
focused and one blurred; the ring is the pixels that are ``rgb(17,17,17)`` when focused and not
when blurred. Coverage of a side is the share of positions along its straight part (the corners
are left out) where ring pixels are painted in the band the outline occupies (``offset`` to
``offset + width`` outside the border box; inside it for a negative offset), with one pixel of
slack each way for anti-aliasing. Nothing here reads a clock, a locale or a time zone; every wait
is for a state of the page, never for a duration.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from playwright.sync_api import Browser, BrowserContext, Error, Locator, Page, Playwright

import prismabib
from prismabib.gui import layer
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR

#: The check runs only when this is set, so the five required jobs never need a browser. When it
#: is set a missing browser FAILS the run: a hidden defect must not keep main green.
OPT_IN_VAR: Final = "PRISMABIB_BROWSER_CHECK"
PASS_LINE: Final = 0.98
RING: Final = (17, 17, 17)
SIDES: Final = ("top", "right", "bottom", "left")
#: Local preference: the browser the facts were measured with, then the other installed ones.
CHANNELS: Final = ("msedge", "chrome", "chromium")
#: name -> (width, height, device scale factor). "200" is the emulated 200% zoom; "short" makes
#: the chip dropdown flip upward (the Subject areas box sits near the bottom edge); "200tall" is
#: the same zoom in a window tall enough for the dropdown to open below.
VIEWS: Final = {
    "100": (1280, 900, 1),
    "200": (640, 450, 2),
    "200tall": (640, 1100, 2),
    "short": (1280, 600, 1),
}
_MARGIN: Final = 10  # CSS px kept around the border box in a screenshot
_SERVER_WAIT: Final = 120
_STATE_WAIT: Final = 10_000  # ms; a state that does not arrive fails the run, it is not skipped


class HarnessError(Exception):
    """The check itself could not run (no browser, no server, a page that did not load).

    Deliberately not an ``AssertionError``: an expected-failure marker only accepts assertions
    about a measured value, so a broken harness fails the build instead of passing as a defect.
    """


def launch_browser(pw: Playwright) -> Browser:
    """Headless Edge, Chrome or Chromium, whichever is installed; fail loudly when none is."""
    errors = []
    for channel in CHANNELS:
        try:
            return pw.chromium.launch(channel=channel, headless=True)
        except Error as exc:  # not installed; try the next
            errors.append(f"{channel}: {str(exc).splitlines()[0]}")
    raise HarnessError("no Chromium-family browser could be launched: " + "; ".join(errors))


class App:
    """The built app, started the way a researcher starts it, on its own port and folders."""

    def __init__(self, home: Path) -> None:
        self.root = home / "projects"
        self.root.mkdir()
        self._count = 0
        (home / "user.env").write_text("PRISMABIB_FIRST_RUN_SEEN=1\n", encoding="utf-8")
        # The child must run the code under test, not another copy that happens to be installed.
        source = str(Path(prismabib.__file__).resolve().parent.parent)
        env = {
            **os.environ,
            USER_FILE_OVERRIDE_VAR: str(home / "user.env"),
            "PRISMABIB_PROJECTS_ROOT": str(self.root),
            "PYTHONPATH": os.pathsep.join(filter(None, [source, os.environ.get("PYTHONPATH")])),
            "PYTHONUNBUFFERED": "1",
        }
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from prismabib.cli import app; app()",
                "gui",
                "--no-browser",
                "--port",
                "0",
            ],
            cwd=home,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        watchdog = threading.Timer(_SERVER_WAIT, self.proc.kill)  # a kill ends the read below
        watchdog.daemon = True
        watchdog.start()
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        watchdog.cancel()
        found = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        if not found:
            raise HarnessError(f"the app did not announce a loopback URL; got {line!r}")
        self.url = found.group(0)
        # Keep draining so a chatty server never blocks on a full pipe.
        threading.Thread(target=self.proc.stdout.read, daemon=True).start()

    def new_project(self) -> str:
        """A fresh Project (never opened by an earlier session, so never read-only)."""
        self._count += 1
        slug = f"demo-{self._count}"
        layer.create_project(
            slug, title="Demo", root=self.root, source_mode="openalex", reviewer="Ada"
        )
        return slug

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(30)
        except subprocess.TimeoutExpired:
            self.proc.kill()


@dataclass(frozen=True)
class Screen:
    """A screen to Tab through: the button that shows it and the heading it shows."""

    name: str
    button: str | None = None
    heading: str | None = None
    #: "project" (a loaded Project), "new_project" (the form on the start page) or "settings".
    where: str = "project"


#: Where Tab starts from: the top of the page (otherwise the browser continues from the last click).
PARK = "()=>{document.activeElement.blur();getSelection().collapse(document.body,0)}"


@contextmanager
def session(app: App, browser: Browser, screen: Screen, view: str = "100") -> Iterator[Page]:
    """A fresh browser context on ``screen`` of a fresh Project, focus parked at the page top.

    The Screening screen has no focusable control when nothing is loaded, hence a Project is
    always loaded first. A Project opened by an earlier session stays read-only for a while,
    hence a new one every time.
    """
    width, height, scale = VIEWS[view]
    ctx: BrowserContext = browser.new_context(
        viewport={"width": width, "height": height}, device_scale_factor=scale
    )
    try:
        page = ctx.new_page()
        slug = app.new_project()
        page.goto(app.url)
        if screen.where == "new_project":
            page.get_by_role("button", name="New Project").click()
            page.get_by_role("heading", name="New Project").first.wait_for()
        elif screen.where == "settings":
            page.get_by_role("button", name=f"Open {slug}").wait_for()
            page.get_by_text("Settings", exact=True).first.click()
            page.get_by_role("heading", name="Settings").first.wait_for()
        else:
            page.get_by_role("button", name=f"Open {slug}").click()
            page.get_by_role("button", name="Back to Projects").wait_for()
            if screen.button and screen.heading:
                page.get_by_role("button", name=screen.button).first.click()
                page.get_by_role("heading", name=screen.heading).first.wait_for()
        page.evaluate(PARK)
        yield page
    finally:
        ctx.close()


# -- what is focused, and what its ring looks like ------------------------------------------------

_DEEP_ACTIVE = """
function deepActive(){let e=document.activeElement;
  while(e&&e.shadowRoot&&e.shadowRoot.activeElement)e=e.shadowRoot.activeElement;return e}
function parentOf(e){return e.parentElement||(e.getRootNode&&e.getRootNode().host)||null}
function describe(e){if(!e)return null;
  return e.tagName.toLowerCase()+(e.id?'#'+e.id:'')+
  (typeof e.className==='string'&&e.className?'.'+e.className.trim().split(/\\s+/).join('.'):'')}
"""

FOCUS_INFO = (
    "() => {"
    + _DEEP_ACTIVE
    + """
 const a=deepActive(); if(!a) return null;
 let o=a, found=null, n=0;
 while(o&&n<12){const cs=getComputedStyle(o);
   if(cs.outlineStyle!=='none'&&parseFloat(cs.outlineWidth)>0){found=o;break} o=parentOf(o);n++}
 const t=found||a, cs=getComputedStyle(t), r=t.getBoundingClientRect();
 return {active:describe(a), body:a===document.body, tag:a.tagName.toLowerCase(),
  type:a.type||null,
  numeric:!!(a.parentElement&&a.parentElement.classList.contains('bk-spin-wrapper')),
  name:(a.getAttribute('aria-label')||a.innerText||a.value||'').trim().slice(0,40),
  hasOutline:!!found, outlined:describe(t),
  outline:{color:cs.outlineColor,width:parseFloat(cs.outlineWidth)||0,
           offset:parseFloat(cs.outlineOffset)||0,style:cs.outlineStyle},
  rect:{x:r.x,y:r.y,w:r.width,h:r.height}}
}
"""
)

#: True once a focused chip box has opened its dropdown (it opens on focus); true for any other
#: control. The dropdown is what covers a chip box's edge, so it is measured open, never racing.
SETTLED = (
    "()=>{"
    + _DEEP_ACTIVE
    + "const c=deepActive().closest('.choices');return !c||c.classList.contains('is-open')}"
)
#: True once focus is not inside a chip box (its dropdown closes when focus leaves).
LEFT_CHIPS = "()=>{" + _DEEP_ACTIVE + "return !deepActive().closest('.choices')}"
#: Which way the focused chip box's dropdown opened: "below" or "above" the box (or "none").
DROPDOWN_SIDE = (
    "()=>{"
    + _DEEP_ACTIVE
    + """const c=deepActive().closest('.choices');if(!c)return null;
 const d=c.querySelector('.choices__list--dropdown'),i=c.querySelector('.choices__inner');
 if(!d||!i)return null;const a=d.getBoundingClientRect(),b=i.getBoundingClientRect();
 if(!c.classList.contains('is-open')||a.height===0)return 'none';
 return a.top>=b.bottom-1?'below':(a.bottom<=b.top+1?'above':'overlap')}"""
)
_BLUR ="()=>{" + _DEEP_ACTIVE + "deepActive().blur()}"

_COUNT = """
async ([focused, blurred, dsf, rect, off, width, ring]) => {
 async function load(b){const i=new Image();i.src='data:image/png;base64,'+b;await i.decode();
   const c=document.createElement('canvas');c.width=i.width;c.height=i.height;
   const x=c.getContext('2d');x.drawImage(i,0,0);return x.getImageData(0,0,i.width,i.height)}
 const F=await load(focused),U=await load(blurred),W=F.width;
 const bx=rect.fx*dsf,by=rect.fy*dsf,bw=rect.w*dsf,bh=rect.h*dsf;
 const isRing=(D,i)=>Math.abs(D[i]-ring[0])<=24&&Math.abs(D[i+1]-ring[1])<=24
   &&Math.abs(D[i+2]-ring[2])<=24;
 const hit=(x,y)=>{if(x<0||y<0||x>=W||y>=F.height)return 0;const i=(y*W+x)*4;
   return isRing(F.data,i)&&!isRing(U.data,i)?1:0};
 const a=off*dsf,b=(off+width)*dsf,lo=Math.min(a,b),hi=Math.max(a,b);
 // corners (the arcs) are left out; small controls have small corners
 const corner=(Math.min(bw,bh)<30*dsf?4:8)*dsf, tol=1;
 // share of positions along one side where the ring colour is painted anywhere across the band
 // (one pixel of slack each way for anti-aliased edges and fractional layout)
 function side(horizontal, from, to, across0, across1){
   let present=0,total=0;
   for(let p=Math.ceil(from);p<Math.floor(to);p++){
     total++; let any=0;
     for(let q=Math.round(across0)-tol;q<Math.round(across1)+tol&&!any;q++)
       any=horizontal?hit(p,q):hit(q,p);
     present+=any;}
   return total>0?Math.round(present/total*1000)/1000:0}
 return {
  top:side(true,bx+corner,bx+bw-corner,by-hi,by-lo),
  bottom:side(true,bx+corner,bx+bw-corner,by+bh+lo,by+bh+hi),
  left:side(false,by+corner,by+bh-corner,bx-hi,bx-lo),
  right:side(false,by+corner,by+bh-corner,bx+bw+lo,bx+bw+hi)};
}
"""


def kind(info: dict[str, Any]) -> str:
    """The kind of control that has focus (the words of the spec's list of control kinds)."""
    active, tag = str(info["active"]), info["tag"]
    if "bk-spin-btn" in active:
        return "spin"
    if "choices__input" in active:
        return "chip"
    if "bk-tab" in active:
        return "tab"
    if tag == "input":
        if info["numeric"]:
            return "number"
        return info["type"] if info["type"] in {"password", "checkbox"} else "text"
    return {"textarea": "textarea", "select": "select", "button": "button"}.get(tag, "other")


@dataclass
class Stop:
    """One thing that has focus: what it is, and how much of its ring is painted on each side."""

    screen: str
    info: dict[str, Any]
    #: Empty when nothing was measured. Zero on every side when the control paints no ring.
    coverage: dict[str, float] = field(default_factory=dict)
    #: For a chip box: which way its open dropdown lies ("below" or "above"), read before measuring.
    dropdown: str | None = None
    #: With ``names``: the accessibility snapshot of the focused control (its role and name).
    snapshot: str = ""

    @property
    def has_name(self) -> bool:
        """Whether the snapshot gives the control a non-empty accessible name."""
        first = self.snapshot.splitlines()[0] if self.snapshot else ""
        return bool(re.search(r'"[^"]+"', first) or re.match(r"- text: \S", first))

    @property
    def kind(self) -> str:
        return "body" if self.info["body"] else kind(self.info)

    @property
    def label(self) -> str:
        return f"{self.kind} {self.info['active']} {self.info['name']!r}"


def measure_focused(page: Page, counter: Page, *, view: str = "100") -> dict[str, float]:
    """Ring coverage of the control that has focus now: focused screenshot versus blurred.

    ``counter`` is a second page of the same browser that decodes the screenshots on a canvas.
    A focused control that paints no outline has no ring: zero on every side.
    """
    page.wait_for_function(SETTLED, timeout=_STATE_WAIT)
    info: dict[str, Any] | None = page.evaluate(FOCUS_INFO)
    if info is None or info["body"]:
        raise HarnessError("nothing is focused")
    if not info["hasOutline"]:
        return dict.fromkeys(SIDES, 0.0)
    dsf = VIEWS[view][2]
    r, vp = info["rect"], page.viewport_size
    if vp is None:
        raise HarnessError("the page has no viewport")
    x, y = max(0.0, r["x"] - _MARGIN), max(0.0, r["y"] - _MARGIN)
    clip = {
        "x": x,
        "y": y,
        "width": min(r["w"] + 2 * _MARGIN, vp["width"] - x),
        "height": min(r["h"] + 2 * _MARGIN, vp["height"] - y),
    }
    focused = page.screenshot(clip=clip)
    page.evaluate(_BLUR)
    page.wait_for_function(LEFT_CHIPS, timeout=_STATE_WAIT)
    blurred = page.screenshot(clip=clip)
    rect = {"fx": r["x"] - clip["x"], "fy": r["y"] - clip["y"], "w": r["w"], "h": r["h"]}
    o = info["outline"]
    cov: dict[str, float] = counter.evaluate(
        _COUNT,
        [
            base64.b64encode(focused).decode(),
            base64.b64encode(blurred).decode(),
            dsf,
            rect,
            o["offset"],
            o["width"],
            list(RING),
        ],
    )
    return cov


def tab_stops(
    page: Page,
    counter: Page,
    screen: str,
    *,
    view: str = "100",
    measure: Callable[[str], bool] = lambda _kind: True,
    names: bool = False,
    limit: int = 40,
) -> list[Stop]:
    """Press Tab from the top of the page until focus reaches the tab row.

    Every stop is recorded; its ring is measured when it paints one and ``measure(kind)`` says so
    (measuring blurs the control; the browser still continues the walk from it). The walk ends at
    the first stop in the tab row, which holds Home and Settings and comes last on every screen.
    """
    stops: list[Stop] = []
    page.evaluate(PARK)
    for _ in range(limit):
        from_chip = bool(stops) and stops[-1].kind == "chip"
        if from_chip:
            page.wait_for_function(SETTLED, timeout=_STATE_WAIT)  # its dropdown is open
        page.keyboard.press("Tab")
        if from_chip:
            page.wait_for_function(LEFT_CHIPS, timeout=_STATE_WAIT)  # and has closed again
        info = page.evaluate(FOCUS_INFO)
        if info is None:
            break
        stop = Stop(screen, info)
        stops.append(stop)
        if names and stop.kind != "body":
            stop.snapshot = page.locator(":focus").last.aria_snapshot()
        if stop.kind == "chip":
            page.wait_for_function(SETTLED, timeout=_STATE_WAIT)
            stop.dropdown = page.evaluate(DROPDOWN_SIDE)
        if stop.kind != "body" and measure(stop.kind):
            stop.coverage = measure_focused(page, counter, view=view)
        if stop.kind == "tab":
            break
    else:
        raise HarnessError(f"{screen}: no tab row after {limit} Tab presses")
    return stops


def click_and_measure(
    page: Page,
    counter: Page,
    target: Locator,
    settled: Callable[[], None] | None = None,
    what: str = "control",
) -> Stop:
    """Click ``target`` with the mouse and measure the ring it then shows.

    ``settled`` waits for the page to stop changing when the click makes it change (a message
    that pushes the control down); the measure is taken after it.
    """
    try:
        target.click(timeout=10_000)  # a stuck click fails fast and says which control
    except Error as exc:
        raise HarnessError(f"{what}: the click did not go through: {exc}") from exc
    if settled:
        settled()
    info = page.evaluate(FOCUS_INFO)
    if info is None or info["body"]:
        raise HarnessError("the click did not focus the control")
    stop = Stop("click", info)
    stop.coverage = measure_focused(page, counter)
    return stop


def short_of(coverage: dict[str, float]) -> dict[str, float]:
    """The sides below the pass line (all four when nothing was measured)."""
    return {s: coverage.get(s, 0.0) for s in SIDES if coverage.get(s, 0.0) < PASS_LINE}
