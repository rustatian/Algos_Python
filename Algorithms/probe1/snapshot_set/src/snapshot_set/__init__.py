"""SnapshotSet — snapshot-isolated set, tiered learning port.

A generic set whose ``snapshot()`` returns a version-stable view: the
snapshot reflects the set's contents at the moment it was taken, immune to
later live mutations. The set-flavored sibling of LeetCode #1146 (Snapshot
Array) and #981 (Time-Based KV Store).

Public classes:
    SimpleSnapshotSet — Tier 1: copy-on-snapshot (full frozenset copy).
    SimpleSnapshot    —         the frozen view it returns.
    CoWSnapshotSet    — Tier 2: copy-on-write versioning (#1146 technique).
    CoWSnapshot       —         the version-pinned view it returns.
    GCSnapshotSet     — Tier 3: Tier 2 plus refcounted GC of old versions.
    GCSnapshot        —         the releasable, version-pinned view it returns.

Tier 4 (DistributedSnapshotSet) is an architecture discussion, not code —
see README.md.
"""

from snapshot_set.snapshot_set import (
    CoWSnapshot,
    CoWSnapshotSet,
    GCSnapshot,
    GCSnapshotSet,
    SimpleSnapshot,
    SimpleSnapshotSet,
)

__all__ = [
    "SimpleSnapshotSet",
    "SimpleSnapshot",
    "CoWSnapshotSet",
    "CoWSnapshot",
    "GCSnapshotSet",
    "GCSnapshot",
]
