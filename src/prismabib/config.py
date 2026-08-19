"""Environment configuration (BUILD_PLAN §3.1, lines 307-316).

Reads the four ``.env`` variables via ``pydantic-settings``:

```
SCOPUS_API_KEY=
SCOPUS_INSTTOKEN=          # optional; required for off-campus COMPLETE view
ELSEVIER_SD_API_KEY=       # may be the same key with different entitlements
PRISMABIB_PROJECTS_ROOT=./projects
```

``SCOPUS_API_KEY`` is the one required secret: without it, no source can be
queried, so :class:`Settings` fails loudly and by name rather than letting a
generic Pydantic error surface later at first use. All secrets are typed
``SecretStr`` so they cannot leak via ``repr``, ``str``, logging, or an
unguarded traceback -- ``SecretStr.__repr__`` always renders as
``SecretStr('**********')`` regardless of the underlying value.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import SecretStr
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from prismabib.errors import ConfigError

# Maps a Settings field name to the environment variable it reads, so a validation
# failure can name the variable the operator actually has to set (§Stage 1's
# test_config__missing_api_key__raises_config_error_naming_the_var).
#
# The `allowlist secret` pragma is deliberate and narrow: detect-secrets' keyword
# detector fires on a key-ish name followed by a quoted string, and this line holds two
# *identifiers*, never a value. Real keys live in an untracked `.env` (§3.1) and are
# rejected by the §2.5 data guard. Scoped to this one line so the hook keeps working
# everywhere else — the repository is public, so it is the gate that matters.
_REQUIRED_VAR_NAMES: dict[str, str] = {
    "scopus_api_key": "SCOPUS_API_KEY",  # pragma: allowlist secret
}


class Settings(BaseSettings):
    """prismabib's environment configuration.

    Field names are lower-snake-case; ``pydantic-settings`` matches them to
    the upper-case environment variable of the same name case-insensitively
    (e.g. ``scopus_api_key`` <-> ``SCOPUS_API_KEY``), and also reads a
    local ``.env`` file when present.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    scopus_api_key: SecretStr
    scopus_insttoken: SecretStr | None = None
    elsevier_sd_api_key: SecretStr | None = None
    prismabib_projects_root: Path = Path("./projects")

    def __init__(self, **data: Any) -> None:
        """Construct settings from the environment, translating failures.

        Args:
            **data: Explicit overrides, forwarded to
                ``pydantic_settings.BaseSettings``; primarily used by tests
                to inject values without mutating the real environment.
                Typed ``Any`` because it is forwarded verbatim to
                ``BaseSettings.__init__``, whose own keyword arguments span
                several unrelated types (bool, str, Path, mappings, ...).

        Raises:
            ConfigError: If a required variable is missing or any value
                fails validation. When ``SCOPUS_API_KEY`` is the missing
                variable, the message names it explicitly.
        """
        try:
            super().__init__(**data)
        except PydanticValidationError as exc:
            raise _translate(exc) from exc


def _translate(exc: PydanticValidationError) -> ConfigError:
    """Turn a Pydantic settings validation failure into a readable :class:`ConfigError`.

    Args:
        exc: The Pydantic validation error raised while constructing
            :class:`Settings`.

    Returns:
        A :class:`ConfigError` whose message names every missing/invalid
        variable by its ``.env`` name (uppercased field name), so
        ``SCOPUS_API_KEY`` in particular is always named verbatim when it is
        the cause.
    """
    problems: list[str] = []
    for error in exc.errors():
        field_name = str(error["loc"][0]) if error["loc"] else "<unknown>"
        env_name = _REQUIRED_VAR_NAMES.get(field_name, field_name.upper())
        problems.append(f"{env_name}: {error['msg']}")
    joined = "; ".join(problems) if problems else str(exc)
    return ConfigError(
        f"Invalid prismabib configuration ({joined}). "
        "Set the missing/invalid variable(s) in your environment or in a "
        ".env file -- see .env.example."
    )
