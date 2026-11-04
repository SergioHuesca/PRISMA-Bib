"""CLI tests for ``prismabib openalex-search`` (ADR 0042 Decision 4, Verification 31 to 34).

The command runs the real capture function; only the HTTP transport is a fake, so a request count
is the assertion for "no request". The key comes from the environment (a dummy sentinel). Help
text is never asserted on: Rich colours it in CI.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import typer.main
from typer.testing import CliRunner

from prismabib.capture import openalex_search_commands
from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, is_sealed
from prismabib.cli import app
from prismabib.project import Project
from prismabib.sources import scopus
from prismabib.sources.ratelimit import RateLimiter
from tests.integration.capture.test_openalex_search import (
    _DAILY_BODY,
    _DAILY_HEADERS,
    Fake,
    three_pages,
)

pytestmark = pytest.mark.integration

_KEY = "oa-sentinel-key-not-real"  # pragma: allowlist secret
_SEARCH = "[openalex_query]\nsearch = '\"carbon capture\" AND policy'\n"
runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_redaction_registry() -> Iterator[None]:
    """Older tests register one-character secrets (e.g. "x") that are process-global and never
    removed; they would redact the letter "x" in the log text asserted on here."""
    saved = set(scopus._REDACTED_SECRETS)  # pyright: ignore[reportPrivateUsage]
    scopus._REDACTED_SECRETS.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    scopus._REDACTED_SECRETS.clear()  # pyright: ignore[reportPrivateUsage]
    scopus._REDACTED_SECRETS.update(saved)  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Fake]:
    monkeypatch.chdir(tmp_path)  # no real .env
    monkeypatch.setenv("OPENALEX_API_KEY", _KEY)
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)
    served = three_pages()
    monkeypatch.setattr(
        openalex_search_commands,
        "capture_openalex_search",
        functools.partial(
            openalex_search_commands.capture_openalex_search,
            transport=served.transport,
            rate_limiter=RateLimiter(rate=1e6),
            sleep=lambda _s: None,
        ),
    )
    yield served


def make_project(tmp_path: Path, mode: str = "openalex", *, query: bool = True) -> Project:
    project = Project.init("demo", title="Demo", root=tmp_path, source_mode=mode, actor="t")  # type: ignore[arg-type]
    path = project.root / "project.toml"
    base = path.read_text(encoding="utf-8").split("\n# The OpenAlex search")[0]  # drop the scaffold
    path.write_text(base + ("\n" + _SEARCH if query else ""), encoding="utf-8")
    return project


def invoke(tmp_path: Path, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["openalex-search", "demo", "--root", str(tmp_path), *args])
    return result.exit_code, result.output


def run_dirs(project: Project) -> list[Path]:
    root = project.raw_dir / OPENALEX_SEARCH_DIRNAME
    return sorted(root.iterdir()) if root.is_dir() else []


def test_command__declared_params__budget_required_no_opt_in() -> None:
    command = typer.main.get_command(app).commands["openalex-search"]  # type: ignore[attr-defined]
    params = {p.name: p for p in command.params}

    assert set(params) == {"slug", "budget", "refresh", "dry_run", "root"}  # no opt_in
    assert params["budget"].required is True
    assert params["budget"].default is None
    assert params["refresh"].default is False
    assert params["dry_run"].default is False


def test_command__budget_missing_or_zero__usage_error_and_no_request(
    tmp_path: Path, fake: Fake
) -> None:
    make_project(tmp_path)

    assert invoke(tmp_path)[0] == 2
    assert invoke(tmp_path, "--budget", "0")[0] == 2
    assert fake.requests == []


def test_command__opt_in_flag__is_not_accepted(tmp_path: Path, fake: Fake) -> None:
    make_project(tmp_path)

    code, _ = invoke(tmp_path, "--budget", "5", "--opt-in")

    assert code == 2
    assert fake.requests == []


def test_command__success__seals_and_says_build_rebuild_and_builds_nothing(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 0, out
    (run_dir,) = run_dirs(project)
    assert is_sealed(run_dir)
    assert f"run {run_dir.name} sealed: 5 works captured of 5 reported by OpenAlex" in out
    assert "3 page(s) this call, spent $0.003" in out
    assert out.strip().splitlines()[-1] == "Next: prismabib build demo --rebuild"
    assert not project.db_path.exists()  # the command never builds the store
    assert len(fake.requests) == 3


def test_command__query_sent_is_the_toml_search_string_and_select_is_not_in_it(
    tmp_path: Path, fake: Fake
) -> None:
    make_project(tmp_path)

    invoke(tmp_path, "--budget", "10")

    assert fake.requests[0].url.params["search"] == '"carbon capture" AND policy'
    assert "select" in fake.requests[0].url.params  # a separate parameter, never part of search


def test_command__budget_stop_then_resume__no_page_is_asked_twice(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)

    code, out = invoke(tmp_path, "--budget", "1")

    assert code == 0, out
    assert "page budget of 1 is used" in out
    assert "not sealed" in out
    assert "Run the same command again to resume" in out
    assert "Next:" not in out
    (run_dir,) = run_dirs(project)
    assert not is_sealed(run_dir)
    assert len(fake.requests) == 1

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 0, out
    assert "sealed: 5 works captured of 5" in out
    assert [r.url.params["cursor"] for r in fake.requests] == ["*", "c1", "c2"]
    assert run_dirs(project) == [run_dir]


def test_command__daily_429__prints_when_it_resets_exits_zero_and_resumes_next_day(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)
    invoke(tmp_path, "--budget", "1")
    leaked = dict(_DAILY_BODY, message=f"Insufficient budget for {_KEY}. Resets at midnight UTC.")
    fake.override = lambda _r: httpx.Response(429, json=leaked, headers=_DAILY_HEADERS)

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 0, out
    assert "daily budget is spent" in out
    assert "It resets in 9 h 32 min (00:00 UTC)" in out  # 34265 s, rounded up
    assert "not sealed" in out
    assert _KEY not in out
    (run_dir,) = run_dirs(project)
    assert not is_sealed(run_dir)

    fake.override = None
    code, out = invoke(tmp_path, "--budget", "10")
    assert code == 0, out
    assert "sealed: 5 works captured of 5" in out


def test_command__discarded_run__exits_one_with_discard_and_restart_hint(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)
    invoke(tmp_path, "--budget", "1")
    fake.count = 6  # the corpus moved while the run waited

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 1
    assert "discard and restart" in out
    assert "never resumed" in out
    assert "Next:" not in out
    (run_dir,) = run_dirs(project)
    assert not is_sealed(run_dir)

    fake.count = 5
    code, out = invoke(tmp_path, "--budget", "10")  # a new run, as the message says
    assert code == 0, out
    assert len(run_dirs(project)) == 2


def test_command__already_sealed__makes_no_request_and_points_to_refresh(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)
    invoke(tmp_path, "--budget", "10")
    fake.requests.clear()

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 0, out
    assert "already exists" in out
    assert "--refresh" in out
    assert "Next:" not in out
    assert fake.requests == []
    assert len(run_dirs(project)) == 1


def test_command__refresh__makes_a_second_run(tmp_path: Path, fake: Fake) -> None:
    project = make_project(tmp_path)
    invoke(tmp_path, "--budget", "10")

    code, out = invoke(tmp_path, "--budget", "10", "--refresh")

    assert code == 0, out
    assert "sealed: 5 works captured of 5" in out
    assert len(run_dirs(project)) == 2
    assert len(fake.requests) == 6


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_command__other_modes__refused_before_the_key_or_any_request(
    tmp_path: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    project = make_project(tmp_path, mode)
    monkeypatch.delenv("OPENALEX_API_KEY")  # a key check first would name the key instead

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 1
    assert "`openalex-search` is not available in sources mode" in out
    assert "OPENALEX_API_KEY" not in out
    assert fake.requests == []
    assert run_dirs(project) == []


def test_command__no_key__named_error_before_any_request(
    tmp_path: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    monkeypatch.delenv("OPENALEX_API_KEY")

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 1
    assert "OPENALEX_API_KEY is not set" in out
    assert "credentials set openalex" in out
    assert fake.requests == []
    assert run_dirs(project) == []


@pytest.mark.parametrize(
    ("toml", "needle"),
    [
        (None, "no [openalex_query] table"),
        ("[openalex_query]\nsearch = '   '\n", "empty"),
        ("[openalex_query]\nsearch = '(a OR b)'\n", "parentheses"),
        ("[openalex_query]\nsearch = 'a'\nfilter = 'x'\n", "'filter'"),
    ],
)
def test_command__bad_query_table__named_error_before_any_request(
    tmp_path: Path, fake: Fake, toml: str | None, needle: str
) -> None:
    project = make_project(tmp_path, query=False)
    if toml:
        with (project.root / "project.toml").open("a", encoding="utf-8") as handle:
            handle.write("\n" + toml)

    code, out = invoke(tmp_path, "--budget", "10")

    assert code == 1
    assert needle in out
    assert fake.requests == []


def test_command__dry_run__prints_plan_and_cost_bound_and_does_nothing(
    tmp_path: Path, fake: Fake
) -> None:
    project = make_project(tmp_path)
    before = sorted(project.root.rglob("*"))  # directories too

    code, out = invoke(tmp_path, "--budget", "100", "--dry-run")

    assert code == 0, out
    assert 'query: "carbon capture" AND policy' in out
    assert 'query key: "carbon capture" AND policy' in out
    assert "page budget: 100 pages of 100 works" in out
    assert "cost bound: at most $0.100" in out
    assert "select: " in out
    assert "OpenAlex key: set" in out
    assert fake.requests == []
    assert run_dirs(project) == []
    assert sorted(project.root.rglob("*")) == before
    assert not (project.raw_dir / OPENALEX_SEARCH_DIRNAME).exists()


def test_command__dry_run_without_key__says_so_and_still_makes_no_request(
    tmp_path: Path, fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(tmp_path)
    monkeypatch.delenv("OPENALEX_API_KEY")

    code, out = invoke(tmp_path, "--budget", "5", "--dry-run")

    assert code == 0, out
    assert "NOT SET" in out
    assert fake.requests == []


def test_command__dry_run_in_other_mode__refused(tmp_path: Path, fake: Fake) -> None:
    make_project(tmp_path, "scopus")

    code, out = invoke(tmp_path, "--budget", "5", "--dry-run")

    assert code == 1
    assert "not available in sources mode" in out


def test_command__the_key_never_appears_in_output_or_on_disk(tmp_path: Path, fake: Fake) -> None:
    project = make_project(tmp_path)
    outputs = [
        invoke(tmp_path, "--budget", "1")[1],
        invoke(tmp_path, "--budget", "10")[1],
        invoke(tmp_path, "--budget", "10", "--dry-run")[1],
    ]

    assert all(_KEY not in out for out in outputs)
    for path in project.root.rglob("*"):
        if path.is_file():
            assert _KEY.encode() not in path.read_bytes(), path


@pytest.mark.parametrize(
    "toml", [None, "[openalex_query]\nsearch = '(a)'\n"], ids=["no-table", "bad-search"]
)
@pytest.mark.parametrize("flags", [[], ["--dry-run"]])
def test_command__other_mode_with_missing_or_bad_query_table__mode_refusal_wins(
    tmp_path: Path, fake: Fake, toml: str | None, flags: list[str]
) -> None:
    project = make_project(tmp_path, "scopus", query=False)
    if toml:
        with (project.root / "project.toml").open("a", encoding="utf-8") as handle:
            handle.write("\n" + toml)

    code, out = invoke(tmp_path, "--budget", "5", *flags)

    assert code == 1
    assert "not available in sources mode" in out
    assert "[openalex_query]" not in out
    assert fake.requests == []


def test_command__logs_go_to_stderr_and_stdout_holds_only_the_summary(
    tmp_path: Path, fake: Fake
) -> None:
    make_project(tmp_path)

    result = runner.invoke(
        app, ["openalex-search", "demo", "--root", str(tmp_path), "--budget", "10"]
    )

    assert result.exit_code == 0, result.output
    assert "openalex_search." in result.stderr  # the capture's log lines
    assert "openalex_search." not in result.stdout
    assert [ln.split(" ")[0] for ln in result.stdout.splitlines()] == ["run", "Next:"]


def test_logs_to_stderr__restores_logging_state_even_when_the_block_raises() -> None:
    import structlog

    before = structlog.get_config()
    factory, processors = before["logger_factory"], list(before["processors"])

    with pytest.raises(RuntimeError), openalex_search_commands._logs_to_stderr():
        assert structlog.get_config()["logger_factory"] is not factory  # really switched
        raise RuntimeError("boom")

    after = structlog.get_config()
    assert after["logger_factory"] is factory
    assert list(after["processors"]) == processors
