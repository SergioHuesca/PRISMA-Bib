"""Content-addressed HTTP cache (BUILD_PLAN §Stage 2, line 772; risk 2, line 1488).

Keyed on ``(url, params)`` rather than on response content, because the
request -- not the response -- is what a re-run needs to recognise as
"already fetched": the whole point is to avoid re-spending Scopus quota on
a request the operator already made during development, and the response
does not exist yet until we decide whether to make it.

The cache stores the exact response bytes it was given, unmodified, so that
a warm-cache re-run replays byte-for-byte what the server returned
(BUILD_PLAN S02-AC2). Callers that need to persist a *reproducible*
serialisation (e.g. :mod:`prismabib.capture.writer`, which re-encodes the
parsed page rather than storing raw bytes -- see that module's docstring for
why) get that reproducibility from re-encoding deterministically, not from
this cache; this cache's only promise is "you get back exactly what you
put in".
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path


class HttpCache:
    """A simple content-addressed cache of raw HTTP response bodies.

    Args:
        root: The directory the cache is rooted at. Created on first write
            if it does not already exist.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def key_for(self, url: str, params: Mapping[str, str]) -> str:
        """Compute the cache key for a request.

        Args:
            url: The request URL (without query string -- ``params`` is
                passed separately so callers do not have to pre-encode it).
            params: The request's query parameters.

        Returns:
            A hex-encoded SHA-256 digest of the canonicalised
            ``(url, params)`` pair. Canonicalisation sorts ``params`` by key
            so that dict-ordering differences between two otherwise-identical
            requests never produce different keys.
        """
        canonical = json.dumps(
            {"url": url, "params": dict(sorted(params.items()))},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def load(self, url: str, params: Mapping[str, str]) -> bytes | None:
        """Return the cached response body for a request, if present.

        Args:
            url: The request URL.
            params: The request's query parameters.

        Returns:
            The exact bytes previously passed to :meth:`store` for this
            ``(url, params)`` pair, or ``None`` on a cache miss.
        """
        path = self._path_for(self.key_for(url, params))
        if not path.is_file():
            return None
        return path.read_bytes()

    def store(self, url: str, params: Mapping[str, str], body: bytes) -> None:
        """Cache a response body for a request.

        Writes are atomic (write-to-temp-then-rename) so a process killed
        mid-write can never leave a corrupt cache entry that a later
        :meth:`load` would return.

        Args:
            url: The request URL.
            params: The request's query parameters.
            body: The exact response bytes to cache, verbatim.
        """
        key = self.key_for(url, params)
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_bytes(body)
        tmp_path.replace(path)

    def _path_for(self, key: str) -> Path:
        """Resolve the on-disk path for a cache key, sharded by its first two hex digits.

        Args:
            key: A hex-encoded SHA-256 digest as returned by :meth:`key_for`.

        Returns:
            ``<root>/<key[:2]>/<key>.bin`` -- the two-character shard keeps
            any single directory from accumulating thousands of entries
            over a long-running review.
        """
        return self.root / key[:2] / f"{key}.bin"
