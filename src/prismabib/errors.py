"""The prismabib error taxonomy (BUILD_PLAN §3.3, lines 376-392).

```
PrismabibError
├── ConfigError
├── SourceError
│   ├── AuthError            # 401 -- bad key
│   ├── EntitlementError     # 403 -- key valid, view/content not entitled
│   ├── QuotaExceededError   # 429 with weekly quota headers exhausted
│   ├── RateLimitError       # 429 transient, retryable
│   └── UpstreamError        # 5xx
├── StoreError
├── LogError                 # append-only violation, schema drift
└── ValidationError
```

**On the ``ValidationError`` name collision.** Pydantic v2 also defines
``pydantic.ValidationError``, and BUILD_PLAN §3.3 names this class
``ValidationError`` regardless -- the spec's error tree is frozen and is not
renamed to dodge the clash. The two types are unrelated (this one does not
subclass Pydantic's) and must not be confused:

- Never do ``from prismabib.errors import *`` in a module that also does
  ``from pydantic import ValidationError`` (or vice versa) -- the second
  import silently wins and callers will be matching the wrong exception in
  ``except`` clauses.
- Code that needs both in the same module should import at least one under
  an alias, e.g. ``from pydantic import ValidationError as
  PydanticValidationError``.
- Code that catches a ``pydantic.ValidationError`` at a boundary (for
  example while parsing ``criteria.yaml`` in :mod:`prismabib.project`) must
  translate it into a prismabib exception -- either this ``ValidationError``
  or a more specific sibling such as ``ConfigError`` -- before it is allowed
  to propagate out of prismabib code. A bare ``pydantic.ValidationError``
  must never cross a public prismabib API boundary.
"""

from __future__ import annotations


class PrismabibError(Exception):
    """Base class for every exception raised deliberately by prismabib."""


class ConfigError(PrismabibError):
    """Configuration is missing or invalid.

    Covers environment settings (e.g. a missing ``SCOPUS_API_KEY``) and
    project-level configuration files (``project.toml``, ``criteria.yaml``).
    """


class SourceError(PrismabibError):
    """Base class for failures talking to an external metadata/full-text source."""


class AuthError(SourceError):
    """The upstream source rejected the request with HTTP 401 (bad key)."""


class EntitlementError(SourceError):
    """The upstream source rejected the request with HTTP 403.

    The key is valid but the requested view or content is not entitled.
    BUILD_PLAN §3.3: this must never be silently swallowed -- a full-text
    stage that quietly degrades to "inaccessible" on a 403 biases the corpus
    and misreports ``M_full``.
    """


class QuotaExceededError(SourceError):
    """HTTP 429 where the upstream's weekly quota headers report exhaustion."""


class RateLimitError(SourceError):
    """HTTP 429 that is transient and safe to retry with backoff."""


class UpstreamError(SourceError):
    """The upstream source returned an HTTP 5xx response."""


class StoreError(PrismabibError):
    """A Layer 1 (DuckDB normalised store) operation failed."""


class LogError(PrismabibError):
    """A Layer 2 decision-log invariant was violated.

    For example, an attempted mutation of an append-only log, or a record
    whose shape does not match the log's declared schema version.
    """


class ValidationError(PrismabibError):
    """A prismabib domain object failed validation.

    See the module docstring for how this relates to, and must be kept
    distinct from, ``pydantic.ValidationError``.
    """
