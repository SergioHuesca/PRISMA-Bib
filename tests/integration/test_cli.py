"""Integration tests for ``src/prismabib/cli.py`` (BUILD_PLAN Stage 11, line 1455).

Real filesystem (``tmp_path``), real DuckDB, mocked network (``respx``) -- the
integration mix of §3.7.2. Every test drives the CLI as a user does, through
the same ``typer`` app the ``prismabib`` console script points at, over real
projects built by the shared helpers.

Nothing here monkeypatches a ``prismabib.*`` symbol (§3.7.3 rule 1): the only
doubles are the HTTP transport and an environment variable.

``test_cli__known_error__exits_nonzero_without_a_traceback`` runs a real
subprocess rather than ``CliRunner``. That is deliberate and not redundant:
``CliRunner`` catches exceptions itself and never prints a traceback, so a
traceback regression is precisely the thing it cannot see. Only a real process
with a real stderr can assert "no traceback".
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx
import typer
from typer.testing import CliRunner

from prismabib.cli import app
from prismabib.project import Project
from prismabib.store.load import build_store
from tests.prisma_helpers import (
    CorpusSpec,
    RecordSpec,
    build_project,
    copy_reference_project_with_criteria,
    reference_golden,
    screen_reference_project,
)

runner = CliRunner()

_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"


def _screened_reference(tmp_path: Path) -> Project:
    """A reference-fixture copy whose store is built and whose screening is logged."""
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)
    return project


# ---------------------------------------------------------------------------
# Surface -- BUILD_PLAN Stage 11 Tests table
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("command", ["init", "search", "build", "flow"])
def test_cli__every_subcommand__has_help_and_exits_zero(command: str) -> None:
    """``--help`` must work for every subcommand, and each must accept ``--root``.

    The option is asserted against the click command's declared parameters
    rather than against the rendered help text. Typer renders help through
    rich, whose output depends on terminal width, colour support and rich's
    own version -- this assertion was ``"--root" in result.stdout`` and
    passed on every local configuration I could construct (narrow columns,
    ``TERM=dumb``, ``NO_COLOR``, xdist, Python 3.11) while failing on CI.
    That is presentation, not contract: what a caller depends on is that the
    option exists and is spelled ``--root``, which is exactly what this now
    checks.
    """
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output

    subcommand = typer.main.get_command(app).commands[command]  # type: ignore[attr-defined]
    option_spellings = {name for param in subcommand.params for name in param.opts}

    assert "--root" in option_spellings


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_init__fresh_slug__creates_the_section_2_3_skeleton(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "--title", "Demo review", "--root", str(tmp_path)])

    root = tmp_path / "demo"
    assert result.exit_code == 0
    assert sorted(path.name for path in root.iterdir()) == [
        "criteria.yaml",
        "decisions",
        "exports",
        "fulltext",
        "project.toml",
        "raw",
        "store",
        "taxonomy",
    ]


@pytest.mark.integration
def test_cli_init__fresh_project__names_both_files_to_edit_in_order(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "--root", str(tmp_path)])

    root = tmp_path / "demo"
    lines = [line for line in result.stdout.splitlines() if line.startswith("  1. ")]
    lines += [line for line in result.stdout.splitlines() if line.startswith("  2. ")]

    assert result.exit_code == 0
    assert lines == [f"  1. {root / 'project.toml'}", f"  2. {root / 'criteria.yaml'}"]


@pytest.mark.integration
def test_cli_init__fresh_project__names_the_next_commands(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "demo", "--root", str(tmp_path)])

    assert "prismabib search demo" in result.stdout
    assert "prismabib build demo" in result.stdout
    assert "prismabib flow demo" in result.stdout


@pytest.mark.integration
def test_cli_init__existing_project__reports_reuse_and_leaves_edits_untouched(
    tmp_path: Path,
) -> None:
    runner.invoke(app, ["init", "demo", "--root", str(tmp_path)])
    edited = tmp_path / "demo" / "criteria.yaml"
    original = edited.read_text(encoding="utf-8")
    edited.write_text(original.replace("languages: []", 'languages: ["English"]'), encoding="utf-8")

    result = runner.invoke(app, ["init", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout.startswith("Reused project 'demo'")
    assert 'languages: ["English"]' in edited.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_build__sealed_layer0__builds_the_store_and_reports_counts(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path)

    result = runner.invoke(app, ["build", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert project.db_path.is_file()
    assert "  records                     120" in result.stdout
    assert "duplicate DOI groups: 1" in result.stdout


@pytest.mark.integration
def test_cli_build__store_already_present__says_nothing_was_loaded(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)

    result = runner.invoke(app, ["build", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout.startswith("Reused existing store")
    assert "--rebuild" in result.stdout


def _break_reference_entries(project: Project, *, every_page: bool) -> Path:
    """Delete ``dc:title`` from the copied fixture's Layer 0 entries.

    Helper, not a test. Nothing in prismabib writes a malformed entry, and
    §3.7.3 rule 1 forbids monkeypatching the loader to pretend one exists, so
    the only way to produce one is to edit the copied Layer 0 pages.

    Args:
        project: A copied reference project (its ``raw/`` is writable).
        every_page: Break every entry of every page (the fixture has five, so
            editing ``page-0000.jsonl`` alone breaks 25 of 120, not all of
            them) rather than only line 2 of ``page-0000.jsonl``.

    Returns:
        The run directory that was modified.
    """
    run_dir = next(d for d in project.raw_dir.iterdir() if (d / "manifest.json").is_file())
    pages = sorted(run_dir.glob("page-*.jsonl")) if every_page else [run_dir / "page-0000.jsonl"]
    for page in pages:
        text = page.read_text(encoding="utf-8").splitlines()
        for index in range(len(text)) if every_page else [2]:
            entry = json.loads(text[index])
            del entry["dc:title"]
            text[index] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        page.write_text("".join(f"{line}\n" for line in text), encoding="utf-8")
    return run_dir


@pytest.mark.integration
@pytest.mark.parametrize("extra_args", [[], ["--rebuild"]], ids=["reuse", "rebuild"])
def test_cli_build__skipped_entry__is_rendered_in_the_summary(
    tmp_path: Path, extra_args: list[str]
) -> None:
    """The summary must say a record was skipped, on both paths.

    It was reported only through a structlog warning that scrolls past above
    this summary, while ``unmapped_country_values`` -- which loses no record
    at all -- got a rendered line. Parametrised over ``--rebuild`` because the
    reuse path is the default one and used to report a clean load outright.
    """
    project = copy_reference_project_with_criteria(tmp_path)
    run_dir = _break_reference_entries(project, every_page=False)
    build_store(project, rebuild=True)

    result = runner.invoke(app, ["build", project.slug, "--root", str(tmp_path), *extra_args])

    assert result.exit_code == 0, result.output
    assert "  records                     119" in result.stdout
    assert "1 Layer 0 entry/entries could not be parsed into a record" in result.stdout
    assert f"{run_dir.name}/page-0000.jsonl:2" in result.stdout


@pytest.mark.integration
def test_cli_build__wholly_broken_capture__exits_nonzero_without_a_next_step(
    tmp_path: Path,
) -> None:
    """A capture nothing could be loaded from must not exit 0 reporting success.

    With ``dc:title`` stripped from all 120 entries this printed ``records 0``
    followed by ``Next: prismabib flow reference`` and exited ``0``. An
    operator following that hint screens an empty corpus and reports its
    numbers.
    """
    project = copy_reference_project_with_criteria(tmp_path)
    _break_reference_entries(project, every_page=True)

    result = runner.invoke(app, ["build", project.slug, "--root", str(tmp_path), "--rebuild"])

    assert result.exit_code == 1
    assert "120 of the 120 Layer 0 entries" in result.stderr
    assert "Next:" not in result.stdout
    assert not project.db_path.exists()


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_flow__screened_project__prints_the_counts_compute_flow_counts_derived(
    tmp_path: Path,
) -> None:
    project = _screened_reference(tmp_path)
    golden = reference_golden()

    result = runner.invoke(app, ["flow", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert f"{golden.identified:,}" in result.stdout
    assert f"-{golden.excluded_automated:,}" in result.stdout
    assert "reason INACCESSIBLE" in result.stdout
    assert f"{golden.excluded_fulltext['NOT_PRIMARY_RESEARCH']:,}" in result.stdout
    assert f"{golden.included:,}" in result.stdout


@pytest.mark.integration
def test_cli_flow__screened_project__reports_both_unsure_buckets(tmp_path: Path) -> None:
    project = _screened_reference(tmp_path)
    golden = reference_golden()

    result = runner.invoke(app, ["flow", project.slug, "--root", str(tmp_path)])

    unsure_lines = [
        line for line in result.stdout.splitlines() if "unsure or not yet screened" in line
    ]
    assert [line.split()[-1] for line in unsure_lines] == [
        f"{golden.unsure_title_abstract:,}",
        f"{golden.unsure_fulltext:,}",
    ]


@pytest.mark.integration
def test_cli_flow__consistent_counts__emits_no_warning(tmp_path: Path) -> None:
    project = _screened_reference(tmp_path)

    result = runner.invoke(app, ["flow", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "WARNING" not in result.stderr


@pytest.mark.integration
def test_cli_flow__counts_that_do_not_close__warns_and_still_prints_them(tmp_path: Path) -> None:
    # `identified` comes from the run manifest's server-reported total, while
    # `after_automated` is counted from Layer 1 -- a capture that holds fewer
    # records than Scopus reported (interrupted, or built before it finished) is
    # exactly how equation 1 legitimately fails in the field.
    build_project(
        tmp_path,
        CorpusSpec(records=[RecordSpec(number=1)], total_results=999),
        slug="partial",
    )

    result = runner.invoke(app, ["flow", "partial", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "records identified" in result.stdout
    assert "WARNING: these counts do not close into a consistent diagram." in result.stderr
    assert (
        "identified - duplicates_across_searches - removed_other_reasons - excluded_automated == after_automated"
        in result.stderr
    )


# ---------------------------------------------------------------------------
# fulltext -- Stage 6 / ADR 0019 (item 15: `prismabib fulltext` had no
# integration test at all, and `_print_fulltext_summary` was never invoked
# by one).
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cli_fulltext__manual_drop__resolves_and_reports_a_sealed_layer0_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.fulltext.resolve import manual_drop_path
    from prismabib.prisma.engine import manual_abstract_set

    # Force the chain to degrade to ManualDropResolver alone, regardless of
    # what a developer's own local `.env` happens to carry (`Settings()`
    # reads it, and this repository's working copy does) -- an explicit,
    # empty-string override takes precedence over the `.env` file, and (per
    # the BLOCKING fix for item 3) an empty value is now treated the same as
    # an absent one.
    monkeypatch.setenv("ELSEVIER_SD_API_KEY", "")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "")

    project = _screened_reference(tmp_path)
    (record_id,) = sorted(manual_abstract_set(project))[:1]
    drop_path = manual_drop_path(project.fulltext_dir, record_id)
    drop_path.parent.mkdir(parents=True, exist_ok=True)
    drop_path.write_bytes(b"%PDF-1.4\n%%EOF")

    # No ELSEVIER_SD_API_KEY/UNPAYWALL_EMAIL in the test environment: the chain
    # degrades to ManualDropResolver alone (see `default_chain`'s docstring),
    # so this needs no network mock at all.
    result = runner.invoke(app, ["fulltext", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    # `resolve_fulltext` catches `Exception` so one record's failure costs one
    # record -- which also swallows `pytest_socket.SocketBlockedError`, a
    # `RuntimeError`. Without this assertion a test that reached the network
    # would still pass, recording the block as a per-record failure and leaving
    # "no live API calls" to whoever happens to read stderr.
    assert "unexpected error mid-chain" not in result.stdout
    assert f"Resolved full text for {project.slug}" in result.stdout
    assert "records attempted" in result.stdout
    assert "resolved, by resolver:" in result.stdout
    assert "manual" in result.stdout
    assert "Run sealed. Re-run `prismabib build" in result.stdout

    # Layer 0 was written; Layer 1 was not touched (ADR 0019 Decision 0).
    (run_dir,) = [entry for entry in (project.fulltext_dir / "runs").iterdir() if entry.is_dir()]
    assert (run_dir / "manifest.json").is_file()


@pytest.mark.integration
def test_cli_fulltext__budget_of_zero_records__reports_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.fulltext.resolve import manual_drop_path
    from prismabib.prisma.engine import manual_abstract_set

    monkeypatch.setenv("ELSEVIER_SD_API_KEY", "")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "")

    project = _screened_reference(tmp_path)
    for record_id in sorted(manual_abstract_set(project))[:2]:
        drop_path = manual_drop_path(project.fulltext_dir, record_id)
        drop_path.parent.mkdir(parents=True, exist_ok=True)
        drop_path.write_bytes(b"%PDF-1.4\n%%EOF")

    result = runner.invoke(
        app, ["fulltext", project.slug, "--root", str(tmp_path), "--budget", "1"]
    )

    assert result.exit_code == 0
    # `resolve_fulltext` catches `Exception` so one record's failure costs one
    # record -- which also swallows `pytest_socket.SocketBlockedError`, a
    # `RuntimeError`. Without this assertion a test that reached the network
    # would still pass, recording the block as a per-record failure and leaving
    # "no live API calls" to whoever happens to read stderr.
    assert "unexpected error mid-chain" not in result.stdout
    assert re.search(r"records attempted\s+1\b", result.stdout)
    assert "Run is UNSEALED" in result.stdout


@pytest.mark.integration
def test_cli_fulltext__no_eligible_records__fails_with_the_librarys_own_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELSEVIER_SD_API_KEY", "")
    monkeypatch.setenv("UNPAYWALL_EMAIL", "")
    build_project(
        tmp_path,
        CorpusSpec(records=[RecordSpec(number=1)]),
        slug="no-fulltext-targets",
    )

    result = runner.invoke(app, ["fulltext", "no-fulltext-targets", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "prismabib: ValidationError" in result.stderr
    assert "No records to resolve full text for" in result.stderr


# ---------------------------------------------------------------------------
# Known errors -- requirement 1
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("command", ["search", "build", "flow"])
def test_cli__missing_project__fails_with_the_libraries_own_message(
    command: str, tmp_path: Path
) -> None:
    result = runner.invoke(app, [command, "nope", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "prismabib: ConfigError" in result.stderr
    assert (
        f"No prismabib project named 'nope' found: expected a directory at {tmp_path / 'nope'}"
        in result.stderr
    )


@pytest.mark.integration
def test_cli__unparseable_criteria__reports_the_multi_line_message_in_full(
    tmp_path: Path,
) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    (project.root / "criteria.yaml").write_text(
        (project.root / "criteria.yaml")
        .read_text(encoding="utf-8")
        .replace("languages:", "language:"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["flow", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "language is not a criteria.yaml key" in result.stderr
    assert "did you mean 'languages'?" in result.stderr
    assert "record it in your protocol" in result.stderr


@pytest.mark.integration
def test_cli__known_error__exits_nonzero_without_a_traceback(tmp_path: Path) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "prismabib.cli", "flow", "nope", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Traceback (most recent call last)" not in completed.stderr
    assert completed.stderr.splitlines()[0] == "prismabib: ConfigError"


# ---------------------------------------------------------------------------
# search -- requirement 4
# ---------------------------------------------------------------------------


def _page(*, entry_ids: list[str], current: str, next_cursor: str | None) -> dict[str, object]:
    """One Scopus search response page, shaped as the API returns it."""
    return {
        "search-results": {
            "opensearch:totalResults": "3",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{eid}"} for eid in entry_ids],
            "cursor": {"@current": current, "@next": next_cursor or ""},
        }
    }


def _searchable_project(tmp_path: Path) -> Project:
    """A project whose ``[query]`` table has something to search for."""
    project = Project.init("demo", title="Demo review", root=tmp_path)
    (project.root / "project.toml").write_text(
        "[project]\n"
        'slug = "demo"\n'
        'title = "Demo review"\n'
        "created = 2026-01-15\n"
        "track_decisions = true\n"
        "\n"
        "[query]\n"
        'terms = ["video anomaly detection"]\n'
        "compound_terms = []\n"
        'fields = ["TITLE-ABS-KEY"]\n',
        encoding="utf-8",
    )
    return project


@pytest.mark.integration
def test_cli_search__running__reports_progress_per_page_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _searchable_project(tmp_path)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(
                200, json=_page(entry_ids=["1", "2", "3"], current="*", next_cursor=None)
            )
        )
        result = runner.invoke(app, ["search", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "page 1 written -- 3 of 3 records captured, cursor saved" in result.stderr


@pytest.mark.integration
def test_cli_search__before_it_starts__says_it_is_resumable_and_costs_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _searchable_project(tmp_path)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(
                200, json=_page(entry_ids=["1"], current="*", next_cursor=None)
            )
        )
        result = runner.invoke(app, ["search", project.slug, "--root", str(tmp_path)])

    assert "quota" in result.stderr
    assert "Ctrl-C loses nothing" in result.stderr
    assert f"re-run `prismabib search {project.slug}`" in result.stderr


@pytest.mark.integration
def test_cli_search__completed_run__prints_the_sealed_manifest_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _searchable_project(tmp_path)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(
                200, json=_page(entry_ids=["1", "2", "3"], current="*", next_cursor=None)
            )
        )
        result = runner.invoke(app, ["search", project.slug, "--root", str(tmp_path)])

    run_id = next(path.name for path in project.raw_dir.iterdir() if not path.name.startswith("_"))
    manifest = (project.raw_dir / run_id / "manifest.json").read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert f"Run {run_id} sealed" in result.stdout
    assert "total_results  3" in result.stdout
    assert f"Next: prismabib build {project.slug}" in result.stdout
    assert '"client_version"' in manifest


@pytest.mark.integration
def test_cli_search__interrupted__reports_that_the_run_resumes_where_it_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _searchable_project(tmp_path)

    def interrupt(request: httpx.Request) -> httpx.Response:
        raise KeyboardInterrupt

    with respx.mock:
        # Raised from the transport, which is a permitted double (§3.7.3 rule 1) and
        # is where a Ctrl-C during a long capture actually lands: in the request.
        respx.get(_SEARCH_URL).mock(side_effect=interrupt)
        result = runner.invoke(app, ["search", project.slug, "--root", str(tmp_path)])

    assert result.exit_code == 130
    assert "Interrupted." in result.stderr
    assert f"re-run `prismabib search {project.slug}` to continue from there" in result.stderr


@pytest.mark.integration
def test_cli_search__empty_query_table__refuses_rather_than_searching_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    Project.init("demo", title="Demo review", root=tmp_path)

    result = runner.invoke(app, ["search", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "Traceback" not in result.stderr


@pytest.mark.integration
def test_cli_init__no_scopus_credentials__still_creates_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first command in the README must not demand an API key.

    ``prismabib init`` creates a directory and writes two template files. It
    makes no network call, and a researcher will reasonably want to lay a
    project out before going to their library to request Scopus access.
    Refusing at step one reads like the tool is broken, and it inverts the
    order people actually work in.

    This is pinned separately from the ``Project.init`` test because the CLI
    resolved the projects root through its own ``Settings()`` call, so the
    library fix alone left the command still failing -- which is exactly how
    it was found: by walking the README rather than reading it.
    """
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["init", "my-review", "--title", "My systematic review"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "projects" / "my-review" / "criteria.yaml").is_file()
