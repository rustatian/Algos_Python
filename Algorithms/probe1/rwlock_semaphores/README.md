# Reader-Writer Lock with semaphores

A synchronization primitive that allows **multiple concurrent readers**
but only **one writer** at a time, with strict mutual exclusion between
the two groups. Either many readers run together OR a single writer runs
alone; never both.

Modeled on the classic "reader-writer lock with semaphores"
interview question — *"the standard MT question they ask everybody"*
per Blind. The classical algorithm comes from Courtois, Heymans, and
Parnas (1971), "Concurrent Control with Readers and Writers."

Related LeetCode concurrency drills: #1226 (Dining Philosophers),
#1117 (Building H2O), #1188 (Bounded Blocking Queue), #1114 (Print in
Order).

## Problem

Two paired operations:

```
acquire_read()  / release_read()    — the caller is a "reader"
acquire_write() / release_write()   — the caller is a "writer"
```

Invariants:

- A writer is alone — no other reader or writer is inside while a
  writer holds the lock.
- Many readers may share the lock simultaneously, but no writer is
  inside while any reader holds it.
- Acquire and release must be paired by the same caller; the lock is
  **not reentrant** — a thread holding the read lock that calls
  `acquire_read()` again will deadlock.

**Lock upgrade (read → write) is generally disallowed.** The textbook
fix is "release read, then acquire write," but that opens a state-gap
where the protected resource can change between the release and the
acquire. The recommended pattern is to acquire the write lock from the
start if there's any chance of writing.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1a  | `WriterPriorityRWLock` | adds a reader-gate that writers close while queued | the "second readers-writers problem" — writers preempt new readers; readers can starve |
| 1b  | `ReaderPriorityRWLock` | two semaphores + reader counter (no gate) | the classical "first readers-writers problem" — readers can starve writers |
| 3   | `FairRWLock` | FIFO waiter queue via `Condition` | the "third readers-writers problem" — neither side starves, requests served in arrival order |
| 4   | `DistributedRWLock` | Redis / ZooKeeper-backed cross-machine lock | the system-design follow-up — distributed RW lock with leases and fencing tokens |

Tiers 1a and 1b are **siblings**, not a strict refinement. Both are
valid "Tier 1" answers to "implement a basic RW lock," differing only
in which side can starve. The classical textbook ladder presents them
as Tier 1 and Tier 2 (Courtois et al.'s "first" and "second" readers-
writers problems); this port treats them as fairness variants at the
same conceptual level, with Tier 3 (FIFO) being the structural fix.

All four expose the same surface:

```python
lock = WriterPriorityRWLock()
lock.acquire_read();  ...; lock.release_read()
lock.acquire_write(); ...; lock.release_write()
```

The fairness story:

- **Tier 1a (writer-priority)** — once any writer is queued, new
  readers wait until the writer has finished. Existing readers finish
  first, then the writer enters; writers chain through until none are
  left. **Readers can starve** under continuous writer arrivals.
- **Tier 1b (reader-priority)** — readers proceed as long as another
  reader holds the lock; they never wait for queued writers. **Writers
  can starve** under continuous reader arrivals.
- **Tier 3 (fair, FIFO)** — both sides queue in arrival order; neither
  starves. The cost is queue overhead and slightly less reader
  parallelism (the queue serializes the *queue manipulation*, not the
  reads themselves).
- **Tier 4 (distributed)** — leaves the single machine: leases bounded
  by TTL, fencing tokens to prevent stale-holder writes, and consensus
  via ZooKeeper or Redis Redlock.

## Tier 4 architecture — the system-design follow-up

The first three tiers all live in one process — they coordinate
threads that share memory. The system-design follow-up takes the
next step: *what does an RW lock look like when the resource lives on
disk shared across N machines, and the lock holders are different
processes on different boxes?* The architectural question shifts from
"how do I prevent two threads from interleaving" to "how do I prevent
two crashed processes from corrupting a shared file."

Distributed RW locks are fundamentally harder than in-process ones —
not because the algorithm is different, but because **lock holders
can die without releasing** and **the network can partition**. Both
are impossible inside one process; both must be handled in a
distributed system.

### Opener — clarifying questions

- **What is the resource?** A shared file? A row in a sharded DB? A
  blob in S3? Drives the storage shape and where the lock state
  lives.
- **Read vs write rate?** If reads dominate, optimize for cheap
  concurrent reads (the original RW lock motivation); if writes
  dominate, a simple exclusive lock is fine.
