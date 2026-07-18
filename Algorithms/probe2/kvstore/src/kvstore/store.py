"""KV Store — progressive design, Confluent tumbling-window flavor.

A tiered learning ladder. Every tier keeps a ``key -> value`` map; each
tier adds exactly one new idea on top of the previous one. Implement them
in order — the later tiers reuse the lessons of the earlier ones.

    Tier 1  SimpleKV       put / get / delete over a dict. O(1) everywhere.
    Tier 2  StatsKV        adds get_average() and get_max() over live entries.
                           Average is O(1) (running sum + count); max is
                           O(log n) amortized via a lazy-deletion max-heap.
    Tier 3  WindowedKV     tumbling time windows (Kafka-Streams style).
                           Aggregates are scoped to the window a timestamp
                           falls in. Append-only within a window, so the
                           per-window max is a plain O(1) running max.
    Tier 4  RetentionKV    adds a retention horizon: whole windows older than
                           `retention` are dropped wholesale, reclaiming memory.
    Tier 5  DistributedKV  thread-safety, then shard-by-key + scatter-gather
                           merge of per-window (sum, count, max) across shards
                           — the distributed system-design follow-up.

Values are floats throughout (so average / max are well-defined); keys are
strings. No input validation — these are interview-style exercises, so spend
the effort on the algorithm, not on guarding arguments.
"""

from heapq import heappush_max, heapreplace_max, heappop_max

from collections import defaultdict


class SimpleKV:
    """Tier 1 — a plain key/value store.

    Contract:
        put(key, val)  insert or overwrite.                       O(1)
        get(key)       current value, or None if absent.          O(1)
        delete(key)    remove key; a no-op if absent (idempotent). O(1)
    """

    def __init__(self) -> None:
        self.kv = defaultdict(float)

    def put(self, key: str, val: float) -> None:
        self.kv[key] = val

    def get(self, key: str) -> float | None:
        if key in self.kv:
            return self.kv[key]
        return None

    def delete(self, key: str) -> None:
        if key in self.kv:
            del self.kv[key]


class StatsKV:
    """Tier 2 — SimpleKV plus O(1) average and lazy-deletion max.

    Adds two aggregate queries over all *live* entries:
        get_average()  running total / count.                   O(1)
        get_max()      largest live value, or None if empty.    O(log n) am.

    Why max can't be O(1): once a key is overwritten or deleted, a single
    scalar "max so far" can become wrong and there is no O(1) way to find
    the new maximum. Keep a max-heap and use *lazy deletion* — never remove
    eagerly; instead, when a heap top surfaces, discard it if it no longer
    matches the live value for its key.
    """

    def __init__(self) -> None:
        self.kv = {}
        self.heap = []
        self.total = 0.0
        self.count = 0

    def put(self, key: str, val: float) -> None:
        if key in self.kv:
            self.total -= self.kv[key]
            self.count -= 1
        self.total += val
        self.kv[key] = val
        self.count += 1
        heappush_max(self.heap, (val, key))

        def get(self, key: str) -> float | None:
            if key in self.kv:
                return self.kv[key]
            return None

        def delete(self, key: str) -> None:
            if key in self.kv:
                self.total -= self.kv[key]
                self.count -= 1
                del self.kv[key]
                # stale left for lazy cleanup

        def get_average(self) -> float:
            return self.total / self.count

        def get_max(self) -> float | None:
            while self.heap:
                val, key = self.heap[0]
                if self.kv[key] == val:
                    return val
                heappop_max(self.heap)
            return None


    class WindowedKV:
        """Tier 3 — tumbling-window aggregates (Kafka-Streams style).

        Time is divided into fixed, non-overlapping windows of ``window_size``.
        A timestamp ``ts`` belongs to window id ``ts // window_size``. Aggregates
        are reported *per window*: a query at time ``ts`` describes the window
        that ``ts`` falls into, nothing else.

        Design decision — aggregate over the *stream of records*, not the
        deduplicated keys: every put counts toward its window's total/count/max.
        Because values are only ever added within a window (never retracted), the
        per-window max is a plain O(1) running max — no heap needed here. (That is
        the deliberate contrast with Tier 2, where overwrite/delete forced a heap.)
        ``get(key, ts)`` is still last-write-wins for the key inside window(ts).

        Contract:
            put(key, val, ts)
            get(key, ts)     latest value of key in window(ts), or None.
            get_average(ts)  mean of the records in window(ts) (0.0 if none).
            get_max(ts)      max record in window(ts), or None.
        """

    def __init__(self, window_size: int) -> None: ...

    def put(self, key: str, val: float, ts: int) -> None: ...

    def get(self, key: str, ts: int) -> float | None: ...

    def get_average(self, ts: int) -> float: ...

    def get_max(self, ts: int) -> float | None: ...


class RetentionKV:
    """Tier 4 — WindowedKV plus a retention horizon.

    A window stays live only while it is newer than ``now - retention``.
    Once a window lies entirely behind the retention horizon it is dropped
    as a unit — O(1) per dropped window, amortized O(1) per operation —
    releasing its aggregate counters and its key map.

    Every operation carries the current time and advances the horizon:
    expired windows are evicted *before* the operation is served. A query
    against an evicted (or never-seen) window returns None / 0.0.

    Contract (same surface as WindowedKV, now retention-aware):
        put(key, val, ts)
        get(key, ts)     -> float | None
        get_average(ts)  -> float
        get_max(ts)      -> float | None
    """

    def __init__(self, window_size: int, retention: int) -> None: ...

    def put(self, key: str, val: float, ts: int) -> None: ...

    def get(self, key: str, ts: int) -> float | None: ...

    def get_average(self, ts: int) -> float: ...

    def get_max(self, ts: int) -> float | None: ...


class DistributedKV:
    """Tier 5 — the distributed system-design follow-up.

    A single-process simulation of the at-scale shape:

      * thread-safety   guard each mutation so the map, the counters, and the
                        heap move atomically together (one lock, or one per shard).
      * sharding        route each key to shard ``hash(key) % shards``; each
                        shard is an independent RetentionKV.
      * scatter-gather  a window aggregate is merged across shards from each
                        shard's (total, count, max). Those are associative and
                        commutative, so they merge cleanly:
                            total = sum of totals
                            count = sum of counts
                            max   = max of maxes
                        Average is NOT directly mergeable — carry the
                        (sum, count) pair across shards and divide only at the end.

    Strong vs eventual consistency lives in the README architecture note: a
    single leader per shard serves strong reads; reading a replica is eventual.

    Contract (same surface, now sharded + locked):
        put(key, val, ts)
        get(key, ts)     -> float | None
        get_average(ts)  -> float
        get_max(ts)      -> float | None
    """

    def __init__(self, shards: int, window_size: int, retention: int) -> None: ...

    def put(self, key: str, val: float, ts: int) -> None: ...

    def get(self, key: str, ts: int) -> float | None: ...

    def get_average(self, ts: int) -> float: ...

    def get_max(self, ts: int) -> float | None: ...
