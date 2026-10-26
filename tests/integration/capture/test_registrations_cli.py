"""CLI tests for ``register`` / ``reverse`` / ``registrations`` (thin wrappers over the writer).

Reuses the synthetic OpenAlex run and ``env`` fixture of ``test_registrations.py``; no network.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from prismabib.capture.registrations import load_registration_events
from prismabib.cli import app
from tests.integration.capture.test_registrations import (
    DOI_ONE,
    PAYLOAD,
    TITLE_ONE,
    Env,
    env,  # noqa: F401  (pytest fixture)
)

runner = CliRunner()


def _run(env: Env, *args: str) -> tuple[int, str]:  # noqa: F811
    result = runner.invoke(app, [*args, "--root", str(env.project.root.parent)])
    return result.exit_code, result.output


def _register(env: Env, cid: str, *extra: str) -> tuple[int, str]:  # noqa: F811
    return _run(env, "register", "reg", cid, "--reviewer", "sh", "--payload", PAYLOAD, *extra)


@pytest.mark.integration
def test_register__doi_match__confirms_ids_and_registrations_lists_it(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    code, out = _register(env, cid)

    (event,) = load_registration_events(env.project.registrations_path)
    assert code == 0
    assert out.strip() == f"registered {event.record_id} (event {event.event_id})"
    code, out = _run(env, "registrations", "reg")
    assert code == 0
    assert out.split() == [event.event_id, event.record_id, DOI_ONE, "sh", event.ts.isoformat()]


@pytest.mark.integration
def test_register__unknown_candidate__exits_nonzero_with_message_and_writes_nothing(
    env: Env,  # noqa: F811
) -> None:
    code, out = _register(env, "nope")

    assert code == 1
    assert "unknown candidate 'nope'" in out
    assert "Traceback" not in out
    assert not env.project.registrations_path.exists()


@pytest.mark.integration
def test_register__near_match__needs_confirm_near(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2022)  # year off by one: a near match

    code, out = _register(env, cid, "--match-rule", "near_confirmed")
    assert code == 1
    assert "needs the reviewer's confirmation" in out
    assert not env.project.registrations_path.exists()

    code, _ = _register(env, cid, "--match-rule", "near_confirmed", "--confirm-near")
    assert code == 0
    (event,) = load_registration_events(env.project.registrations_path)
    assert event.match_rule == "near_confirmed"


@pytest.mark.integration
def test_registrations__no_log__prints_none_and_creates_no_file(env: Env) -> None:  # noqa: F811
    code, out = _run(env, "registrations", "reg")

    assert (code, out.strip()) == (0, "none")
    assert not env.project.registrations_path.exists()


@pytest.mark.integration
def test_reverse__valid_reason__removes_it_from_the_active_list(env: Env) -> None:  # noqa: F811
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))

    code, out = _run(
        env, "reverse", "reg", event.event_id, "--reviewer", "sh", "--reason", "wrong_doi"
    )

    assert code == 0
    assert f"reversed {event.event_id}" in out
    assert _run(env, "registrations", "reg") == (0, "none\n")


@pytest.mark.integration
def test_reverse__other_without_note__is_refused(env: Env) -> None:  # noqa: F811
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    before = env.snapshot()

    code, out = _run(env, "reverse", "reg", event.event_id, "--reviewer", "sh", "--reason", "other")

    assert code == 1
    assert "requires a non-empty note" in out
    assert env.snapshot() == before
