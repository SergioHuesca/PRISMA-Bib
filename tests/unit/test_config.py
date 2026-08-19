"""Unit tests for ``src/prismabib/config.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

``monkeypatch.delenv``/``monkeypatch.setenv`` here control the *process
environment* ``Settings`` reads from -- not a ``prismabib.*`` internal, so
this does not run afoul of §3.7.3 rule 1 ("never monkeypatch
``prismabib.*`` internals"). ``_env_file=None`` additionally stops
``Settings`` from picking up a real, developer-local ``.env`` file, so these
tests are hermetic regardless of what is on disk outside ``tests/``.
"""

from __future__ import annotations

import pytest

from prismabib.config import Settings
from prismabib.errors import ConfigError


@pytest.mark.unit
def test_config__missing_api_key__raises_config_error_naming_the_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="SCOPUS_API_KEY"):
        Settings(_env_file=None)


@pytest.mark.unit
def test_config__repr__never_contains_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "super-secret-value")

    settings = Settings(_env_file=None)

    assert "super-secret-value" not in repr(settings)
