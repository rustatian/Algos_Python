"""Event Logger — batching, fsync, group commit, tiered learning port.

Public API:
    Sink              — the durable-destination Protocol (write + fsync).
    InMemorySink      — a test/inspection sink modeling durability + fsync count.
    SimpleLogger      — Tier 1: one fsync per event.
    BatchedLogger     — Tier 2: buffer + one fsync per batch.
    GroupCommitLogger — Tier 3: concurrent appenders block until durable;
                        one fsync per coalesced batch.

Tier 4 (DistributedLog) is an architecture discussion, not code — see
README.md.
"""

from event_logger.event_logger import (
    BatchedLogger,
    GroupCommitLogger,
    InMemorySink,
    SimpleLogger,
    Sink,
)

__all__ = [
    "Sink",
    "InMemorySink",
    "SimpleLogger",
    "BatchedLogger",
    "GroupCommitLogger",
]
