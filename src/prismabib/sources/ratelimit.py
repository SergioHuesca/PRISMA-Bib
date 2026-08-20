"""Header-aware token-bucket rate limiter (BUILD_PLAN §Stage 2, line 766).

The Scopus Search API exposes its remaining quota via three response
headers -- ``X-RateLimit-Limit``, ``X-RateLimit-Remaining``, and
``X-RateLimit-Reset`` -- and a client that ignores them either wastes quota
racing ahead of a throttle it cannot see, or bursts past the server's own
cap and starts drawing 429s. :class:`RateLimiter` does two independent
things and does both unconditionally, every call:

1. **Hard-caps the client's own request rate** with a classic token bucket
   (default 6 requests/second, configurable) -- this is enforced
   independently of what the server reports, so a service that returns no
   rate-limit headers at all is still throttled.
2. **Honours the server's own throttle** by parking further requests until
   ``X-RateLimit-Reset`` once ``X-RateLimit-Remaining`` reaches zero.

Both the clock and the sleep function are injected (defaulting to
``time.time`` / ``time.sleep``) rather than called directly, precisely so a
test can substitute a frozen/steerable clock (e.g. ``time-machine``, which
patches ``time.time``) without the limiter ever truly blocking a test
process.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from time import sleep as _real_sleep
from time import time as _real_time

# One request costs exactly one token.
_ONE_TOKEN = 1.0

# Token accounting is floating point, and `acquire()`'s wait is computed as
# `deficit / rate` then converted back to tokens by `_refill()` as
# `elapsed * rate`. That round trip is not exact: with a clock that advances by
# precisely the requested amount, `_tokens` can settle one ULP BELOW 1.0 and the
# loop spins forever on ever-smaller sleeps, allocating as it goes. Real
# `time.sleep` hides this by always overshooting, but correctness should not
# depend on the OS being generous -- a hung acquisition on page 40 of 71 is a
# silent stall, not a crash. This epsilon makes the loop terminate on the last
# meaningful iteration; it is far smaller than any scheduling granularity, so it
# cannot let the observed rate exceed the configured cap.
_TOKEN_EPSILON = 1e-9


Clock = Callable[[], float]
"""A zero-argument callable returning the current time as epoch seconds.

Deliberately wall-clock (``time.time``-shaped) rather than monotonic:
``X-RateLimit-Reset`` is itself a wall-clock epoch timestamp, and using a
single clock abstraction for both the token-bucket refill and the
header-driven wait avoids reconciling two different time bases.
"""

Sleep = Callable[[float], None]
"""A one-argument callable that blocks for (at least) the given seconds."""


class RateLimiter:
    """A header-aware token bucket, hard-capped at a configurable request rate.

    Args:
        rate: The maximum sustained request rate, in requests per second.
            Defaults to 6/s per BUILD_PLAN §Stage 2 line 766. Must be
            strictly positive.
        capacity: The bucket's burst capacity, in tokens. Defaults to
            ``rate``, i.e. at most one second's worth of burst.

            Note precisely what this does and does not guarantee. The bucket
            starts full, so the first ``capacity`` requests are granted
            immediately and a short window spanning them *does* exceed
            ``rate`` -- 200 requests at ``rate=6`` measure 6.19/s because
            six of them were free. What is capped is the **sustained** rate:
            excluding the initial burst it is exactly ``rate``, and the
            excess is bounded by ``capacity`` for the process's whole life,
            never per-window. That is the standard token-bucket contract and
            what BUILD_PLAN line 766 asks for; a larger ``capacity`` would
            widen the burst without changing the sustained rate.
        clock: Injected time source; see :data:`Clock`. Defaults to
            ``time.time``.
        sleep: Injected blocking primitive; see :data:`Sleep`. Defaults to
            ``time.sleep``.

    Raises:
        ValueError: If ``rate`` or ``capacity`` is not strictly positive.
    """

    def __init__(
        self,
        rate: float = 6.0,
        *,
        capacity: float | None = None,
        clock: Clock = _real_time,
        sleep: Sleep = _real_sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be > 0, got {rate!r}")
        resolved_capacity = capacity if capacity is not None else rate
        if resolved_capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {resolved_capacity!r}")

        self.rate = rate
        self.capacity = resolved_capacity
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.capacity
        self._last_refill = clock()
        self._reset_at: float | None = None

    def acquire(self) -> None:
        """Block (via the injected ``sleep``) until one request may proceed.

        First honours any pending server-reported reset (see
        :meth:`observe_headers`), then draws one token from the bucket,
        sleeping in increments if the bucket is empty. Never raises under
        normal operation -- it always eventually returns, deferring solely
        to whatever the injected ``sleep`` does.
        """
        self._wait_for_reset()
        self._refill()
        while self._tokens < _ONE_TOKEN - _TOKEN_EPSILON:
            deficit = _ONE_TOKEN - self._tokens
            wait_seconds = deficit / self.rate
            self._sleep(wait_seconds)
            self._refill()
        self._tokens = max(0.0, self._tokens - _ONE_TOKEN)

    def observe_headers(self, headers: Mapping[str, str]) -> None:
        """Record the server's throttle state from an API response.

        Args:
            headers: The response headers. Only ``X-RateLimit-Remaining``
                and ``X-RateLimit-Reset`` are consulted; both must be
                present and parse as numbers for this call to have any
                effect. ``X-RateLimit-Limit`` is accepted by the API and by
                callers of this class for observability/logging, but this
                limiter does not itself resize its bucket to match it --
                BUILD_PLAN line 766 asks for a *hard cap* at the configured
                client rate, not a limiter that grows to trust whatever the
                server claims.

        Notes:
            When ``X-RateLimit-Remaining`` is zero (or negative), the next
            call to :meth:`acquire` blocks until ``X-RateLimit-Reset``
            (an epoch-seconds timestamp) has passed. Missing or
            unparseable headers are ignored rather than raising, since a
            throttling header is an optimisation the caller cannot control
            upstream returning malformed data.
        """
        remaining_raw = headers.get("X-RateLimit-Remaining")
        reset_raw = headers.get("X-RateLimit-Reset")
        if remaining_raw is None or reset_raw is None:
            return
        try:
            remaining = int(remaining_raw)
            reset_at = float(reset_raw)
        except ValueError:
            return
        if remaining <= 0:
            self._reset_at = reset_at

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_refill = now

    def _wait_for_reset(self) -> None:
        if self._reset_at is None:
            return
        now = self._clock()
        remaining = self._reset_at - now
        if remaining > 0:
            self._sleep(remaining)
        self._reset_at = None
        # A server-mandated reset replenishes the bucket outright: whatever
        # partial refill the elapsed wait would have produced is a lower
        # bound the server has just told us is safe to exceed.
        self._tokens = self.capacity
        self._last_refill = self._clock()
