"""Regenerate the beginner guide's screenshots from fixed fake data (ticket #426, spec #423).

Run it after any change to the app's screens::

    uv run python scripts/make_guide_screenshots.py --out <a folder outside the repository>

It needs the dev dependencies (Playwright and respx) and an installed Chromium-family browser
(Edge, Chrome or Chromium; ``uv run playwright install chromium`` installs one).

It starts the real app on a throwaway Projects folder, with the reviewer "Example Reviewer" and the
baseball example, then writes eight page-only PNGs (no browser chrome). The app's OpenAlex requests
are answered by a stub inside the app's own process: nothing is captured from a real source, and
the key it holds is a placeholder. The user-settings override variable points at a throwaway file,
so no real settings file is read or written. The Settings screen is never captured.

Privacy is enforced here, not left to inspection. Before each capture the throwaway folder's path is
replaced by a placeholder in the page (text, field values, labels). Then the page's text is checked
for that path, the home directory's path and the logged-in user's name. Any hit fails the run, and
no image is written: images are held in memory until every one has passed.

Harness choice (option 2, a short self-contained launch): the integration-test harness starts the
app with the real network and cannot hand it a stub, and importing it imports ``prismabib.gui``,
which swaps the process's asyncio policy on Windows and would break Playwright's driver here.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FOLDER_NAME = "prismabib-guide-example"  # neutral, fixed
PLACEHOLDER = "<your Projects folder>"
REVIEWER = "Example Reviewer"
PROJECT_NAME = "Baseball pose estimation"
BROWSERS = ("msedge", "chrome", "chromium")
VIEWPORT = {"width": 1280, "height": 760}
WAIT_MS = 60_000
STUB_FLAG = "--stub-app"

# -- the fake OpenAlex ----------------------------------------------------------------------------

_TITLES = (
    "Pose estimation of the throwing arm from broadcast video",
    "Monocular 3D skeleton tracking for baseball pitchers",
    "Keypoint detection under motion blur in pitching footage",
    "Joint angle estimation for pitch classification",
    "A benchmark of pose models on baseball broadcast frames",
    "Markerless pitcher biomechanics with a single camera",
    "Estimating release point from pose sequences",
    "Transformer pose estimation for sports video analysis",
    "Injury risk screening from pitcher pose trajectories",
    "Pitch type recognition from skeleton data",
    "Domain shift in baseball pose datasets",
    "Real-time pitcher pose estimation on edge devices",
)


def _work(n: int) -> dict[str, object]:
    words = f"{_TITLES[n - 1]}. This example abstract describes a pitcher, a camera and a model.".split()
    return {
        "id": f"https://openalex.org/W{9000 + n}",
        "doi": f"https://doi.org/10.0000/example.{n}",
        "title": _TITLES[n - 1],
        "publication_year": 2014 + n,
        "type": "article",
        "cited_by_count": n,
        "abstract_inverted_index": {w: [i] for i, w in enumerate(words)},
        "authorships": [
            {"author": {"id": f"https://openalex.org/A{n}", "display_name": "A. Author"}}
        ],
        "primary_location": {"source": {"display_name": "Journal of Examples", "type": "journal"}},
    }


def _stub_app() -> None:
    """Child process: the app, with every request to OpenAlex answered from the fake works."""
    import httpx
    import respx

    from prismabib.cli import app

    works = [_work(n) for n in range(1, len(_TITLES) + 1)]

    def answer(_request: httpx.Request) -> httpx.Response:
        meta = {"count": len(works), "per_page": 100, "next_cursor": None, "cost_usd": 0.001}
        return httpx.Response(200, json={"meta": meta, "results": works})

    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        mock.route(host="api.openalex.org").mock(side_effect=answer)
        app(
            args=[
                "gui",
                "--no-browser",
                "--port",
                "0",
                "--root",
                os.environ["PRISMABIB_PROJECTS_ROOT"],
            ]
        )


# -- privacy --------------------------------------------------------------------------------------

_WALK = """
(args) => {
  const [pairs, replace] = args;
  const out = [];
  const swap = (s) => { for (const [from, to] of pairs) s = s.replace(new RegExp(from, 'gi'), to); return s; };
  const walk = (n) => {
    if (n.nodeType === 3) {
      if (replace && n.textContent !== swap(n.textContent)) n.textContent = swap(n.textContent);
      out.push(n.textContent);
    } else if (n.nodeType === 1) {
      for (const a of ['value', 'placeholder', 'title', 'aria-label', 'alt']) {
        const v = n.getAttribute(a);
        if (v === null) continue;
        if (replace && v !== swap(v)) n.setAttribute(a, swap(v));
        out.push(v);
      }
      if (typeof n.value === 'string' && n.tagName !== 'BUTTON') {
        if (replace && n.value !== swap(n.value)) n.value = swap(n.value);
        out.push(n.value);
      }
      if (n.shadowRoot) walk(n.shadowRoot);
    }
    for (const c of n.childNodes) walk(c);
  };
  walk(document);
  return out.join('\\n');
}
"""


def _spellings(path: Path) -> list[str]:
    return list({str(path), path.as_posix(), str(path).replace("\\", "\\\\")})


class Privacy:
    def __init__(self, throwaway: Path) -> None:
        self.throwaway = throwaway
        self.user = getpass.getuser()
        self.forbidden = [*_spellings(throwaway), *_spellings(Path.home()), self.user]

    def scrub(self, page) -> None:
        pairs = [[re.escape(s), PLACEHOLDER] for s in _spellings(self.throwaway)]
        page.evaluate(_WALK, [pairs, True])

    def check(self, page, what: str) -> None:
        text = page.evaluate(_WALK, [[], False]).lower()
        for item in self.forbidden:
            if item and item.lower() in text:
                # Name the kind of hit, never the text: the output must hold no path or name.
                raise SystemExit(
                    f"FAIL: the page text of '{what}' holds a private value; no image written."
                )


# -- the run --------------------------------------------------------------------------------------


def _throwaway_root() -> Path:
    """The fixed-name folder, outside the home directory where the system allows."""
    home = Path.home().resolve()
    for base in (Path(home.anchor), Path(tempfile.gettempdir())):
        candidate = base / FOLDER_NAME
        try:
            if candidate.resolve().is_relative_to(home) and base != Path(tempfile.gettempdir()):
                continue
            shutil.rmtree(candidate, ignore_errors=True)
            candidate.mkdir()
            return candidate
        except OSError:
            continue
    raise SystemExit("FAIL: no place to create the throwaway Projects folder.")


def _launch(pw):
    from playwright.sync_api import Error

    for channel in BROWSERS:
        try:
            return pw.chromium.launch(channel=channel, headless=True)
        except Error:
            continue
    raise SystemExit(
        "FAIL: no Chromium-family browser (Edge, Chrome or Chromium) is installed for Playwright. "
        "Install one (for example: uv run playwright install chromium) and run again. "
        "No image was written."
    )


def _start_app(projects: Path, settings: Path) -> tuple[subprocess.Popen[str], str]:
    env = {
        **os.environ,
        "PRISMABIB_USER_SETTINGS_FILE": str(settings),
        "PRISMABIB_PROJECTS_ROOT": str(projects),
        "OPENALEX_API_KEY": "example-placeholder-not-a-key",  # pragma: allowlist secret
        "PYTHONUNBUFFERED": "1",
    }
    env.pop("SCOPUS_API_KEY", None)
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), STUB_FLAG],
        cwd=settings.parent,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    watchdog = threading.Timer(300, proc.kill)
    watchdog.daemon = True
    watchdog.start()
    assert proc.stdout is not None
    line = proc.stdout.readline()
    watchdog.cancel()
    found = re.search(r"http://127\.0\.0\.1:\d+/", line)
    if not found:
        proc.kill()
        raise SystemExit("FAIL: the app did not announce a loopback address.")
    threading.Thread(target=proc.stdout.read, daemon=True).start()
    return proc, found.group(0)


def _drive(page, url: str, shots: dict[str, bytes], privacy: Privacy) -> None:
    def take(name: str, height: int = VIEWPORT["height"]) -> None:
        page.set_viewport_size({"width": VIEWPORT["width"], "height": height})
        privacy.scrub(page)
        privacy.check(page, name)
        shots[name] = page.screenshot()

    def button(name: str, exact: bool = True):
        return page.get_by_role("button", name=name, exact=exact).first

    def step(label: str) -> None:
        button(f"{label} (", exact=False).click()

    def field(label: str):
        return page.get_by_label(label, exact=True).first

    def see(text: str) -> None:
        page.get_by_text(text, exact=False).first.wait_for(timeout=WAIT_MS)

    page.goto(url)
    see("Projects folder")
    button("New Project").wait_for(timeout=WAIT_MS)
    take("1-home")

    button("New Project").click()
    field("Project name").fill(PROJECT_NAME)
    field("Reviewer name").fill(REVIEWER)
    button("No").click()
    see("Folder name")
    take("2-new-project")
    button("Create Project").click()
    see("created with Source mode")

    page.locator(".bk-spin-wrapper input:visible").nth(1).wait_for()  # the form is drawn
    entry = page.get_by_placeholder("parts that must all appear", exact=False).first
    entry.fill("baseball + pose estimation + pitcher")
    button("Add to: Compound terms (button)").click()
    page.locator(".choices__list--multiple .choices__item").first.wait_for()  # the chip is made
    for n, year in enumerate(("2015", "2026")):
        years = page.locator(".bk-spin-wrapper input:visible").nth(n)
        years.fill(year)
        years.press("Enter")  # Bokeh's spinner commits a typed number on Enter or blur
    button("Save query").click()
    see("Query saved")
    take("3-query")

    step("Criteria")
    button("Save Criteria").click()
    see("saved")

    step("Size check and capture")
    button("Check the size of the OpenAlex search").click()
    see("Records")
    take("4-size-check")
    button("Continue to capture").click()
    page.locator("input[type=checkbox]:visible").check()  # its label is not linked (#383)
    button("Start the capture").click()
    see("Sealed. The capture is finished")
    take("5-capture", 900)

    step("Build")
    button("Build the store").click()
    see("The store holds")

    step("Screening")
    button("Start screening").click()
    for done in (1, 2):  # two Decisions, so the Flow counts have something to count
        button("include (i)").click()
        see(f"{done} / {len(_TITLES)} decided")
    take("6-screener", 1000)

    step("Flow counts")
    button("Refresh the Flow counts").click()
    see("Records identified")
    take("7-flow")

    step("Export")
    button("Export the citable bundle").click()
    see("The bundle is in")
    take("8-export")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, required=True, help="where to write the PNGs (outside the repository)"
    )
    out = parser.parse_args().out.resolve()
    if out == REPO or REPO in out.parents:
        raise SystemExit("FAIL: --out must be outside the repository.")

    from playwright.sync_api import sync_playwright

    throwaway = _throwaway_root()
    settings = throwaway / "user.env"  # inside the folder, so the path scrub covers it too
    settings.write_text("PRISMABIB_FIRST_RUN_SEEN=1\n", encoding="utf-8")
    proc = None
    shots: dict[str, bytes] = {}
    try:
        with sync_playwright() as pw:
            browser = _launch(pw)  # before the app: a missing browser stops here
            proc, url = _start_app(throwaway, settings)
            page = browser.new_page(viewport=VIEWPORT)
            _drive(page, url, shots, Privacy(throwaway))
            browser.close()
        out.mkdir(parents=True, exist_ok=True)
        for name, png in shots.items():
            (out / f"{name}.png").write_bytes(png)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(throwaway, ignore_errors=True)
    print(f"wrote {len(shots)} images")
    return 0


if __name__ == "__main__":
    if STUB_FLAG in sys.argv:
        _stub_app()
    else:
        sys.exit(main())
