"""What the keys screen decides, without widgets (GUI spec #312, ticket #325)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

import httpx
from dotenv import dotenv_values

from prismabib.credentials import ENV_FILENAME, ProbeResult
from prismabib.project import Project
from prismabib.source_mode import CAPABILITY_MODES
from prismabib.sources.openalex import OpenAlexClient
from prismabib.sources.scopus import register_secret_for_redaction
from prismabib.user_settings import load_settings, read_user_setting, write_user_settings

OPENALEX_KEY: Final = "OPENALEX_API_KEY"
SCOPUS_KEY: Final = "SCOPUS_API_KEY"

#: The next step shown beside a failed test, keyed by the probe label's leading status.
_NEXT: Final = {
    "401": "Copy the key again from your account page, paste it here, save it and test again.",
    "403": "Check that your account may use this service (for Scopus, the institutional token).",
    "network": "Check your connection, then test again.",
}


def project_env_file(project: Project) -> Path:
    """The Project's own environment file (it need not exist); its keys beat the per-user file's."""
    return project.root / ENV_FILENAME


def scopus_needed(project: Project) -> bool:
    """Whether this Project's Source mode searches Scopus, so a Scopus key is needed."""
    return project.source_mode in CAPABILITY_MODES["scopus_search"]


def key_source(name: str, project: Project, user_file: Path | None = None) -> str:
    """Which place supplies ``name`` now, in words; never the value."""
    if os.environ.get(name):
        return "the process environment (it beats both files)"
    env = project_env_file(project)
    if env.is_file() and dotenv_values(env).get(name):
        return "this Project's environment file (it beats the per-user file)"
    if read_user_setting(name, user_file):
        return "your per-user settings file"
    return "not set"


def save_openalex_key(value: str, user_file: Path | None = None) -> Path:
    """Store the key (stripped) in the per-user file; the caller clears the input."""
    return write_user_settings({OPENALEX_KEY: value.strip()}, user_file)


def probe_openalex_key(
    project: Project, user_file: Path | None = None, *, http_client: httpx.Client | None = None
) -> ProbeResult:
    """One OpenAlex request with the key that applies: ok, 401, 403, another status or network."""
    settings = load_settings(project_env_file(project), user_file)
    if settings.openalex_api_key is None:
        return ProbeResult(False, "no key is set, so there is nothing to test")
    key = settings.openalex_api_key.get_secret_value()
    register_secret_for_redaction(key)
    client = http_client if http_client is not None else httpx.Client(timeout=30.0)
    try:
        response = client.get(
            OpenAlexClient.ENDPOINT,
            params={"per-page": "1"},
            headers={"Authorization": f"Bearer {key}"},
        )
    except (httpx.HTTPError, UnicodeError) as exc:
        return ProbeResult(False, f"network error ({type(exc).__name__})")
    finally:
        if http_client is None:
            client.close()
    status = response.status_code
    if status == 200:
        return ProbeResult(True, "ok")
    if status in (401, 403):
        return ProbeResult(False, f"{status}: the key was rejected")
    return ProbeResult(False, f"http {status}")


def readable(result: ProbeResult) -> str:
    """A test result as a sentence, with the next step when it failed."""
    if result.ok:
        return "The key works."
    step = next((v for k, v in _NEXT.items() if result.label.startswith(k)), "Test again later.")
    return f"The test failed: {result.label}. {step}"
