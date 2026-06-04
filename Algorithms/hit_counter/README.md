# Hit Counter

Design a counter that, for any timestamp, returns the number of hits the
system received during the past 300 seconds (5 minutes). Hits arrive in
chronological order — timestamps to `hit()` are monotonically
non-decreasing — and multiple hits may share one timestamp.

Modeled on LeetCode #362 (Design Hit Counter) and the classic
"5-min / 15-min visit count" phone-screen variant. A learning exercise:
four tiers, the same `hit` / `get_hits` surface, an escalating answer to
"how do you scale this *across servers*?"

## Problem

Two operations:

```
hit(timestamp)        # record a hit at this timestamp (seconds).
get_hits(timestamp)   # return the count of hits in (timestamp - 300, timestamp].
```

The window is **half-open on the left**: a hit at exactly `t - 300` is
out (so a hit visible at `t = 5min` is no longer visible one second
later). This is the most common off-by-one on this problem.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `DequeCounter` | deque of raw timestamps, lazy purge on read | simplest right answer; amortized O(1) per hit |
| 2 | `BucketCounter` | circular array of 300 second-buckets | bucketing collapses storage from O(hits) to O(window) |
| 3 | `ConcurrentCounter` | bucketed + per-bucket lock | bucketed structure enables fine-grained locking; a deque cannot |
| 4 | `DistributedCounter` | per-shard local counters + scatter-gather aggregator | the system-design follow-up — sliding window across N servers |

All four expose the same surface:

```python
counter.hit(timestamp)
counter.get_hits(timestamp) -> int
```

Each tier answers the previous one's weak spot. Tier 1 holds every
timestamp — fine for a problem instance, ruinous under millions of hits.
Tier 2 collapses storage to a fixed array of 300 buckets, regardless of
hit volume. Tier 3 makes that array safe for concurrent `hit()` calls
via a per-bucket lock — the bucket boundaries become natural contention
boundaries. Tier 4 leaves the single machine: each server keeps a local
bucket counter, an aggregator sums their windowed counts on read, and a
durable store survives shard restarts.

The bucket structure pays off three times in this ladder: Tier 2
introduces it for bounded memory, Tier 3 uses it for fine-grained
locking, Tier 4 uses it for shard-associative aggregation. Picking the
right Tier 2 was a strategic decision that compounded downstream.

## Tier 4 architecture — the system-design follow-up

`DistributedCounter` ships as a single-process **scatter-gather
simulation** of a distributed rate counter. The class docstring covers
the in-process plumbing; this section is the high-level design write-up
— what you'd whiteboard if the interviewer says "now scale this to a
million hits per second across a fleet."

This shape differs from `file_duplicates`'s Tier 4: that one is
*recursive self-spawning jobs* (the work transitively subdivides). This
one is *scatter-gather* (hits scatter to shards by load-balancer
hashing; reads gather from every shard and sum). Different data shape,
different distributed pattern.

### Opener — the clarifying questions

- **Granularity?** Per-second or per-millisecond buckets? Per-second is
  standard; per-millisecond is 1000× the storage.
- **Window?** Just 5 minutes, or configurable (5 / 15 / 60 min queries
  from the same data)? Configurable changes the bucket-array sizing.
- **Multi-tenant / multi-key?** One counter for the whole system, or
  one per user / endpoint? Multi-key changes the storage shape (one
  bucket array per key) and the sharding (key-based hashing).
- **Hit rate?** 1K/sec? 1M/sec? 100M/sec? Drives shard count and
  storage choice (in-memory vs. durable backing).
- **Read latency SLO?** Real-time (every read fans out to every shard)
  vs. eventual (read from a pre-aggregated store with ~1 sec lag)?
- **Durability?** Lose counts on a shard restart, or replicate via a
  durable store?
- **Cross-region?** Aggregate worldwide, or per-region?

Assumed for the design below: per-second granularity, fixed 5-min
window, single key (multi-key noted as a scaling lever), 1M hits/sec
aggregate, eventual reads OK with ~1 sec lag, durability via Redis,
single-region.

### Block diagram

```
                        ┌──────────┐
   client requests ─► │  load    │  consistent hash on client ID
                      │ balancer │
                      └────┬─────┘
                           │
                ┌──────────┼──────────┐
                │          │          │
                ▼          ▼          ▼
          ┌─────────┐┌─────────┐┌─────────┐
          │ shard 0 ││ shard 1 ││ shard N │   in-mem ConcurrentCounter
          └────┬────┘└────┬────┘└────┬────┘
               │ flush every 1 sec   │
               └─────────┬───────────┘
                         ▼
                  ┌──────────────┐
                  │ Redis ZSET   │   durable; survives shard restarts
                  │ /TSDB        │
                  └──────┬───────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  aggregator  │   real-time fan-out OR eventual read
                  └──────┬───────┘
                         │
                         ▼
              GET /hits?window=300
```

Five components, only the durable store carries state across restarts:

- **Clients** — application servers, mobile, web. Each request is a hit.
- **Load balancer** — consistent hashing on client ID; pins a client to
  one shard. Same client always lands on the same shard.
