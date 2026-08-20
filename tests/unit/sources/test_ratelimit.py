"""Unit test for ``src/prismabib/sources/ratelimit.py`` (BUILD_PLAN Stage 2 Tests table, line 822).

:class:`~prismabib.sources.ratelimit.RateLimiter` is deliberately built to
accept an injected ``clock``/``sleep`` pair (its own module docstring: "so a
test can substitute a frozen/steerable clock ... without the limiter ever
truly blocking a test process"). That is the sanctioned test seam for this
class -- not ``time-machine`` (which freezes ``time.time()`` but does not
make ``time.sleep`` return instantly) and not a monkeypatch of any
``prismabib.*`` internal. A fake clock/sleep pair is a plain constructor
argument, exactly like ``respx`` standing in for a real ``httpx`` transport.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from prismabib.sources.ratelimit import RateLimiter


@dataclass
class _FakeClock:
    """A controllable time source: ``sleep`` advances ``now`` by its argument.

    The tiny ``+ 1e-9`` overshoot mirrors real ``time.sleep``'s "sleeps for
    *at least* this long" guarantee (see
    ``tests/property/sources/test_ratelimit.py``'s ``_FakeClock`` for the
    full rationale: an exact, jitter-free fake clock can otherwise trap
    :meth:`~prismabib.sources.ratelimit.RateLimiter.acquire`'s convergence
    loop on a token count one ULP below its target). It is recorded in
    ``sleeps`` before the overshoot is applied, so assertions against
    ``sleeps`` below see exactly what the limiter requested, unperturbed.
    """

    now: float = 1_700_000_000.0
    sleeps: list[float] = field(default_factory=list)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + 1e-9


@pytest.mark.unit
def test_ratelimit__header_says_zero_remaining__waits_until_reset() -> None:
    clock = _FakeClock()
    limiter = RateLimiter(rate=5.0, clock=clock.time, sleep=clock.sleep)
    reset_at = clock.now + 120.0

    limiter.observe_headers({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset_at)})
    limiter.acquire()

    assert clock.sleeps == pytest.approx([120.0])
    assert clock.now == pytest.approx(reset_at)
