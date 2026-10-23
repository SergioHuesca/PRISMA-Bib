"""Neutral id generation: the ``IdFactory`` protocol and the monotonic ULID factory.

Lives outside :mod:`prismabib.prisma` so capture/taxonomy writers need not import the
PRISMA event module for an id source. :mod:`prismabib.prisma.events` re-exports both names.
"""

from __future__ import annotations

import secrets
import threading
import time
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class IdFactory(Protocol):
    """Injectable id generator, structurally identical to ``tests/conftest.py``'s protocol.

    Production code (``log.py``) depends on this ``Protocol``, not on
    :class:`MonotonicUlidFactory` directly, so a test can substitute a
    seeded, deterministic double (``tests/conftest.py``'s
    ``SeededIdFactory``) and get an identical, reproducible sequence of
    ``event_id`` values.
    """

    def __call__(self) -> str:
        """Return the next id in sequence."""
        ...


# ---------------------------------------------------------------------------
# Monotonic ULID
# ---------------------------------------------------------------------------

_CROCKFORD_ALPHABET: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LENGTH: Final[int] = 26
_TIMESTAMP_BITS: Final[int] = 48
_RANDOMNESS_BITS: Final[int] = 80
_TIMESTAMP_MASK: Final[int] = (1 << _TIMESTAMP_BITS) - 1
_RANDOMNESS_MASK: Final[int] = (1 << _RANDOMNESS_BITS) - 1


def _encode_ulid(value: int) -> str:
    """Encode a 128-bit integer as a 26-character Crockford base32 ULID string.

    Args:
        value: A non-negative integer that fits in 128 bits: the top 48
            bits are the millisecond timestamp, the low 80 bits are the
            randomness component.

    Returns:
        The 26-character encoding. Because base32 digit order matches
        numeric magnitude order, and every encoding is left-padded to the
        same fixed width, two ULIDs compare the same way as strings and as
        integers -- lexicographic sort order over the encoded string equals
        numeric order over ``value`` (BUILD_PLAN's fold depends on exactly
        this to break ``ts`` ties by ``event_id``).
    """
    characters = [""] * _ULID_LENGTH
    remaining = value
    for index in range(_ULID_LENGTH - 1, -1, -1):
        characters[index] = _CROCKFORD_ALPHABET[remaining & 0x1F]
        remaining >>= 5
    return "".join(characters)


class MonotonicUlidFactory:
    """A stdlib-only, monotonic ULID generator satisfying :class:`IdFactory`.

    Each id is 48 bits of millisecond Unix timestamp followed by 80 bits of
    randomness, Crockford base32-encoded (26 characters). Within a single
    instance, successive ids are always non-decreasing:

    - **Clock has visibly advanced** (a later call observes a larger
      millisecond timestamp than the previous one): a fresh 80-bit random
      component is drawn via :func:`secrets.randbits`.
    - **Same millisecond as the previous call** (the common case for two
      decisions logged back-to-back in a notebook cell, or two events
      appended within the same millisecond by different reviewers): the
      *previous* random component is incremented by one rather than
      redrawn. Redrawing would risk a smaller value than the previous
      call's and silently break the fold's ``event_id`` tie-break;
      incrementing guarantees strict ordering.
    - **Clock has gone backwards** (NTP adjustment, VM suspend/resume): the
      previous millisecond is reused and the random component is
      incremented, exactly as in the same-millisecond case -- this
      generator's monotonicity guarantee never depends on the wall clock
      being monotonic itself.
    - **80-bit randomness component overflows** (would require roughly
      2**80 calls inside one process within a single millisecond -- not
      reachable in practice, but silently wrapping around would produce a
      *smaller* value and break monotonicity): the timestamp is bumped
      forward by one millisecond and a fresh random component is drawn,
      preserving strict ordering at the cost of the timestamp briefly
      running ahead of the wall clock.

    Not shared automatically across instances or processes: monotonicity is
    only guaranteed within one instance's call sequence, which is exactly
    the scope ``log.py`` needs it for (one ``DecisionLog`` instance per
    append site).
    """

    def __init__(self, *, randbits: Callable[[int], int] = secrets.randbits) -> None:
        """Initialise a generator with no prior calls.

        Args:
            randbits: Source of the 80-bit random component, taking a bit
                width. Defaults to :func:`secrets.randbits`, which is what
                production uses and what the monotonicity argument assumes.

                Injected solely so the **overflow branch is reachable at
                all**: it fires only when the random component saturates
                within one millisecond, which takes roughly 2**80 calls and
                cannot be provoked by calling this object. Left untestable,
                that branch silently accepted a mutation setting
                ``timestamp_ms = 1`` -- resetting every subsequent event to
                one millisecond after the Unix epoch and destroying the
                ordering the decision log's fold depends on -- and no test
                in the suite noticed. §3.7.3 rule 1 permits injecting a
                double at a boundary like this; reaching into
                ``_last_randomness`` from a test would couple the test to
                internals instead.
        """
        self._lock = threading.Lock()
        self._randbits = randbits
        self._last_timestamp_ms = -1
        self._last_randomness = -1

    def __call__(self) -> str:
        """Return the next id in sequence.

        Returns:
            A 26-character Crockford base32 ULID string, strictly greater
            than (or, in the astronomically unlikely randomness-overflow
            case, still greater than) every id this instance has returned
            before.
        """
        with self._lock:
            timestamp_ms = time.time_ns() // 1_000_000
            if timestamp_ms > self._last_timestamp_ms:
                randomness = self._randbits(_RANDOMNESS_BITS)
            else:
                timestamp_ms = self._last_timestamp_ms
                randomness = self._last_randomness + 1
                if randomness > _RANDOMNESS_MASK:
                    timestamp_ms += 1
                    randomness = self._randbits(_RANDOMNESS_BITS)
            self._last_timestamp_ms = timestamp_ms
            self._last_randomness = randomness
            value = (timestamp_ms & _TIMESTAMP_MASK) << _RANDOMNESS_BITS | randomness
            return _encode_ulid(value)


__all__ = ["IdFactory", "MonotonicUlidFactory"]
