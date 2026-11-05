"""Clean-machine proof: a peer with no Scopus access installs and runs the OpenAlex-only path.

Run as ``uv run python scripts/clean_install_scenario.py`` after a minimal install
(``uv sync --extra viz --extra notebook``). Stdlib and prismabib only, so it needs no dev
dependency. Everything happens in a temporary projects root and a temporary git repository, with
every credential variable removed; the only "network" is an in-process ``httpx.MockTransport``
serving the recorded page ``tests/fixtures/openalex_search/page-0.json``. Mirrors
``docs/getting-started.md`` and ``docs/how-to/build-an-openalex-query.md``.

Prints one PASS/FAIL line per step and exits 1 at the first failure, naming the step.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import SecretStr

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "tests" / "fixtures" / "openalex_search" / "page-0.json"
SLUG = "peer-review"
QUERY = '"baseball" AND "pitcher" AND "pose estimation"'
# Never printed and never a real key.
DUMMY_KEY = "oa-clean-install-dummy-value"  # pragma: allowlist secret
CREDENTIAL_PREFIXES = ("SCOPUS_", "OPENALEX_", "ELSEVIER_", "UNPAYWALL_")

_captured: list[str] = []


class StepError(Exception):
    """A step's expectation was not met."""


def scrub_environment() -> None:
    """Remove every credential variable and make child output stable and wide."""
    for name in [n for n in os.environ if n.upper().startswith(CREDENTIAL_PREFIXES)]:
        del os.environ[name]
    os.environ.update(PYTHONIOENCODING="utf-8", COLUMNS="200", NO_COLOR="1", TERM="dumb")
    os.environ.pop("FORCE_COLOR", None)


