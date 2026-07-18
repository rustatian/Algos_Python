# ID Allocator

Hand out integer IDs from a fixed range `[0, max_id)` and reclaim them on
release, so a freed ID can be allocated again. Four tiers trade memory,
allocate-speed, ordering, and concurrency against one another — all behind
the same `allocate` / `release` contract.

Modeled on LeetCode #1845 (Seat Reservation Manager) and the classic
"ID / range allocator" interview question.

## Problem

- `allocate() -> int | None` — return an ID not currently in use, or
  `None` once every ID in `[0, max_id)` is taken.
- `release(id) -> None` — return a previously allocated ID to the pool,
  making it available again.

The caller only releases IDs it actually holds; validating the argument is
out of scope (a learning exercise).

## Tiers

| Tier | Class | Data structure | The lesson |
|------|-------|----------------|------------|
| 1 | `Allocator` | freelist (deque) + bump counter | O(1) allocate & release; lazy init, no O(n) pre-fill |
| 2 | `BitmapAllocator` | one packed bit per ID | ~n/8 bytes of state; O(n) scan for the lowest free ID |
| 3 | `SegmentTreeAllocator` | segment tree of AND-bits | O(log n) descent to a non-full child; lowest-ID-first |
| 4 | `ThreadSafeAllocator` | sharded Tier-3 trees + locks | different shards never contend; ordering is shard-local |

All four expose the same entry point:

```python
a = Allocator(max_id)
a.allocate()    # -> int | None
a.release(id)
```

Each tier answers the previous one's weak spot. Tier 1 is O(1) but spends a
set tracking in-use IDs and hands them out in no useful order. Tier 2 packs
all state into a bit array and returns the *lowest* free ID — at the cost
of an O(n) scan to find it. Tier 3 layers an AND-summary tree over those
bits (a node is set only when its whole range is allocated), so `allocate`
descends toward the first non-full child and reaches the lowest free ID in
O(log n). Tier 4 splits the range into independent shards, each its own
Tier-3 tree under its own lock; the price is a relaxed contract — allocate
returns the lowest ID *within a shard*, not the global minimum.

## Distributed extension — the system-design follow-up

Tier 4 in this repo (`ThreadSafeAllocator`) is **single-machine concurrent**
— shards live in one process, behind one mutex each. The system-design
follow-up takes the next step: *what does this look like across data
centers?* The architectural question shifts from "how do we serialize
concurrent allocates?" to "how do we allocate unique IDs at low latency,
on N independent servers, without a global lock?"

### Opener — clarifying questions

