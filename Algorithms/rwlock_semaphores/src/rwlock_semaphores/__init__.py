"""Reader-Writer Lock with semaphores — tiered learning port.

Public classes:
    WriterPriorityRWLock  — Tier 1a: writers block new readers; readers can starve.
    ReaderPriorityRWLock  — Tier 1b: readers proceed freely; writers can starve.

Additional tiers (FairRWLock, DistributedRWLock) will land in this
namespace as they are added.
"""

from rwlock_semaphores.rwlock import (
    ReaderPriorityRWLock,
    WriterPriorityRWLock,
)

__all__ = ["ReaderPriorityRWLock", "WriterPriorityRWLock"]
