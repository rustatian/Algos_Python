# LRU Cache

A fixed-capacity cache with O(1) `get` and `put` that evicts the
**least-recently-used** entry when it overflows. The foundational
state-management problem — once you can build this, TTL caches, LFU
caches, and distributed caches are variations on the same skeleton.

Modeled on LeetCode #146 (LRU Cache) and the classic "design a cache"
interview question. Related references: #460 (LFU Cache), #1797
(Authentication Manager — TTL), #432 (All O'one Data Structure).

## Problem

- `get(key) -> value | None` — return the value, mark the key
  most-recently-used; `None` on a miss.
- `put(key, value)` — insert or overwrite, mark most-recently-used; if
  this pushes the cache past `capacity`, evict the least-recently-used
  entry.

Both operations must be **O(1)**. That requirement is the whole puzzle:
a dict gives O(1) lookup but no ordering; a list gives ordering but O(N)
reordering. The answer is to combine them — a **hash map + doubly-linked
list**:

```
dict:  key -> node
list:  head <-> MRU <-> ... <-> LRU <-> tail      (head/tail are sentinels)
```

The dict finds any node in O(1); the linked list moves that node to the
front (most-recently-used) or pops the back (least-recently-used) in O(1)
by splicing four pointers — no shifting. Two **sentinel** nodes at the
ends mean insertion and removal never special-case an empty list or a
boundary node.

```python
c = SimpleLRU(capacity=2)
c.put("a", 1); c.put("b", 2)
c.get("a")        # -> 1   ("a" now MRU, "b" is LRU)
c.put("c", 3)     #        over capacity -> evict "b"
c.get("b")        # -> None
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleLRU` | doubly-linked list + dict | the algorithm — O(1) via the map+list pairing and sentinel nodes |
| 2 | `ThreadSafeLRU` | Tier 1 under one `threading.Lock` | concurrency — why the list splice forces a coarse-grained critical section |
| 3 | `TTLLRU` | per-entry TTL + LRU eviction | two eviction reasons coexisting — capacity (LRU) and time (lazy expiry) |
| 4 | `DistributedCache` | sharded + replicated | the system-design follow-up — consistent hashing, hot keys, invalidation |

Every tier shares the `get` / `put` surface.

Each tier answers the previous one's weak spot. Tier 1 is correct but
single-threaded — two threads calling `put` at once would corrupt the
linked list mid-splice. Tier 2 wraps each operation in one lock; the
splice and the dict update must be atomic together, so the critical
section is the whole operation (the cost is that all access serializes —
Tier 4's sharding is what restores parallelism). Tier 3 adds a second
eviction axis: each entry carries an `expires_at`, and a read of an
expired entry returns `None` and drops it (lazy expiry), while capacity
pressure still evicts the LRU entry independently. Tier 4 leaves the
single machine.

### Why a hand-built linked list and not `OrderedDict`

`collections.OrderedDict` already supports `move_to_end` and
`popitem(last=False)`, so a production LRU is ~10 lines on top of it. The
explicit doubly-linked list is here because it is the **interview-expected
answer** (interviewers usually disallow `OrderedDict`) and because it
makes the O(1) reordering visible: you can see that "use this entry" is
four pointer assignments, not a search.

## Tier 4 — the system-design follow-up (distributed cache)

The single-machine cache is the per-node building block of a distributed
cache (Memcached / Redis Cluster). The follow-up: *serve a working set far
larger than one machine's RAM, at low latency, across a fleet.*

**Opener questions.** Read/write ratio? Eviction policy globally LRU, or
is per-node LRU acceptable? Consistency on writes — write-through,
write-back, or write-around? Tolerable staleness? What is the hot-key
story (one key getting a disproportionate share of traffic)? Fail mode on
a cache miss — fall through to the database, or fail?

**Design sketch.**

```
   client ─► cache client (consistent-hash ring) ─► cache node A (LRU)
                                                    cache node B (LRU)
                                                    cache node C (LRU)
                                  on miss ▼
                              system of record (DB)
```

- **Sharding by consistent hashing.** Each key maps to a node on a hash
  ring; adding/removing a node remaps only ~1/N of keys (not all of them).
  Each node runs exactly Tier 1/2's LRU over its slice — eviction is
  per-node, which is why "global LRU" is usually relaxed to "per-shard
  LRU."
- **Replication for hot keys / availability.** A hot key is replicated to
  R nodes and read from any; the client spreads reads. Without it, one
  node owning a viral key becomes a hotspot.
- **Write policy.** Write-through (update cache + DB synchronously: simple,
  consistent, slower writes), write-back (update cache, flush to DB async:
  fast, risks loss on node death), or write-around (write DB only, let the
  cache fill on read: avoids caching write-only data). TTL bounds staleness
  regardless.
- **Invalidation.** On a DB write, publish an invalidation for the key so
  cache nodes drop it — the classic cache-coherence problem; TTL is the
  backstop when an invalidation is missed.

**Failures.** Node death → its keys are re-fetched from the DB on the next
read (a brief miss storm — mitigate with replication / request
coalescing). *Thundering herd* on a popular expired key → many clients
miss simultaneously and stampede the DB; mitigate with a per-key lock so
one client refills while others wait, or with stale-while-revalidate.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| one `SimpleLRU` instance | one cache node's local store |
| `threading.Lock` (Tier 2) | one node's internal concurrency control |
| TTL `expires_at` (Tier 3) | Redis/Memcached per-key TTL |
| (single process) | consistent-hash ring of nodes + replicas |
| `get` returning `None` | a cache miss → read-through to the DB |

## Running the tests

```sh
uv run pytest Algorithms/lru/tests/ -q
```