- **How long can a lock be held?** Milliseconds (RPC scope) or
  seconds-to-minutes (a long-running job)? Drives lease TTL and
  renewal semantics.
- **What happens if the holder dies?** Auto-release after lease
  expires (eventual recovery) or manual recovery (operator
  intervention)? Almost always the former.
- **Tolerance for split-brain?** If the network partitions, can two
  writers both believe they hold the lock? Drives the consensus
  requirements (Redlock vs. ZooKeeper vs. plain Redis).
- **Reentrancy?** A holder calling acquire again — allowed?
  In-process locks usually no; distributed locks almost always no
  (the protocol gets brittle).

Assumed for the design below: shared file in object storage, mixed
read/write, lock holders are short-lived RPC handlers (5-30 sec hold
time), auto-release via lease TTL, ZooKeeper-strength consistency
(no split-brain), no reentrancy.

### Block diagram

```
   client A ─►acquire_read───┐
                             ▼
   client B ─►acquire_write─►│
                             │   ┌──────────────────────────┐
                             ├──►│  RW lock service         │
   client C ─►acquire_read───┘   │  (ZooKeeper-backed or    │
                                 │   Redis with Lua + leases)│
                                 │                          │
                                 │   per-resource znode:    │
                                 │     /locks/<resource>/   │
                                 │       reader_count: int  │
                                 │       writer_holder: ID  │
                                 │       fencing_token: N   │
                                 │       lease_ttl: epoch   │
                                 └──────────┬───────────────┘
                                            │
                                  fencing   │
                                  token ─►  ▼
                                 ┌───────────────────────┐
                                 │  resource service     │
                                 │  (rejects writes      │
                                 │   with stale token)   │
                                 └───────────────────────┘
```

Three components:

- **Clients** — application servers. Acquire/release RW locks
  through the lock service. Receive a **fencing token** at acquire
  time and **must include it** in every operation on the resource.
- **RW lock service** — sharded ZooKeeper (or Redis+Lua) cluster.
  Owns the lock state per resource: reader count, writer holder ID,
  fencing token (monotonically incremented), lease TTL.
- **Resource service** — the storage that actually holds the data
  (blob store, database, etc.). Inspects the fencing token on every
  write; rejects writes whose token is older than the latest one it
  has seen for that resource.

### API surface

```http
POST /locks/{resource}/acquire_read
  body:     { "client_id": "uuid", "lease_seconds": 30 }
  response: 200 {
    "fencing_token":   147,
    "lease_expires":   "2026-05-23T12:30:00Z"
  }
  Or:       409 { "reason": "writer_active", "retry_after_ms": 200 }

POST /locks/{resource}/acquire_write
  body:     { "client_id": "uuid", "lease_seconds": 30 }
  response: 200 { "fencing_token": 148, "lease_expires": "..." }
  Or:       409 { "reason": "readers_active|writer_active", "retry_after_ms": 200 }

POST /locks/{resource}/renew
  body:     { "client_id": "uuid", "fencing_token": 147 }
  response: 200 { "lease_expires": "..." }
  Or:       410 { "reason": "lease_expired" }    # too late — re-acquire

POST /locks/{resource}/release
  body:     { "client_id": "uuid", "fencing_token": 147 }
  response: 204
  Idempotent — releasing an already-expired/released lock is a no-op.

# Every write to the resource carries the fencing token:
PUT /resource/{id}?fencing_token=148
  body:     { ... }
  response: 200 (token accepted)
  Or:       409 { "reason": "stale_fencing_token", "current_token": 152 }
```

Four endpoints map cleanly to in-process semantics: acquire ↔
`acquire_read/write`, release ↔ `release_*`, renew (new in
distributed — keeps the lease alive for long operations).

### The fencing token — the single critical insight

**The lease TTL alone is not enough.** Consider this sequence:

```
Time  Event
----  ---------------------------------------------------
0     Client A: acquire_write succeeds, lease until t=30, token=147
2     Client A: long GC pause begins
30    Lease expires; lock service reassigns
30    Client B: acquire_write succeeds, lease until t=60, token=148
35    Client A: GC pause ends; A still thinks it holds the lock
35    Client A: writes to resource with stale token=147     ← DISASTER
```

Without fencing tokens, A's stale write corrupts the resource —
both A and B's writes are accepted. With fencing tokens, the
resource service tracks the **latest token seen** for each resource
and rejects writes with smaller tokens. A's write at t=35 carries
token=147; the resource service has already seen token=148; A's
write is rejected.

