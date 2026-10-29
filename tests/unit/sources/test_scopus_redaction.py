"""Unit tests for ``register_secret_for_redaction`` surviving structlog reconfiguration."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
import structlog

from prismabib.cli import _capture_progress
from prismabib.sources import scopus
from prismabib.sources.scopus import register_secret_for_redaction

_Captured = list[dict[str, Any]]


def _sink_factory(captured: _Captured) -> Any:
    def sink(_logger: Any, _name: str, event_dict: Any) -> Any:
        captured.append(dict(event_dict))
        raise structlog.DropEvent

    return sink


@pytest.fixture
def captured() -> Iterator[_Captured]:
    """A sink-only structlog pipeline; global config and secrets restored afterwards."""
    saved_config = dict(structlog.get_config())
    saved_secrets = set(scopus._REDACTED_SECRETS)
    scopus._REDACTED_SECRETS.clear()
    events: _Captured = []
    structlog.configure(processors=[_sink_factory(events)])
    try:
        yield events
    finally:
        structlog.configure(processors=saved_config["processors"])
        scopus._REDACTED_SECRETS.clear()
        scopus._REDACTED_SECRETS.update(saved_secrets)


def test_register_secret__after_capture_progress_exits__redacts_both_secrets(
    captured: _Captured,
) -> None:
    log = structlog.get_logger()
    with _capture_progress(lambda _m: None):
        register_secret_for_redaction("KEY-ONE-aaaa")
    register_secret_for_redaction("KEY-TWO-bbbb")

    log.info("x", k="KEY-ONE-aaaa and KEY-TWO-bbbb")

    assert "KEY-ONE-aaaa" not in captured[-1]["k"]
    assert "KEY-TWO-bbbb" not in captured[-1]["k"]


def test_register_secret__called_twice__installs_the_processor_once(
    captured: _Captured,
) -> None:
    register_secret_for_redaction("KEY-ONE-aaaa")
    register_secret_for_redaction("KEY-TWO-bbbb")

    processors = structlog.get_config()["processors"]
    assert processors.count(scopus._redact_secrets_processor) == 1
    assert processors[0] is scopus._redact_secrets_processor


def test_register_secret__structlog_reconfigured_between_clients__still_redacts(
    captured: _Captured,
) -> None:
    register_secret_for_redaction("KEY-ONE-aaaa")
    structlog.configure(processors=[_sink_factory(captured)])
    register_secret_for_redaction("KEY-TWO-bbbb")

    structlog.get_logger().info("x", k="KEY-ONE-aaaa and KEY-TWO-bbbb")

    assert "KEY-ONE-aaaa" not in captured[-1]["k"]
    assert "KEY-TWO-bbbb" not in captured[-1]["k"]
