# Double Circuit Breaker (multi-server)

A circuit breaker wraps a fallible operation and stops calling it once
failures accumulate, giving the failing dependency time to recover. The
**double** variant composes two breakers — primary and secondary — with
routing logic: try primary; on Open or failure, route to secondary. This
is the multi-PSP failover pattern (Stripe ↔ Adyen) a fintech
payments team uses in production.

Modeled on the classic "multi-PSP failover" interview question
(NEW 2025). Related LeetCode complex-state-machine references:
#1396 (Underground System), #460 (LFU Cache), #146 (LRU Cache),
#2502 (Memory Allocator).

## Problem

One operation:

```
call(fn) -> fn's return value
```

The breaker wraps `fn`. It is in one of three states:

- **Closed** — calls pass through; failures are counted.
- **Open** — calls fail-fast with `CircuitOpenError` (fn is NOT invoked);
  a cooldown timer is running.
- **Half-Open** — one probe call is allowed; success transitions to
  Closed, failure transitions back to Open with the cooldown restarted.

The state machine, drawn as edges:

```
   CLOSED  ──failures ≥ threshold──►  OPEN
     ▲                                  │
     │ probe success           cooldown │ elapsed
     │                                  ▼
     │ ◄──────────────────────  HALF-OPEN
                probe failure (back to OPEN; cooldown restarts)
```

Production realities the interview probes:

- **Sliding-window failure rate**, not total count — transient blips
  shouldn't permanently trip the breaker.
- **Hysteresis** — require *K consecutive successes* in Half-Open
  before fully closing; prevents flapping.
- **Bounded retry budget** per request — don't retry indefinitely.
- **Independent breakers** — on failover from primary to secondary,
  don't reset primary's state; let it recover on its own clock.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleCircuitBreaker` | three-state machine; consecutive-failure counter | the state machine itself — Closed/Open/Half-Open and the probe |
| 2 | `WindowedCircuitBreaker` | sliding-window failure rate + hysteresis (K successes) | failure *rate* not count; hysteresis prevents flapping |
| 3 | `DoubleCircuitBreaker` | primary + secondary; routing + bounded retry budget | failover composition — independent breakers, retry budget, no cross-coupling |
| 4 | `DistributedCircuitBreaker` | breaker state shared across the fleet via Redis | the system-design follow-up — coordinated breaker state, no thundering-herd recovery |

All tiers expose the same `call` surface:

```python
b = SimpleCircuitBreaker(failure_threshold=3, cooldown=10.0)
result = b.call(some_function)        # returns result, or raises
```

Each tier answers the previous one's weak spot. Tier 1's
consecutive-failure counter is fooled by interleaved successes ("flaky"
looks fine); Tier 2's sliding window measures actual failure rate. Tier
2's single breaker can't handle "what if my dependency is permanently
down" — Tier 3 routes to a secondary when the primary is unhealthy.
Tier 3's breaker state lives per-process; under thousands of app
servers all tripping their own breakers independently, recovery
generates a thundering herd. Tier 4 coordinates the state in Redis so
the whole fleet decides to recover together.

## Running the tests

```sh
uv run pytest Algorithms/double_circuit_breaker/tests/ -q
```
