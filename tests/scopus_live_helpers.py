"""The CI-only 401 skip rule for the Scopus live test (issue #302), kept here so a fast test can prove it."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from prismabib.errors import AuthError


@contextmanager
def skip_on_ci_auth_error() -> Iterator[None]:
    """Skip on a Scopus 401 under GitHub Actions; re-raise it anywhere else.

    Only ``AuthError`` is caught, so schema drift, 5xx and network errors always fail.
    """
    try:
        yield
    except AuthError:
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise
        pytest.skip(
            "Scopus answered 401 on GitHub Actions: the runners' key is rejected; "
            "renew it at dev.elsevier.com"
        )