- **Shard fleet** — N stateless app servers, each holding a
  `ConcurrentCounter` (300 slots, 300 locks) in memory.
- **Durable store** — Redis sorted set or a time-series DB. Each shard
  flushes its bucket state every ~1 second. Survives shard restarts and
  is the fallback when a shard is unreachable.
- **Aggregator** — stateless service that answers `GET /hits`. Real-time
  fan-out (RPC to every shard) for fresh reads; durable-store read for
  eventual reads.

### API surface

```http
GET /hits?window=300
  response: 200 {
    "count":   12345,
    "as_of":   "2026-05-23T12:34:56Z",
    "shards_reached": 4,
    "shards_total":   4
  }
  Real-time read — fan-out to all shards. shards_reached < shards_total
  signals a degraded result (one or more shards timed out; the missing
  slice is backfilled from the durable store).

GET /hits?window=300&consistency=eventual
  response: 200 {
    "count":        12345,
    "as_of":        "2026-05-23T12:34:55Z",
    "lag_seconds":  1
  }
  Eventual read — straight from the durable store. Lower latency,
  about 1 sec stale (one flush interval).

# Hit recording is NOT a separate API — it's inline middleware on
# whatever the shard already does (log a request, charge a card, etc.).
# Calling /hit per request would double the QPS the system has to
# absorb; folding it into the existing request path is essentially free.
```

Three things worth calling out:

- **There is no POST endpoint.** Hits are recorded inline by the shard
  as it handles its actual workload — making `hit()` a function call,
  not a network round-trip. A separate `POST /hit` endpoint would
  double the QPS budget for no semantic benefit.
- **The response carries provenance** (`shards_reached`, `lag_seconds`).
  Distributed counters can serve degraded answers; the client deserves
  to know which kind it got, especially for alerting decisions.
- **No "scan_id" or lifecycle endpoint.** Unlike `file_duplicates`'s
  scan-and-result flow, the hit counter is **steady-state** — the
  counter is always-on. The architectural focus shifts from "when is
  the work done?" to "how fresh is the answer?"

### Storage shapes

**Shard-local (in-memory):**

```
times[300]   — last second written to each slot.
counts[300]  — count at that second.
locks[300]   — per-bucket threading.Lock.
```

600 ints + 300 locks per shard. Fits L1 cache; restart cost is one
flush interval of lost recent hits.

**Durable store (Redis sorted set per counter):**

```redis
# Per shard, every flush interval (~1 sec):
ZADD hits:counter1 <second> "<second>:<shard_id>:<count>"

# Aged-out GC (run periodically by any shard, idempotent):
ZREMRANGEBYSCORE hits:counter1 -inf <now - 300>

# Eventual read (returns members in window):
ZRANGEBYSCORE hits:counter1 <now - 299> <now>
# Sum the count fields of the returned members.
```

`(second, shard_id, count)` is the wire format — one row per
(timestamp, shard) pair, idempotent on rewrite. Storage per counter:
300 seconds × N shards rows in the window = O(W·N).

Alternative: a time-series DB (InfluxDB / TimescaleDB / Prometheus)
with native per-second-bucket sliding-window queries. Higher
operational overhead, better at long retention.

### ★ The dispatcher and aggregator — the critical design ★

The two halves of scatter-gather, and where most of the design value lives.

**Dispatcher — picking a shard for each hit:**

```
on hit(t) from client C:
    shard_id = consistent_hash(C.id) mod N
    shards[shard_id].hit(t)
```

| Strategy | Pro | Con |
|----------|-----|-----|
| **Consistent hash on client ID** (used here) | adding/removing a shard re-routes only `1/N` of clients; existing buckets stay valid; same client → same shard → cache-friendly | hot clients (heavy hitters) pin to one shard and overheat it |
| Round-robin (load balancer) | perfectly uniform | a single client's hits scatter across all shards; eventually-consistent aggregation no longer maps onto "ask one shard for one client" — pure global aggregate only |
| Hash on `(client_id, second)` | smears hot clients across shards-per-second | within one second, the hot client still serializes on one shard's slot |
| `t mod N` (timestamp-based) | trivially uniform | **HOT SPOTTING** — every hit in any one second goes to the same shard. Never use this. |

The simulation's `threading.get_ident() % N` is the in-process analogue
of consistent hashing on client ID — each thread is "pinned" to one
shard the way each client is pinned by the load balancer.

**Aggregator — answering get_hits:**

```
aggregator.get_hits(t, consistency='strong'):
    if consistency == 'strong':                    # real-time fan-out
        results = parallel_rpc(
            (shard, shard.get_hits(t), timeout=50ms)
            for shard in shards
        )
        for shard, result in results:
            if result.ok:
                total += result.value
            else:                                   # shard unreachable
                total += durable_store_slice(shard, t)
        return total

    else:                                           # eventual
        members = durable_store.zrange(t - 299, t)
        return sum(m.count for m in members)
```

Two read modes serve two SLOs:

