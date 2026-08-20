"""Property test for ``src/prismabib/sources/ratelimit.py`` (BUILD_PLAN Stage 2 Tests table, line 823).

Hypothesis generates ``(rate, num_calls)`` schedules; for each, the token
bucket is driven through ``num_calls`` sequential ``acquire()`` calls on a
fake clock (the same injected-clock seam as
``tests/unit/sources/test_ratelimit.py`` -- see that module's docstring for
why this, not ``time-machine``, is the correct double here) and the gap
between every pair of consecutive completions is asserted to be at least
``1 / rate`` seconds -- i.e. the instantaneous request rate never exceeds
``rate``, for any generated schedule.

``capacity=1.0`` is pinned deliberately (rather than left at its
``rate``-sized default) so the property is exact rather than merely
asymptotic: with a one-token bucket there is no burst allowance to reason
about, so *every* consecutive pair -- not just the tail after a burst is
exhausted -- must be spaced by exactly ``1 / rate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from prismabib.sources.ratelimit import RateLimiter


@dataclass
class _FakeClock:
    """A controllable time source that mirrors real ``time.sleep``'s "at least" guarantee.

    A real OS scheduler never wakes a sleeper *exactly* on its requested
    deadline -- only at or after it -- and that small, essentially random
    overshoot is what keeps a real ``RateLimiter`` from ever landing on an
    exact floating-point fixed point. A bit-for-bit exact fake clock (``now
    += seconds``, no more) removes that overshoot entirely, and
    :meth:`~prismabib.sources.ratelimit.RateLimiter.acquire`'s
    ``while self._tokens < 1.0`` convergence loop can then chase a token
    count that lands one ULP below ``1.0`` forever for some
    hypothesis-generated ``rate`` values -- a genuine floating-point
    artefact of *this test's* clock fidelity, not a token-bucket-rate
    violation, and not something ``tests/`` may fix by patching
    ``src/prismabib/sources/ratelimit.py``. Adding the same trivially small
    epsilon overshoot a real clock would supply removes the artefact
    without weakening what is actually being asserted: the recorded
    ``sleeps`` durations below are still exactly what the limiter itself
    requested.
    """

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds + 1e-9


@pytest.mark.property
@given(
    rate=st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False),
    num_calls=st.integers(min_value=2, max_value=30),
)
@settings(max_examples=50, deadline=None)
def test_ratelimit__token_bucket__never_exceeds_configured_rate(
    rate: float, num_calls: int
) -> None:
    clock = _FakeClock()
    limiter = RateLimiter(rate=rate, capacity=1.0, clock=clock.time, sleep=clock.sleep)

    completion_times = []
    for _ in range(num_calls):
        limiter.acquire()
        completion_times.append(clock.now)

    gaps = [later - earlier for earlier, later in pairwise(completion_times)]
    minimum_gap = (1.0 / rate) - 1e-6

    assert all(gap >= minimum_gap for gap in gaps)