- **Range size?** Bounded `[0, max_id)` (the LeetCode contract), or
  unbounded / 64-bit IDs (the production reality)? Bounded changes the
  whole story (you'd shard the range); unbounded enables Snowflake-style.
- **Need to release?** If IDs are never reused (database PKs, request
  IDs, file inode-style), the design is dramatically simpler — no
  reclamation. If they must be reusable (seat reservation, port
  allocation), every shard needs a release path.
- **Ordering constraint?** Strictly increasing IDs, lowest-free-first,
  or any-unique? Strictly increasing breaks under partition; lowest-free
  requires coordination; any-unique is the cheapest.
- **ID density required?** Compact IDs (0, 1, 2, ...) or sparse (random
  64-bit values acceptable)? Compact requires reclamation tracking;
  sparse allows random generation.
- **Latency target?** Local-only (each server allocates without a
  network call), or okay with a coordinator round-trip?
- **Multi-region?** Cross-region uniqueness without cross-region calls?

Assumed for the design below: unbounded 64-bit IDs, no release (IDs are
never reused), any-unique ordering, sub-millisecond latency, multi-region,
~1M IDs/sec aggregate across the fleet.

### Three viable patterns

| Pattern | How it works | Trade-off |
|---------|--------------|-----------|
| **Snowflake** (Twitter, Discord, Sony) | encode `(timestamp, machine_id, sequence_within_ms)` into one 64-bit int — no coordination ever | strictly time-ordered, no reuse, requires unique `machine_id` per process; clock skew is the failure mode |
| **Sharded range pre-allocation** | each server owns IDs `≡ shard_id (mod N)` — server `k` allocates from `{k, k+N, k+2N, ...}` | trivially collision-free, lossy on shard death (its IDs are gone), needs static N |
| **Coordinator lease** | each server requests a block of K IDs from ZooKeeper / etcd / a SQL `RETURNING` query; allocates locally from the block; requests another block when low | exact, supports reuse, central component is the bottleneck and SPOF |

The standard answer for "distributed unique IDs at scale" is **Snowflake**
— it dominates because it needs no coordination at allocation time.

### Block diagram (Snowflake variant)

```
                  ┌──────────┐
   client ──────► │  app srv │  generate ID locally
                  │  (k=42)  │       │
                  └──────────┘       │
                                     ▼
                         ┌────────────────────────┐
                         │  Snowflake ID encoder  │
                         │  in-process, lock-free │
                         └───────────┬────────────┘
                                     │
                                     ▼
                          ID = (ts << 22)
                              | (machine_id << 12)
                              | sequence_within_ms

   ┌─────────────────┐
   │ ZooKeeper /     │  one-time, on process startup:
   │ etcd            │  assign a unique machine_id (0..1023)
   │                 │  to each server in the fleet
   └─────────────────┘
```

Two components:

- **Snowflake encoder** runs in-process on every app server. It composes
  a 64-bit ID from three fields: a 41-bit millisecond timestamp (custom
  epoch), 10 bits of machine ID, 12 bits of per-millisecond sequence.
  Capacity: 4096 IDs per machine per millisecond, or ~4M/sec/machine.
- **Coordinator** (ZooKeeper, etcd, or a database row with a
  monotonically allocated counter) — used **once per process** at
  startup to assign a unique `machine_id`. After that, the app server
  generates IDs without any network call.

### The ID layout

```
| timestamp_ms (41 bits)         | machine_id (10) | seq (12) |
└──────────────────────────────────┴─────────────────┴──────────┘
   ms since custom epoch            0..1023            0..4095

  total: 63 bits (sign bit zero — fits a signed 64-bit int)
```

Properties that fall out of this layout:

- **Roughly time-ordered.** IDs from later milliseconds are larger.
  Useful for range scans on the primary key — recent rows are
  contiguous.
- **No coordination at allocation.** The only shared state is the
  `machine_id`, assigned once at startup. Allocation is a local int
  shift + bit-or — ~5 ns per ID.
- **No reuse.** A released ID can't be re-issued. Drop the requirement
  if you can; if you can't, Snowflake is the wrong pattern.
- **Bounded by clock.** With a 41-bit ms field and a 1970-epoch, the
  encoder overflows in ~69 years. With a custom epoch (e.g., 2020-01-01),
  you get another ~50 years. Plenty.

### Allocation pseudocode

```
data:
    machine_id   — assigned at startup, unique across the fleet.
    last_ms      — millisecond of the last ID issued.
    seq          — sequence counter within the current ms.
    lock         — local mutex protecting (last_ms, seq).

allocate():
    with lock:
        now = current_time_ms()
        if now == last_ms:
            seq = (seq + 1) AND 0xFFF        # 12-bit wraparound
            if seq == 0:
                # exhausted this ms; busy-wait for next ms
                while now == last_ms:
                    now = current_time_ms()
        elif now > last_ms:
            seq = 0
        else:
            # clock went backwards (NTP correction, leap second).
            # Refuse to issue an ID rather than risk a collision.
            raise ClockSkewError(now, last_ms)
        last_ms = now
        return (now - EPOCH) << 22 | machine_id << 12 | seq
```

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Clock goes backwards (NTP correction) | could issue a duplicate ID if `now < last_ms` | refuse to issue until `now ≥ last_ms` (raise / sleep / shed); never silently rewind |
| Two servers share a `machine_id` (configuration error) | hidden duplicates across the fleet — disastrous | ZooKeeper / etcd ephemeral node lease per machine_id; service refuses to start without a lease |
| Sequence exhausted in one ms (>4096 IDs in <1ms) | encoder blocks for the rest of the ms | bump the seq field to 13–14 bits if hot, or shard hot keys across machine_ids |
| Process restart | `last_ms` resets; if the previous process issued IDs in the same ms, sequence numbers restart from 0 — possible collision within that ms | persist `last_ms` to disk on shutdown, or block briefly after startup until `now > saved_last_ms` |
| Multi-region uniqueness | two regions independently issue with the same `machine_id` | partition the `machine_id` namespace by region (e.g., top 3 bits = region) |

### Scaling levers

- **Bigger machine_id field for huge fleets** — 10 bits = 1024 machines.
  For more, take bits from the sequence field (trade peak/sec/machine
  for fleet size).
- **Multi-region**: top bits of `machine_id` = region; each region's
  Snowflake range disjoint by construction.
- **Higher per-ms throughput**: shard hot keys across multiple
  `machine_id`s on the same physical box. Different `machine_id` values
  give disjoint IDs even in the same millisecond.
- **Persistent `last_ms`** — survives process restarts. WAL or fsync'd
  file per second.

### When NOT to use Snowflake

- **Bounded `[0, max_id)` and IDs must be reused** — Snowflake doesn't
  reclaim. Use sharded range pre-allocation: each server owns the
  arithmetic-progression class `{shard_id, shard_id+N, ...}`, releases
  return locally, exhaustion can either fail or attempt a steal from
  another shard's free list (which adds coordination).
- **Exact lowest-free-first** — Snowflake is time-ordered, not
  lowest-first. Use a coordinator with a SQL `INSERT ... RETURNING id`
  on a SERIAL column; supports release via `DELETE`.
- **You need a global counter (visible, ordinal)** — Snowflake IDs are
  not consecutive. Use a coordinator-backed counter; trade latency for
  consecutiveness.

### What this design defers

- **Release / reuse semantics.** Snowflake doesn't model reuse; pure
  generation. If a returned ID must become re-issuable, layer a
  reclaim store (Redis set of released IDs, polled by allocators).
- **Strict global ordering across regions.** IDs are roughly
  time-ordered *within* a region but not *across* regions (clock skew).
  Cross-region consumers must treat them as unordered.
- **Auditability.** Issued IDs are not logged centrally. If the system
  needs "who issued this ID" for forensics, add per-allocator append-only
  logs.

### Simulation → production mapping

The single-process `ThreadSafeAllocator` mirrors the production
architecture's *concurrency primitives* — a fleet of independent shards,
each lock-free relative to the others — but uses a different ID
*structure*. The mapping is conceptual, not 1:1:

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `ThreadSafeAllocator` shards | one app server's local Snowflake encoder |
| per-shard `Lock` | per-machine `(last_ms, seq)` lock |
| `random.choice(shards)` dispatch | client routed to any app server (no affinity needed) |
| in-process exhaustion → `None` | per-machine sequence exhausted → busy-wait for next ms |
| (none — single-process) | ZooKeeper / etcd lease for machine_id assignment |

The shape transfers — N independent allocators, no inter-allocator
coordination at request time — even though the encoding swaps
"compact reused IDs" for "time-encoded 64-bit IDs."

## Running the tests

```sh
uv run pytest Algorithms/id_allocator/tests/ -q
```
