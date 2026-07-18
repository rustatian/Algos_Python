"""Producer-Consumer / Image-Processing Service — tiered learning port.

Public API:
    BoundedBlockingQueue — Tier 1: in-memory blocking queue (#1188).
    DBBackedQueue        — Tier 2: durable job rows; atomic claim.
    LeasedQueue          — Tier 3: Tier 2 + leases (recover dead workers) + retries.
    Job, JobStatus       — the job row and its lifecycle states.

Tier 4 (DistributedTaskQueue) is an architecture discussion, not code —
see README.md.
"""

from producer_consumer_service.producer_consumer_service import (
    BoundedBlockingQueue,
    DBBackedQueue,
    Job,
    JobStatus,
    LeasedQueue,
)

__all__ = [
    "BoundedBlockingQueue",
    "DBBackedQueue",
    "LeasedQueue",
    "Job",
    "JobStatus",
]
