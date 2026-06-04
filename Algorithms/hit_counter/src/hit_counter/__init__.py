"""Hit Counter — LeetCode #362, tiered learning port.

Public classes:
    DequeCounter       — Tier 1: deque of raw timestamps, lazy purge on read.
    BucketCounter      — Tier 2: circular array of 300 second-buckets.
    ConcurrentCounter  — Tier 3: bucketed + per-bucket lock for parallel hit().
    DistributedCounter — Tier 4: N shards + scatter-gather aggregator.
"""

from hit_counter.hit_counter import (
    BucketCounter,
    ConcurrentCounter,
    DequeCounter,
    DistributedCounter,
)

__all__ = [
    "BucketCounter",
    "ConcurrentCounter",
    "DequeCounter",
    "DistributedCounter",
]