- **Strong consistency** — fans out to every shard, sums responses with
  per-shard timeout, falls back to durable store for any shard that
  times out. Cost: one RPC per shard per read; latency tracks the
  slowest shard.
- **Eventual consistency** — reads exclusively from the durable store.
  Cost: one Redis call; lag = flush interval.

**The aggregator sums; the *bucket* makes the sum correct.** Each
shard's slot `i` is independent of every other shard's slot `i`. A hit
at `t` lands in one shard's slot `t % 300`; three other shards' slot
`t % 300` is untouched. Summing across shards counts each hit exactly
once — this is the *associativity* property of the bucket shape that
made it shardable in the first place. A deque-of-timestamps would not
sum this way; it would need a merge (sorted union of two sequences),
which is `O(N+M)` per read, not `O(1)`.

### Termination — N/A

Unlike `file_duplicates`'s Tier 4, the hit counter is **steady-state**
— there's no "scan complete" condition. The architectural question
shifts from "when is the work done?" to "how fresh is the answer?",
which the dual-read-mode aggregator answers above.

### Idempotency and replay

- **Hit recording is idempotent only at the message-id level.** A hit
  is a `counts[slot] += 1` — without a per-hit ID, a retried hit
  re-increments. For inline hits (the standard case) the network
  hop is local; retries are rare and double-counting at 0.01%
  accuracy is acceptable for rate counters.
- **Flush is idempotent.** `ZADD <second> "<second>:<shard>:<count>"`
  with the same key triggers `ZADD`'s update semantics — the most
  recent flush wins. A retried flush is harmless.
- **Eventual reads are idempotent.** Pure read against the durable
  store; safe to retry on the client.

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Shard crash | recent hits lost from that shard's local memory | each shard flushes every ~1 sec; lose at most 1 sec of pre-flush hits |
| Shard slow (not crashed) | one shard's RPC holds up the fan-out | per-shard timeout (~50 ms); fall back to durable-store slice for that shard |
| Durable store crash | flushes fail; eventual reads fail | shards buffer flushes locally with a bounded queue; strong reads still work via fan-out |
| Aggregator crash | reads fail | aggregator is stateless; standby instances behind a second load balancer |
| All shards crash | counter loses all recent state | warm up from durable store on restart; lose ≤ 1 flush interval |
| Hot client (one user dominates) | one shard's slot overheats; per-bucket lock contention spikes | smear by hashing `(client_id, second_bucket)` — per-second the hot user lives on one shard, but rotates across shards as time advances |
| Clock skew across shards | bucket boundaries misalign across the fleet | NTP everywhere; accept ~1 sec drift; for tighter sync, run a Lamport-style monotonic timestamp service |
| Network partition (shard reachable, durable store not) | shard accepts hits but can't flush | local buffer with a cap; on cap-overrun, drop oldest pre-flush data and emit a metric |

### Scaling levers

- **Auto-scale shards on QPS.** Target hits-per-second-per-shard ≤ K;
  scale up on overshoot.
- **Multi-key.** Real rate counters are per-user, per-endpoint. The
  shape generalizes by keying the bucket arrays:
  `counters[key].times` / `counters[key].counts`. Storage grows with
  active key count, not hit volume.
- **Variable windows from one store.** Per-second buckets answer any
  window from 1 sec to 300 sec by summing the relevant slot range.
  For 15-min windows you size buckets to 900 instead of 300; the
  structure is identical.
- **Per-region aggregation.** Each region holds its own shard fleet
  and aggregator. Global queries sum across regions with an additional
  fan-out level.
- **Approximate counters** (Count-Min Sketch, HyperLogLog) for huge
  key counts where exact-per-key would not fit memory. Trades
  accuracy for storage compression.
- **Sticky session affinity at the load balancer.** Already implicit
  in consistent hashing — keeps related hits in one shard's cache.

### What this design defers

- **Multi-key** beyond mention in scaling — the simulation is
  single-counter for clarity. Real rate-counter usage is always
  per-key.
- **Cross-region aggregation** — single-region only.
- **Adversarial protection** — assumes well-behaved clients. A
  flood-of-hits attacker manipulating the count is its own
  rate-limiting problem (chicken-and-egg).
- **Strict snapshot reads** — fan-out reads are *not* atomic across
  shards; writes can land between per-shard RPCs. A globally-consistent
  snapshot would need a snapshot-isolation read protocol, expensive
  for a counter.

### Simulation → production mapping

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `ConcurrentCounter` per shard | rate-counter sidecar process on each app server |
| `threading.get_ident() % N` | consistent hashing on client ID at the load balancer |
| `sum(s.get_hits(t) for s in shards)` | parallel RPC fan-out with per-shard timeout + Redis fallback |
| in-process method call | RPC over gRPC / HTTP with retries |
| (none — in-process is durable enough) | Redis sorted set or TSDB for cross-restart durability |
| (none — single-process) | aggregator as a stateless replicated service |

The shape is the same — N independent bucketed counters with a sum on
read; what changes is what holds the state and how the parts
communicate.

## Running the tests

```sh
uv run pytest Algorithms/hit_counter/tests/ -q
```
