"""API Design for Long-Running Requests — tiered learning port.

Public API:
    SyncAPI          — Tier 1: do the work inline (the baseline/problem).
    AsyncJobAPI      — Tier 2: submit -> poll; enqueue, return PENDING, worker.
    IdempotentJobAPI — Tier 3: Tier 2 + request_id idempotency on submit.
    JobRecord, JobStatus — the poll-able job state and its lifecycle.

Tier 4 (DistributedJobAPI) is an architecture discussion, not code — see
README.md.
"""

from api_design.api_design import (
    AsyncJobAPI,
    IdempotentJobAPI,
    JobRecord,
    JobStatus,
    SyncAPI,
)

__all__ = [
    "SyncAPI",
    "AsyncJobAPI",
    "IdempotentJobAPI",
    "JobRecord",
    "JobStatus",
]