```
ZooKeeper / Redis state:
    /locks/<resource>/
        reader_count:   int
        writer_holder:  client_id    (or null)
        fencing_token:  monotonically increasing int
        lease_expires:  epoch_ms

Resource service state per resource:
    last_seen_fencing_token: int
```

Every successful acquire (read or write) bumps `fencing_token`. Every
write to the resource carries that token; the resource service
maintains `max(seen)` and rejects writes with `token < max(seen)`.
Stale-holder writes are caught at the storage layer, not the lock
layer — defense in depth.

This is the same insight Martin Kleppmann famously used to argue
"Redlock is not safe for fencing" (2016) — the fencing token doesn't
need to come from a consensus protocol; ANY monotonically increasing
ID works, as long as the resource service checks it.

### Renewal vs. re-acquisition

For long-running work, lease TTLs are too short to hold from start to
finish. Two patterns:

| Pattern | When to use | Cost |
|---------|-------------|------|
| **Renew** | The holder is alive but the work isn't done | one RPC per renewal interval (~lease/3) |
| **Re-acquire** | The holder lost track of the lease (GC pause, network blip) | full acquire cost + risk of contention |

Renewal is cheap; re-acquisition is correct-by-default. Pick renewal
for the happy path and treat `lease_expired` as a re-acquire trigger.

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Holder process crash | lease still ticking; resource unavailable until TTL expires | keep lease TTLs short (5-30s); auto-recover after expiry |
| Holder GC pause > lease TTL | stale-holder writes possible | **fencing tokens** — resource service rejects stale writes |
| Lock service partition (clients can't reach it) | new acquires fail; existing holders carry on | clients fail-closed on acquire errors; existing leases keep working until TTL |
| ZooKeeper / Redis cluster split-brain | two clients could simultaneously hold the same lock | ZooKeeper uses ZAB consensus → split-brain prevented; plain Redis with Redlock has known weaknesses (Kleppmann 2016) |
| Clock skew across lock-service nodes | TTLs compute differently | use a single monotonic clock at the lock service master; clients use *relative* expiry, not absolute timestamps |
| Renewal request lost | holder believes lease is renewed but service thinks it expired | client checks the response; on transport error, immediately re-renew |
| Resource service forgets `last_seen_fencing_token` | fencing protection lost | persist it durably with the resource state; on resource service restart, reload before accepting writes |

### Scaling levers

- **Shard locks by resource ID.** Lock service is sharded; resources
  live on different shards. Adding shards is consistent-hashing rebalance.
- **Read-only fast path.** A common optimization: skip the lock
  service entirely for reads. The resource service's MVCC store
  returns a versioned read; clients see consistent snapshots without
  acquiring read locks. Suitable when readers don't need
  exclusivity — only writers do. (This bends the RW lock semantics
  toward optimistic concurrency.)
- **Lease batching.** A single client holding many locks renews them
  in batches — one RPC for N renewals.
- **Pre-acquired pool.** Clients hold a small pool of unused locks
  and grab from the pool when needed. Trades fairness for latency.

### What this design defers

- **Reentrancy across machines.** A holder calling acquire again
  with the same `client_id` could be modeled, but it's a frequent
  source of distributed-system bugs (the holder may not realize
  they already hold it across services). Standard advice: don't.
- **Priority inheritance.** Single-machine RW locks can boost a
  waiting writer's priority; distributed locks have no equivalent.
- **Conditional acquire** ("acquire if it hasn't changed since
  version N") — that's optimistic concurrency, not a lock; layer
  it on top of fencing tokens.
- **Cross-region locking.** A lock spanning regions pays
  cross-ocean RTT for every operation. Almost always wrong; pin
  locks to a single region and replicate the resource instead.

### Simulation → production mapping

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `threading.Lock` (resource) | ZooKeeper znode with sequential children, OR Redis+Lua exclusive key |
| `threading.Lock` (count_lock) | atomic CAS on the lock service's reader-count counter |
| `reader_count` int | a field in the znode / Redis hash |
| in-process method call | RPC to the lock service |
| (none — process always alive) | lease TTL + auto-release on expiry |
| (none — process always alive) | fencing token + resource-service-side rejection |
| crash-induced deadlock | impossible at process scope; visible across machines |

The core algorithm — counter-protected resource gate — survives.
What's added is *liveness against death*: leases for "the holder is
gone, recover," fencing for "the holder is gone but doesn't know it,
prevent damage."

## Running the tests

```sh
uv run pytest Algorithms/rwlock_semaphores/tests/ -q
```
