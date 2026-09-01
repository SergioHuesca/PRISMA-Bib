"""Unit tests for ``src/prismabib/cli.py`` (BUILD_PLAN Stage 11, line 1455).

These assert on rendered text, which §3.7.3 rule 8 otherwise discourages
("assert on domain objects, not on strings"). The exception it names --
"except in golden tests, where the rendering *is* the subject" -- is exactly
this module's situation: ``cli.py`` computes nothing, so its rendering is the
whole of its behaviour and there is no domain object downstream of it to
compare instead.

Nothing here monkeypatches a ``prismabib.*`` symbol (§3.7.3 rule 1). The
error-path tests raise real prismabib exceptions into the real handler, and
the progress-processor tests feed it real ``capture.*`` event dicts of the
shape ``capture/writer.py`` actually logs.
"""

from __future__ import annotations

import pytest
import structlog
import typer
from typer.testing import CliRunner

import prismabib
from prismabib.cli import _CaptureProgress, _print_flow, _reporting_errors, app
from prismabib.errors import ConfigError
from prismabib.prisma.flow import FlowCounts

runner = CliRunner()

#: The four subcommands Stage 11 line 1455 names *and* that have a tested
#: library function behind them today. `code` are named there too
#: and are deliberately not implemented (see the cli.py module docstring).
_IMPLEMENTED_COMMANDS = {"init", "search", "build", "flow", "enrich", "export", "fill"}


def _counts(**overrides: object) -> FlowCounts:
    """A consistent :class:`FlowCounts` for rendering tests."""
    fields: dict[str, object] = {
        "identified": 1771,
        "duplicates_across_searches": 0,
        "removed_other_reasons": 0,
        "excluded_automated": 412,
        "after_automated": 1359,
        "excluded_language": 27,
        "after_language": 1332,
        "excluded_title_abstract": 1000,
        "unsure_title_abstract": 32,
        "retrieved_fulltext": 300,
        "excluded_fulltext": {"NOT_PRIMARY_RESEARCH": 40, "INACCESSIBLE": 10},
        "unsure_fulltext": 7,
        "included": 243,
    }
    fields.update(overrides)
    return FlowCounts(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli__help__lists_every_implemented_command() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert set(typer.main.get_command(app).commands) == _IMPLEMENTED_COMMANDS  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize("unbuilt", ["code"])
def test_cli__unbuilt_stage_command__is_absent_rather_than_a_stub(unbuilt: str) -> None:
    """A command whose backing stage does not exist must not exist either.

    ``export`` left this list at Stage 10, which is the point of the test:
    a command graduates by being implemented, never by being stubbed. ``code``
    waits for the taxonomy engine (Stage 8) -- "No such command" is honest,
    while a stub that accepts arguments and does nothing is indistinguishable
    from a working one until a researcher trusts its output.
    """
    result = runner.invoke(app, [unbuilt, "demo"])

    assert result.exit_code != 0
    assert unbuilt not in set(typer.main.get_command(app).commands)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_cli__version_flag__reports_the_installed_version() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == f"prismabib {prismabib.__version__}"


# ---------------------------------------------------------------------------
# Error handling -- requirement 1: a known error never tracebacks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reporting_errors__known_error__prints_the_message_verbatim(
    capsys: pytest.CaptureFixture[str],
) -> None:
    message = (
        "criteria.yaml contains 1 key(s) prismabib does not understand:\n"
        "  - language is not a criteria.yaml key; did you mean 'languages'?\n"
        "    valid here: ['doc_types', 'languages']"
    )

    with pytest.raises(typer.Exit) as raised, _reporting_errors():
        raise ConfigError(message)

    captured = capsys.readouterr()
    assert raised.value.exit_code == 1
    assert message in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.unit
