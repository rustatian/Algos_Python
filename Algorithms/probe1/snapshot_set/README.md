# SnapshotSet — a snapshot-isolated set

A generic set whose `snapshot()` returns a **version-stable** view: the
snapshot reflects the set's contents at the moment it was taken, immune to
any later mutation of the live set. The set-flavored sibling of LeetCode
#1146 (Snapshot Array) — #1146 versions an indexed array of values; we
version membership of a set.

Modeled on the classic "generic snapshot-isolated collection" question.
Related LeetCode references: #1146 (Snapshot Array — the exact versioning
technique), #981 (Time-Based KV Store — versioned reads), #460 (LFU Cache
— refcount bookkeeping).

## Problem

The live set carries ordinary operations; the snapshot is the new idea:

- `add(x)` / `remove(x)` — mutate the live set (both idempotent).
- `contains(x) -> bool`, `items() -> set`, `__iter__` — read the *live* set.
- `snapshot() -> Snapshot` — a handle whose `contains(x)` / `items()` /
  `iterator()` report the live set's contents **as they were when
  `snapshot()` was called**.

The single guarantee that defines the problem: a snapshot is **isolated**
from later mutation.

```python
s = SimpleSnapshotSet()
s.add("a")
s.add("b")
snap = s.snapshot()  # captures {a, b}
s.add("c")
s.remove("a")  # live set moves on
sorted(s.items())  # -> ["b", "c"]   (live)
sorted(snap.items())  # -> ["a", "b"]   (frozen)
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleSnapshotSet` | copy-on-snapshot — `snapshot()` deep-copies into a `frozenset` | the baseline — isolation by a full O(N) copy; trivially correct |
| 2 | `CoWSnapshotSet` | copy-on-write versioning — per-element `(version, present)` history, binary-searched | snapshot is an O(1) captured integer; reads look up membership-as-of-a-version (the #1146 technique) |
| 3 | `GCSnapshotSet` | Tier 2 + refcounted GC of unreferenced versions | bounding memory — released snapshots let shadowed history be reclaimed |
| 4 | `DistributedSnapshotSet` | MVCC across nodes | the system-design follow-up — distributed multi-version concurrency control |

All tiers share the live-set surface and the isolation guarantee; what
changes is the snapshot *mechanism*.

Each tier answers the previous one's weak spot. Tier 1 copies the entire
set on every `snapshot()` — O(N) time and O(K·N) memory for K outstanding
snapshots. Tier 2 removes the copy: a global version counter plus a
per-element append-only list of `(version, present)` records. `snapshot()`
captures the current version in O(1) and bumps the counter, so no future
write can ever land at a version a snapshot holds; a snapshot read
binary-searches the element's history for the last record at or before its
version. But that history is append-only and **never shrinks** — K
snapshots over a long-lived set grow it without bound. Tier 3 fixes that
with reference counting: each live snapshot pins its version, `release()`
unpins it, and history strictly older than the oldest pinned version (the
*GC horizon*) is unreachable by any live snapshot and is reclaimed.

### Why `snapshot()` increments the version (not every write)

If every write bumped the version, the history would explode (a record per
op) and create versions no snapshot ever pinned. Bumping only on
`snapshot()` means a version number exists *because* a snapshot pinned it,
and all writes between two snapshots collapse onto one version — exactly
SnapshotArray's `snap()` semantics, and exactly what guarantees isolation.

### Why Tier 3's GC horizon is `min(pinned versions)`

A live snapshot at version `v` needs, per element, the record in effect at
`v` (the latest with `version ≤ v`); records strictly older are shadowed
and unreadable. The oldest surviving reader sets the bar — anything a
reader at `min(pins)` cannot see, no reader can see. With no live snapshots
the horizon is the current version and history collapses to what the live
set needs. Because Python has no reliable destructor, `release()` is
explicit (with a context-manager form) — mirroring how a real MVCC store
closes a read transaction to advance its GC watermark.

## Tier 4 — the system-design follow-up (distributed MVCC)

The single-machine tiers are exactly the in-process model of a distributed
multi-version store. The follow-up: *serve snapshot-isolated reads of a set
that spans many nodes, while writes continue, without locking readers
against writers.*

**Opener questions.** Read/write ratio? How long do snapshots (read
transactions) live — milliseconds (a query) or hours (a backup/analytics
scan)? Bounded element count or unbounded? Consistency: is a snapshot a
global point-in-time across all shards, or per-shard? Fail mode if the
version-GC watermark service is unreachable?

**Design sketch.**

```
   writer ─► coordinator ──assign commit version──► shard 1 (versioned store)
                  │                                  shard 2 (versioned store)
   reader ─► snapshot service ──pin read version──►  shard N (versioned store)
                  │
            version GC watermark  ◄── readers release their pinned version
```

- **Versioned storage per shard** — each element's membership keyed by
  `(element, version)`, exactly Tier 2's per-key history, persisted
  (LSM-tree / MVCC table like Postgres or a RocksDB column family).
- **A global version clock** — a monotonic commit timestamp (a Lamport/HLC
  clock or a central sequencer). `snapshot()` = "read as of version V";
  every shard answers each element's membership-as-of-V locally.
- **Snapshot = a pinned read version**, the distributed analogue of Tier 3's
  pin. A read transaction holds version V; the GC watermark = `min(V)` over
  all live read transactions; shards compact history below the watermark.
  This is precisely Postgres MVCC vacuum / a Snapshot Isolation read view.
- **Global vs per-shard snapshots.** A truly global point-in-time needs the
  version assigned by one sequencer (or a barrier) so all shards agree on
  "version V." Relaxing to per-shard snapshots removes the sequencer
  bottleneck but loses cross-shard atomicity.

**Failures.** A reader that dies without releasing pins history forever →
lease the pin with a TTL; expire abandoned read versions. A lagging shard
serves an older version → the snapshot blocks or reads a replica caught up
to V. The GC watermark service is the one piece of shared state — replicate
it; if unreachable, halt compaction (safe: history only grows) rather than
risk reclaiming a pinned version.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| global `version` counter | a sequencer / hybrid-logical-clock commit timestamp |
| per-element `(version, present)` list | an MVCC table / LSM column family, keyed by `(element, version)` |
| `snapshot()` captured version | a pinned read-transaction version (Snapshot Isolation) |
| `pins` refcount + `_gc()` | the GC watermark = `min(live read versions)`; vacuum below it |
| `release()` | committing/closing a read transaction |

## Running the tests

```sh
uv run pytest Algorithms/snapshot_set/tests/ -q
```
