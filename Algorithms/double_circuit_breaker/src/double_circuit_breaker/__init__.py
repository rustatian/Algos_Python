"""Double Circuit Breaker (multi-server) — tiered learning port.

Public classes:
    SimpleCircuitBreaker  — Tier 1: three-state machine; consecutive-failure counter.
    CircuitOpenError      — raised when the breaker is Open.

Additional tiers (WindowedCircuitBreaker, DoubleCircuitBreaker,
DistributedCircuitBreaker) will land in this namespace as they are added.
"""

from double_circuit_breaker.breaker import (
    CircuitOpenError,
    SimpleCircuitBreaker,
    State,
)

__all__ = ["CircuitOpenError", "SimpleCircuitBreaker", "State"]