def cli(*args: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run the real CLI as a subprocess; return (exit code, stdout + stderr)."""
    # Children get an unroutable proxy (httpx trusts the environment), so an accidental real
    # request fails fast instead of reaching OpenAlex. Not applied to the in-process mock step.
    dead = "http://127.0.0.1:9"
    proxies = dict.fromkeys(("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"), dead)
    env = {**os.environ, **proxies, "NO_PROXY": "", "no_proxy": "", **(extra_env or {})}
    proc = subprocess.run(
        [sys.executable, "-m", "prismabib.cli", *args],
        capture_output=True,
        env=env,
        check=False,
        timeout=300,
    )
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    _captured.append(out)
    return proc.returncode, out


def expect(code: int, out: str, *, ok: bool, contains: tuple[str, ...] = ()) -> None:
    """Assert the exit status (0 or non-zero), no traceback, and every substring."""
    if (code == 0) != ok:
        raise StepError(f"exit code {code}, expected {'0' if ok else 'non-zero'}:\n{out}")
    if "Traceback (most recent call last)" in out:
        raise StepError(f"unexpected traceback:\n{out}")
    for text in contains:
        if text not in out:
            raise StepError(f"missing {text!r} in output:\n{out}")


def seal_offline(projects_root: Path) -> None:
    """Seal a Layer 0 run through the real writer, served by a mock transport."""
    from prismabib.capture.openalex_search import capture_openalex_search
    from prismabib.config import Settings
    from prismabib.project import Project
    from prismabib.query import build_openalex_query_for_project
    from prismabib.sources.ratelimit import RateLimiter

    body = FIXTURE.read_bytes()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=body, headers={"x-ratelimit-remaining": "9990"})

    project = Project.open(SLUG, root=projects_root)
    result = capture_openalex_search(
        project,
        query=build_openalex_query_for_project(project),
        budget=5,
        settings=Settings(openalex_api_key=SecretStr(DUMMY_KEY)),
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(rate=1_000_000.0),
        sleep=lambda _s: None,
    )
    if result.status != "sealed" or len(requests) != 1:
        raise StepError(f"status {result.status!r} after {len(requests)} request(s)")


def run_steps(root: Path) -> None:
    projects = root / "projects"
    repo = root / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=60)
    (repo / ".gitignore").write_text("*.env\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, timeout=60)
    os.chdir(repo)
    os.environ["PRISMABIB_PROJECTS_ROOT"] = str(projects)
    n = int(json.loads(FIXTURE.read_text(encoding="utf-8"))["meta"]["count"])
    project_dir = projects / SLUG
    refusal = "sources mode 'openalex'"

    def version() -> None:
        expect(*cli("--version"), ok=True, contains=("prismabib",))

    def doctor() -> None:
        expect(*cli("doctor"), ok=True, contains=("environment:",))

    def init() -> None:
        code, out = cli("init", SLUG, "--sources", "openalex", "--actor", "CI", "--title", "CI")
        expect(code, out, ok=True, contains=("Created project",))

    def edit_files() -> None:
        toml = project_dir / "project.toml"
        text, subs = re.subn(
            r'(?m)^search = ""$', f"search = '{QUERY}'", toml.read_text(encoding="utf-8")
        )
        if subs != 1:
            raise StepError("project.toml has no empty `search` to fill in")
        toml.write_text(text, encoding="utf-8")
        crit = project_dir / "criteria.yaml"
        text, subs = re.subn(
            r"(?m)^(\s*)year_start: \d+", r"\g<1>year_start: 1990", crit.read_text("utf-8")
        )
        if subs != 1:
            raise StepError("criteria.yaml has no year_start")
        crit.write_text(text, encoding="utf-8")

    def dry_run() -> None:
        expect(
            *cli("openalex-search", SLUG, "--budget", "5", "--dry-run"),
            ok=True,
            contains=("no request made", "baseball", "OpenAlex key: NOT SET"),
        )

    def refused() -> None:
        expect(
            *cli("openalex-search", SLUG, "--budget", "5"),
            ok=False,
            contains=("ConfigError", "OPENALEX_API_KEY is not set"),
        )
        raw = project_dir / "raw"
        if raw.is_dir() and any(raw.iterdir()):
            raise StepError("a refused run wrote to raw/")

    def seal() -> None:
        seal_offline(projects)

    def build() -> None:
        expect(
            *cli("build", SLUG, "--rebuild"), ok=True, contains=("records (OpenAlex works)", str(n))
        )

    def flow() -> None:
        code, out = cli("flow", SLUG)
        expect(code, out, ok=True)
        label = "records identified (OpenAlex meta.count)"
        line = next((ln for ln in out.splitlines() if label in ln), "")
        if line.split()[-1:] != [str(n)]:
            raise StepError(f"identified line {line!r}, expected count {n}:\n{out}")

    def export() -> None:
        expect(*cli("export", SLUG), ok=True)
        numbers = json.loads((project_dir / "exports" / "numbers.json").read_text("utf-8"))
        sentence = numbers.get("methods.source_sentence")
        if sentence is None:
            sentence = numbers.get("methods", {}).get("source_sentence")
        if not isinstance(sentence, str) or "OpenAlex" not in sentence:
            raise StepError(f"methods.source_sentence missing or without OpenAlex: {sentence!r}")

    def scopus_guard() -> None:
        for args in (
            ("search", SLUG),
            ("enrich", SLUG),
            ("cascade", SLUG, "--opt-in", "--scopus-budget", "1"),
        ):
            code, out = cli(*args)
            expect(code, out, ok=False, contains=("ConfigError", refusal))

    def credentials() -> None:
        expect(
            *cli(
                "credentials",
                "set",
                "openalex",
                "--from-env",
                extra_env={"OPENALEX_API_KEY": DUMMY_KEY},
            ),
            ok=True,
            contains=("OPENALEX_API_KEY",),
        )
        expect(*cli("credentials", "status"), ok=True, contains=("OPENALEX_API_KEY: set (.env)",))

    steps: list[tuple[str, Callable[[], None]]] = [
        ("a --version", version),
        ("b doctor", doctor),
        ("c init", init),
        ("d edit project.toml and criteria.yaml", edit_files),
        ("e openalex-search --dry-run", dry_run),
        ("f openalex-search refused without a key", refused),
        ("g seal a run offline", seal),
        ("h build --rebuild", build),
        ("i flow", flow),
        ("j export", export),
        ("k Scopus commands refused", scopus_guard),
        ("l credentials set/status", credentials),
    ]
    for name, step in steps:
        try:
            step()
        except (StepError, subprocess.SubprocessError, OSError) as exc:
            print(f"FAIL  {name}\n{exc}")
            raise SystemExit(1) from exc
        print(f"PASS  {name}")
    if any(DUMMY_KEY in text for text in _captured):
        print("FAIL  l the dummy key appeared in captured output")
        raise SystemExit(1)
    print("PASS  l the dummy key never appears in captured output")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):  # FAIL text may be non-ASCII on a Windows pipe
        sys.stdout.reconfigure(encoding="utf-8")
    scrub_environment()
    with tempfile.TemporaryDirectory(prefix="prismabib-clean-", ignore_cleanup_errors=True) as tmp:
        try:
            run_steps(Path(tmp))
        finally:
            os.chdir(REPO)  # Windows cannot delete the current directory
    print("clean-install scenario: all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
