"""Double Circuit Breaker (multi-server) — a payments-platform interview question.

A circuit breaker wraps a fallible operation and stops calling it once
failures accumulate, giving the failing dependency time to recover.
The **double** variant composes two breakers — primary and secondary —
with routing logic: try primary, fall back to secondary on Open or
failure. This is the multi-PSP failover pattern (Stripe ↔ Adyen) a
fintech payments team uses in production.

Input:
    call(fn: Callable[[], T]) -> T
        Invoke ``fn`` through the breaker. Returns its result on
        success. If the breaker is Open, raises ``CircuitOpenError``
        WITHOUT calling fn. If fn raises, the breaker records a
        failure and re-raises.

Output:
    The return value of ``fn`` on success. On failure or while Open,
    an exception propagates. The breaker's internal state mutates as
    a side effect.

State machine (the canonical three-state circuit breaker):

    ┌─────────┐  failures ≥ threshold   ┌──────┐
    │ CLOSED  │ ───────────────────────►│ OPEN │
    │ (pass   │                         │(fail-│
    │  through│                         │ fast)│
    │  + count│                         │      │
    │  fail)  │                         └──┬───┘
    └────▲────┘                            │ cooldown elapsed
         │                                 ▼
         │     probe success     ┌─────────────┐
         └─────────────────────  │  HALF-OPEN  │
                                 │  (1 probe)  │
                                 │             │
                                 └──────┬──────┘
                                        │ probe failed
                                        ▼ (back to OPEN, restart cooldown)

Example 1 (basic open-on-threshold):
    breaker = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0)
    breaker.call(failing_fn)                # CLOSED, count=1, re-raise
    breaker.call(failing_fn)                # CLOSED, count=2, re-raise
    breaker.call(failing_fn)                # CLOSED, count=3 → OPEN, re-raise
    breaker.call(any_fn)                    # OPEN → raises CircuitOpenError
                                            # WITHOUT calling any_fn

Example 2 (recovery via Half-Open probe):
    (continuing from Example 1)
    # 10 seconds pass on the injected clock...
    breaker.call(succeeding_fn)             # OPEN + cooldown elapsed →
                                            # HALF-OPEN; probe runs; success →
                                            # CLOSED; returns the result
    breaker.call(succeeding_fn)             # CLOSED, count=0, returns result

Example 3 (probe failure restarts cooldown):
    breaker = SimpleCircuitBreaker(failure_threshold=1, cooldown=10.0)
    breaker.call(failing_fn)                # OPEN
    # 10s elapsed
    breaker.call(failing_fn)                # HALF-OPEN probe FAILS →
                                            # OPEN, cooldown restarts
    # Calls in the next 10s raise CircuitOpenError.

Modeled on the classic "multi-PSP failover" pattern. Related
LeetCode complex-state-machine problems: #1396 (Underground System),
#460 (LFU Cache), #146 (LRU Cache), #2502 (Memory Allocator).

This package ports the problem as a tiered learning ladder. Each tier
builds on the state machine of the previous one.

Tier 1: SimpleCircuitBreaker       — basic three-state machine; consecutive-failure counter.
Tier 2: WindowedCircuitBreaker     — sliding-window failure rate + hysteresis.
Tier 3: DoubleCircuitBreaker       — primary + secondary with routing and retry budget.
Tier 4: DistributedCircuitBreaker  — breaker state shared across the fleet via Redis.

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import time
from collections.abc import Callable
from enum import Enum
from typing import Any


class State(Enum):
    """The three states of a circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when ``call`` is invoked while the breaker is Open.

    The wrapped callable is NOT invoked — the breaker rejects the
    call fast, sparing the dependency further load while it recovers.
    """


class SimpleCircuitBreaker:
    """Tier 1: three-state breaker with consecutive-failure counter.

    The simplest circuit breaker: count consecutive failures; open
    when the count crosses a threshold; wait for a fixed cooldown;
    let one probe call through to test recovery; close on probe
    success, reopen on probe failure.

    Input:
        __init__(failure_threshold: int, cooldown: float,
                 clock: Callable[[], float] = time.monotonic)
        call(fn: Callable[[], T]) -> T

    Output:
        call returns fn's result on success. Raises CircuitOpenError
        when the breaker is Open (without invoking fn). Re-raises any
        exception fn raises, after updating internal state.

    Example 1 (open after N consecutive failures):
        b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10)
        for _ in range(3):
            try: b.call(failing_fn)
            except Exception: pass
        # b is now OPEN.
        try: b.call(any_fn)
        except CircuitOpenError: pass         # any_fn was NOT invoked.

    Example 2 (Half-Open probe success → Closed):
        # b is OPEN; cooldown elapses on the injected clock.
        result = b.call(succeeding_fn)        # HALF-OPEN probe; success;
                                              # b transitions back to CLOSED.
        # The next call(succeeding_fn) goes through normally.

    Example 3 (Half-Open probe failure → Open with restarted cooldown):
        # b is OPEN; cooldown elapses.
        try: b.call(failing_fn)               # HALF-OPEN probe; FAILS.
        except Exception: pass
        # b is OPEN again; cooldown restarted; another full cooldown
        # must elapse before another probe.

    Standard library:
        enum.Enum — for the three states (clarity over ``str``).
        time.monotonic — default clock; immune to wall-clock jumps.
        collections.abc.Callable — for the clock and fn parameters.
        typing.Any — call() returns whatever the wrapped fn returns (a
            breaker is a pass-through wrapper), so its result type is Any.

    Pseudocode:
        data:
            state              — CLOSED | OPEN | HALF_OPEN
            failure_count      — int (reset on success in CLOSED)
            opened_at          — float (when we last entered OPEN)
            failure_threshold  — int (config)
            cooldown           — float seconds (config)
            clock              — () -> float

        call(fn):
            if state == OPEN:
                if clock() - opened_at >= cooldown:
                    state = HALF_OPEN          # promote; one probe allowed
                else:
                    raise CircuitOpenError()    # fail fast

            # Now state is CLOSED or HALF_OPEN; invoke fn.
            try:
                result = fn()
            except Exception as e:
                # Failure path.
                if state == HALF_OPEN:
                    state = OPEN
                    opened_at = clock()         # restart cooldown
                else:
                    failure_count += 1
                    if failure_count >= failure_threshold:
                        state = OPEN
                        opened_at = clock()
                raise

            # Success path.
            if state == HALF_OPEN:
                state = CLOSED
                failure_count = 0
            else:
                failure_count = 0               # consecutive-failures model
            return result

    Why inject the clock:
        Tests must verify cooldown behavior without ``time.sleep``.
        With an injected clock, the test holds a mutable cell
        (``clock = [0.0]``) and "advances time" by writing to it
        (``clock[0] += 10``). Every state transition becomes
        deterministic.

    Why count CONSECUTIVE failures, not total:
        A single transient failure followed by 100 successes shouldn't
        trip the breaker. Consecutive-failures requires the
        dependency to be reliably failing right now. Tier 2 replaces
        this with a sliding-window failure rate, which is even better
        at distinguishing "flaky" from "broken."

    Complexity:
        Storage: O(1) — a fixed handful of fields.
        call(): O(1) — one clock read, one state check, one fn call.
    """

    def __init__(
        self,
        *,
        failure_threshold: int,
        cooldown: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown
        self._clock = clock
        # A fresh breaker starts CLOSED (passing calls through).
        self._state = State.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0

    def call(self, fn: Callable[[], Any]) -> Any:
        # OPEN: fail fast — unless the cooldown has elapsed, in which case
        # promote to HALF_OPEN and let this one call through as a probe.
        if self._state is State.OPEN:
            if self._clock() - self._opened_at >= self._cooldown:
                self._state = State.HALF_OPEN
            else:
                raise CircuitOpenError()

        # State is now CLOSED or HALF_OPEN — invoke the wrapped callable.
        try:
            result = fn()
        except Exception:
            if self._state is State.HALF_OPEN:
                # The probe failed: back to OPEN and restart the cooldown.
                self._state = State.OPEN
                self._opened_at = self._clock()
            else:
                # CLOSED: count consecutive failures; trip at the threshold.
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._state = State.OPEN
                    self._opened_at = self._clock()
            raise

        # Success: a probe success closes the breaker; any success resets
        # the consecutive-failure counter.
        if self._state is State.HALF_OPEN:
            self._state = State.CLOSED
        self._failure_count = 0
        return result
