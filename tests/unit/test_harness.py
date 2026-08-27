"""Stage 0's named meta-tests (BUILD_PLAN.md lines 638-645).

Every later stage's suite inherits the guarantees these tests pin down:
that the socket ban actually bans sockets, that the clock is actually
frozen, that ids are actually deterministic, that the traceability plugin
actually catches an unclaimed criterion, and that the coverage gates in
``pyproject.toml`` have not quietly drifted from BUILD_PLAN.md §3.7.6.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from pytest_socket import SocketBlockedError

from tests.conftest import FROZEN_INSTANT, SeededIdFactory

REPO_ROOT = Path(__file__).parent.parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"


@pytest.mark.unit
def test_conftest__any_test__cannot_open_a_socket() -> None:
    url = "http://example.com"

    with pytest.raises(SocketBlockedError):
        httpx.get(url)


@pytest.mark.unit
def test_conftest__frozen_clock__now_is_fixed_instant(frozen_time: None) -> None:
    observed = datetime.now(UTC)

    assert observed == FROZEN_INSTANT


@pytest.mark.unit
def test_conftest__id_factory__is_monotonic_and_seeded() -> None:
    first_run = SeededIdFactory(seed=0, prefix="id")
    second_run = SeededIdFactory(seed=0, prefix="id")

    first_sequence = [first_run() for _ in range(5)]
    second_sequence = [second_run() for _ in range(5)]

    assert first_sequence == second_sequence
    assert first_sequence == sorted(first_sequence)


@pytest.mark.unit
def test_markers__acceptance_report__lists_unclaimed_criteria(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(
        f"""
        import sys
        sys.path.insert(0, {str(REPO_ROOT)!r})

        from tests import markers

        UNCLAIMED_CRITERION = "S99-ACX"

        def pytest_addoption(parser):
            markers.add_option(parser)

        def pytest_configure(config):
            markers.configure(
                config,
                criteria={{UNCLAIMED_CRITERION: "a criterion nothing in this sandbox claims"}},
            )

        def pytest_itemcollected(item):
            markers.item_collected(item)

        def pytest_sessionfinish(session):
            markers.session_finish(session)
        """
    )
    pytester.makepyfile(
        test_sandbox_suite="""
        def test_something_unrelated():
            assert True
        """
    )

    result = pytester.runpytest("--acceptance-report")

    assert result.ret != 0
    result.stdout.fnmatch_lines(["*UNCLAIMED*S99-ACX*"])


@pytest.mark.unit
def test_coverage_config__gates__match_the_build_plan() -> None:
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)

    gates = pyproject["tool"]["prismabib"]["coverage_gates"]

    # Transcribed as literals from BUILD_PLAN.md §3.7.6, independently of the
    # table under test, so a quiet edit to pyproject.toml cannot also edit
    # the expectation it is being checked against.
    expected_gates = {
        "global": {"line": 85},
        "src/prismabib/prisma/engine.py": {"line": 100, "branch": 100},
        "src/prismabib/prisma/log.py": {"line": 100, "branch": 100},
        "src/prismabib/prisma/flow.py": {"line": 100, "branch": 100},
        "src/prismabib/store": {"line": 95, "branch": 90},
        "src/prismabib/taxonomy": {"line": 95, "branch": 90},
        "src/prismabib/bibliometrics": {"line": 90},
        "src/prismabib/report": {"line": 90},
        "src/prismabib/sources": {"line": 85},
        "src/prismabib/fulltext": {"line": 85},
        "src/prismabib/viz": {"line": 60},
        "src/prismabib/screening/ui.py": {"line": 60},
    }

    assert gates == expected_gates


@pytest.mark.unit
@pytest.mark.acceptance("S00-AC4")
@pytest.mark.skipif(shutil.which("pre-commit") is None, reason="pre-commit is not installed")
def test_pre_commit__run_all_files__is_clean() -> None:
    result = subprocess.run(
        ["pre-commit", "run", "--all-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.unit
@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.skipif(not (REPO_ROOT / ".git").exists(), reason="not a git checkout")
@pytest.mark.parametrize(
    "path",
    [
        pytest.param(
            "tests/fixtures/projects/reference/raw/20260115T090000Z-fee5c0de/page-0000.jsonl",
            id="sealed-layer-0-payload",
        ),
        pytest.param(
            "tests/fixtures/projects/reference/raw/20260115T090000Z-fee5c0de/manifest.json",
            id="run-manifest",
        ),
        pytest.param("tests/fixtures/cassettes/standard-page-0000.json", id="recorded-cassette"),
        pytest.param("projects/example/decisions/decisions.jsonl", id="decision-log"),
        pytest.param(
            "projects/example/decisions/decisions.jsonl.sha256", id="decision-log-sidecar"
        ),
    ],
)
def test_gitattributes__checksummed_files__are_never_newline_normalised(path: str) -> None:
    """Files whose *bytes* are checksummed must be exempt from EOL conversion.

    Git for Windows installs with ``core.autocrlf=true``. Without
    ``.gitattributes`` saying otherwise, a Windows clone rewrites every
    ``\\n`` in these files to ``\\r\\n`` on checkout, and each one carries a
    SHA-256 over the bytes it is supposed to have: the reference fixture's
    pages under ``manifest.json``'s ``payload_sha256``, and
    ``decisions.jsonl`` under its own sidecar. The result is a checkout where
    the fixture fails to verify and a decision log reads as hand-edited, on a
    machine where nothing is actually wrong.

    Nothing else in the suite can see this: every CI job and every developer
    machine here is POSIX, where the attribute is inert. So it is asserted
    directly, against ``git`` itself rather than against a parse of the file,
    since what matters is the attribute git *resolves* for the path -- last
    matching pattern wins, and a later broad rule could quietly take it back.
    """
    result = subprocess.run(
        ["git", "check-attr", "text", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )

    assert result.stdout.strip() == f"{path}: text: unset"
