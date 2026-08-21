"""Tests for Double Circuit Breaker.

Tier 1 (SimpleCircuitBreaker) tests use an INJECTED CLOCK — a list
holding a single float — so cooldown behavior is deterministic
without ``time.sleep``. The test mutates ``clock[0]`` to "advance
time," and the breaker reads from ``lambda: clock[0]``.
"""

import pytest

from double_circuit_breaker.breaker import (
    CircuitOpenError,
    SimpleCircuitBreaker,
)


def make_clock(initial: float = 0.0) -> list[float]:
    """A test clock: a single-element list. Tests mutate ``clock[0]``
    to advance time; the breaker reads via ``lambda: clock[0]``.
    """
    return [initial]


def failing_fn():
    """A callable that always raises ValueError."""
    raise ValueError("boom")


def succeeding_fn():
    """A callable that always returns the sentinel string ``"ok"``."""
    return "ok"


# ---------------------------------------------------------------------------
# CLOSED state — the happy path.
# ---------------------------------------------------------------------------


def test_closed_passes_successful_calls_through() -> None:
    """A fresh breaker is Closed; successful calls return their value."""
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0, clock=lambda: clock[0])
    assert b.call(succeeding_fn) == "ok"
    assert b.call(succeeding_fn) == "ok"


def test_closed_propagates_exceptions() -> None:
    """A failing call in Closed state re-raises the underlying exception."""
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0, clock=lambda: clock[0])
    with pytest.raises(ValueError, match="boom"):
        b.call(failing_fn)


def test_closed_single_success_resets_failure_count() -> None:
    """Consecutive-failure model: one success between failures resets
    the count, so the breaker doesn't open prematurely.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0, clock=lambda: clock[0])
    # 2 failures, then a success, then 2 more failures = 2 consecutive, not 4.
    for _ in range(2):
        with pytest.raises(ValueError):
            b.call(failing_fn)
    assert b.call(succeeding_fn) == "ok"
    for _ in range(2):
        with pytest.raises(ValueError):
            b.call(failing_fn)
    # Still Closed — the success reset the count to 0; the second pair only
    # got us to 2/3.
    assert b.call(succeeding_fn) == "ok"


# ---------------------------------------------------------------------------
# CLOSED → OPEN transition.
# ---------------------------------------------------------------------------


def test_threshold_failures_open_the_breaker() -> None:
    """``failure_threshold`` consecutive failures transitions to Open;
    the very next call raises CircuitOpenError without invoking fn.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0, clock=lambda: clock[0])
    for _ in range(3):
        with pytest.raises(ValueError):
            b.call(failing_fn)
    # Now Open.
    invoked = [False]

    def sentinel_fn():
        invoked[0] = True
        return "should not run"

    with pytest.raises(CircuitOpenError):
        b.call(sentinel_fn)
    assert invoked[0] is False, "Open breaker must not invoke fn"


# ---------------------------------------------------------------------------
# OPEN behavior — fail-fast within the cooldown window.
# ---------------------------------------------------------------------------


def test_open_fails_fast_during_cooldown() -> None:
    """Within the cooldown window, every call raises CircuitOpenError
    immediately — no probing, no fn invocation.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: clock[0])
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Open. Multiple calls inside the cooldown window all raise.
    for dt in (0.0, 1.0, 5.0, 9.999):
        clock[0] = dt
        with pytest.raises(CircuitOpenError):
            b.call(succeeding_fn)


# ---------------------------------------------------------------------------
# OPEN → HALF_OPEN → CLOSED — the recovery path.
# ---------------------------------------------------------------------------


def test_half_open_probe_success_closes_the_breaker() -> None:
    """After cooldown elapses, the next call is the Half-Open probe.
    If the probe succeeds, the breaker transitions to Closed and the
    failure count resets — subsequent failures restart the count.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: clock[0])
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Advance past cooldown.
    clock[0] = 10.0
    # Probe runs.
    assert b.call(succeeding_fn) == "ok"
    # Now Closed; a single failure should NOT reopen (threshold=1, but
    # the count was reset to 0 by the Half-Open success).
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Open again.
    with pytest.raises(CircuitOpenError):
        b.call(succeeding_fn)


def test_half_open_probe_failure_reopens_with_restarted_cooldown() -> None:
    """If the Half-Open probe fails, the breaker transitions back to
    Open AND restarts the cooldown timer from the probe's clock value.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: clock[0])
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Cooldown elapses; probe will run on the next call.
    clock[0] = 10.0
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Breaker is now Open again, with cooldown restarted from clock[0]=10.0.
    # Even at clock[0]=19.9 (9.9 sec after probe failed), still Open.
    clock[0] = 19.9
    with pytest.raises(CircuitOpenError):
        b.call(succeeding_fn)
    # At clock[0]=20.0, cooldown has elapsed again.
    clock[0] = 20.0
    assert b.call(succeeding_fn) == "ok"


def test_cooldown_boundary_at_exactly_cooldown_seconds_allows_probe() -> None:
    """The probe fires when ``clock() - opened_at >= cooldown`` —
    inclusive boundary. A breaker with cooldown=10 that opened at
    t=0 should probe at exactly t=10.
    """
    clock = make_clock()
    b = SimpleCircuitBreaker(failure_threshold=1, cooldown=10.0, clock=lambda: clock[0])
    with pytest.raises(ValueError):
        b.call(failing_fn)
    # Exactly at the cooldown boundary — probe should fire.
    clock[0] = 10.0
    assert b.call(succeeding_fn) == "ok"
