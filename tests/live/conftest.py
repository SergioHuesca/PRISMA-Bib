"""Every test under ``tests/live/`` talks to the real GitHub API.

``tests/conftest.py``'s ``_socket_policy`` fixture already re-enables sockets
for any test marked ``live`` (every test here carries that mark, set at
module scope in ``test_github_governance.py``). This conftest exists so that
``tests/live/`` is self-sufficient even when collected on its own -- e.g.
``pytest tests/live -m live`` -- independent of the parent fixture's marker
check, per the task's explicit requirement that this directory "works
without per-test boilerplate".
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from pytest_socket import enable_socket


@pytest.fixture(autouse=True)
def _sockets_enabled_for_live_tests() -> Iterator[None]:
    """Unconditionally lift the socket ban for this directory."""
    enable_socket()
    yield