def test_reporting_errors__known_error__names_the_exception_type(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(typer.Exit), _reporting_errors():
        raise ConfigError("boom")

    assert capsys.readouterr().err == "prismabib: ConfigError\nboom\n"


@pytest.mark.unit
def test_reporting_errors__unexpected_exception__is_left_to_traceback() -> None:
    with pytest.raises(RuntimeError, match="not a prismabib error"), _reporting_errors():
        raise RuntimeError("not a prismabib error")


# ---------------------------------------------------------------------------
# search progress -- requirement 4: progress, and visibly resumable
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_capture_progress__page_written__renders_a_progress_line() -> None:
    lines: list[str] = []
    progress = _CaptureProgress(lines.append)

    with pytest.raises(structlog.DropEvent):
        progress(
            None,
            "info",
            {
                "event": "capture.page_written",
                "run_id": "20260115T090000Z-abcdef12",
                "page_index": 0,
                "result_count": 25,
                "total_results": 1771,
            },
        )

    assert lines == ["  page 1 written -- 25 of 1,771 records captured, cursor saved"]


@pytest.mark.unit
def test_capture_progress__successive_pages__accumulate_records_captured() -> None:
    lines: list[str] = []
    progress = _CaptureProgress(lines.append)
    page = {"event": "capture.page_written", "result_count": 25, "total_results": 1771}

    for index in range(3):
        with pytest.raises(structlog.DropEvent):
            progress(None, "info", {**page, "page_index": index})

    assert lines[-1] == "  page 3 written -- 75 of 1,771 records captured, cursor saved"


@pytest.mark.unit
def test_capture_progress__resumed_run__says_the_pages_already_held_cost_no_quota() -> None:
    lines: list[str] = []
    progress = _CaptureProgress(lines.append)

    with pytest.raises(structlog.DropEvent):
        progress(
            None,
            "info",
            {
                "event": "capture.run_resumed",
                "run_id": "20260115T090000Z-abcdef12",
                "pages_already_written": 40,
            },
        )

    assert lines == [
        (
            "Resuming run 20260115T090000Z-abcdef12: 40 page(s) already in Layer 0, "
            "continuing from the saved cursor -- those pages are not re-fetched and "
            "cost no quota."
        )
    ]


@pytest.mark.unit
def test_capture_progress__unrelated_event__passes_through_untouched() -> None:
    lines: list[str] = []
    progress = _CaptureProgress(lines.append)
    event = {"event": "scopus.quota", "remaining": 4999}

    assert progress(None, "info", dict(event)) == event
    assert lines == []


# ---------------------------------------------------------------------------
# flow rendering -- requirement 3: readable, with unsure and reason codes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_print_flow__counts__reports_every_unsure_bucket(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_flow(_counts(), slug="demo")

    out = capsys.readouterr().out
    assert "  unsure or not yet screened                           32" in out
    assert "  unsure or not yet screened                            7" in out
    assert "never resolves to inclusion" in out


@pytest.mark.unit
def test_print_flow__excluded_fulltext__breaks_down_by_reason_code_sorted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_flow(_counts(), slug="demo")

    out = capsys.readouterr().out
    reason_lines = [line for line in out.splitlines() if "reason " in line]
    assert [line.split()[1] for line in reason_lines] == [
        "INACCESSIBLE",
        "NOT_PRIMARY_RESEARCH",
    ]
    assert "reason INACCESSIBLE" in out
    assert "10" in reason_lines[0]


@pytest.mark.unit
def test_print_flow__no_fulltext_exclusions__says_so_instead_of_printing_an_empty_dict(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_flow(_counts(excluded_fulltext={}, unsure_fulltext=57), slug="demo")

    out = capsys.readouterr().out
    assert "(no full-text exclusion reason codes logged)" in out
    assert "{" not in out


@pytest.mark.unit
def test_print_flow__large_counts__are_thousands_separated_not_repr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _print_flow(_counts(), slug="demo")

    out = capsys.readouterr().out
    assert "1,771" in out
    assert "FlowCounts(" not in out
