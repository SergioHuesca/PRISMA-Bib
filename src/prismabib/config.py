"""Environment configuration (BUILD_PLAN §3.1, lines 307-316).

Reads the four ``.env`` variables via ``pydantic-settings``:

```
SCOPUS_API_KEY=
SCOPUS_INSTTOKEN=          # optional; required for off-campus COMPLETE view
ELSEVIER_SD_API_KEY=       # may be the same key with different entitlements
UNPAYWALL_EMAIL=           # Stage 6 (ADR 0019): required by the Unpaywall API's
                           # own terms of use, not a credential -- see below
PRISMABIB_PROJECTS_ROOT=./projects
```

``UNPAYWALL_EMAIL`` is not a secret: Unpaywall's API takes a plain ``email``
query parameter instead of an API key (https://unpaywall.org/products/api),
and its terms of use require a real, reachable address rather than a
placeholder -- Unpaywall states it may contact high-volume users, and a
fabricated address forfeits that. It stays a plain ``str`` field (unlike the
``SecretStr`` credentials above) precisely because it is not meant to be kept
out of logs; it is meant to be a real contact.

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


class ProjectsRootSettings(BaseSettings):
    """Just ``PRISMABIB_PROJECTS_ROOT``, resolvable without any credential.

    Deliberately separate from :class:`Settings`. ``prismabib init`` is the
    first command a new researcher runs, and it only needs to know *where*
    to create a directory -- yet resolving that through :class:`Settings`
    demanded ``SCOPUS_API_KEY``, so creating an empty folder failed until
    they had obtained an Elsevier credential. That is a discouraging first
    contact for a step that touches no network at all, and it inverts the
    natural order: a researcher wants somewhere to put a project *before*
    they go and request an API key.

    Reads the same ``.env`` as :class:`Settings`, so a configured root is
    still honoured; it simply declares no required secret of its own.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    prismabib_projects_root: Path = Path("./projects")


class FullTextSettings(BaseSettings):
    """The credentials full-text resolution needs, and nothing else.

    :class:`Settings` requires ``SCOPUS_API_KEY`` unconditionally, but
    :func:`prismabib.fulltext.resolve.default_chain` never calls Scopus: it
    talks to Elsevier's Article Retrieval API, to Unpaywall, and to a local
    drop directory. Requiring the Scopus key there made ``prismabib fulltext``
    fail outright for a reviewer who has PDFs in ``fulltext/manual/`` and no
    Scopus subscription at all -- and it hid in CI behind developers' own
    ``.env`` files, which do carry the key.

    Same reasoning as :class:`ProjectsRootSettings`, which exists because a
    researcher wants somewhere to put a project before they go and request an
    API key. Reads the same ``.env``; declares no required secret of its own,
    because every resolver it configures is individually optional and the chain
    degrades to the manual drop when both are absent.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    elsevier_sd_api_key: SecretStr | None = None
    unpaywall_email: str | None = None


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
    unpaywall_email: str | None = None
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
        # pydantic-settings matches a field to its env var case-insensitively, so the
        # upper-cased field name IS the variable the operator must set. A lookup
        # table here was dead code whose only effect was to trip detect-secrets.
        env_name = field_name.upper()
        problems.append(f"{env_name}: {error['msg']}")
    joined = "; ".join(problems) if problems else str(exc)
    return ConfigError(
        f"Invalid prismabib configuration ({joined}). "
        "Set the missing/invalid variable(s) in your environment or in a "
        ".env file -- see .env.example."
    )
