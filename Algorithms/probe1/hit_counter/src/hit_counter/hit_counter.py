"""Design Hit Counter — LeetCode #362.

Design a counter that, for any timestamp, returns the number of "hits" the
system received during the past 300 seconds (5 minutes). Hits arrive in
chronological order — timestamps to ``hit()`` are monotonically
non-decreasing — and multiple hits can share one timestamp.

Input:
    hit(timestamp: int) -> None
        Record a hit at ``timestamp`` (seconds).
    get_hits(timestamp: int) -> int
        Return the number of hits in the window (timestamp - 300, timestamp].
Output:
    get_hits returns the count of hits within the last 300 seconds — the
    window is half-open on the left, so a hit at exactly ``t - 300``
    does *not* count.

Example 1:
    ops:  ["hit","hit","hit","get_hits","hit","get_hits","get_hits"]
    args: [[1],  [2],  [3],  [4],        [300],[300],     [301]]
    out:  [None, None, None, 3,          None, 4,         3]
    Explanation:
        hit(1), hit(2), hit(3)        record three hits.
        get_hits(4)   -> 3            window (4-300, 4] = (-296, 4]; all three in.
        hit(300)                       record a fourth.
        get_hits(300) -> 4            window (0, 300]; all four in.
        get_hits(301) -> 3            window (1, 301]; t=1 excluded (1 ≤ 1).

Example 2:
    ops:  ["get_hits"]; args: [[100]]; out: [0]
    Explanation: no prior hit recorded -> 0.

Example 3:
    ops:  ["hit","hit","hit","get_hits"]; args: [[10],[10],[10],[10]]; out: [None,None,None,3]
    Explanation: three hits share timestamp 10; no de-duplication by
        timestamp, so all three are counted.

This package ports the problem as a tiered learning ladder. Each tier is a
class with the same hit / get_hits surface; what changes is *how* the
counter is represented and which scaling weakness the next tier responds to.

Tier 1: DequeCounter       — store every timestamp; drop old ones on read.
Tier 2: BucketCounter      — circular array of 300 buckets; O(1) get_hits.
Tier 3: ConcurrentCounter  — bucketed + per-bucket lock for parallel hit().
Tier 4: DistributedCounter — per-shard local counters + central aggregator
    (the system-design follow-up: how the counter shape carries across N servers).

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import threading
from collections import deque


class DequeCounter:
    """Tier 1: store every hit timestamp in a deque; drop old ones on read.

    Input:
        hit(timestamp: int) -> None       — record a hit at ``timestamp``.
        get_hits(timestamp: int) -> int   — count of hits in (timestamp-300, timestamp].
    Output:
        get_hits returns the number of stored timestamps within the last
        300 seconds of the query.

    Example 1:
        ops:  ["hit","hit","hit","get_hits","hit","get_hits","get_hits"]
        args: [[1],  [2],  [3],  [4],        [300],[300],     [301]]
        out:  [None, None, None, 3,          None, 4,         3]
        Explanation:
            After hit(1)/hit(2)/hit(3) the deque holds [1, 2, 3].
            get_hits(4): cutoff = -296; nothing aged out; size = 3.
            hit(300): deque is [1, 2, 3, 300].
            get_hits(300): cutoff = 0; nothing aged out; size = 4.
            get_hits(301): cutoff = 1; popleft drops t=1; size = 3.
            (The lazy purge inside get_hits removes 1 while computing.)

    Example 2:
        ops:  ["hit","hit","hit","get_hits"]
        args: [[1],  [1],  [1],  [1]]
        out:  [None, None, None, 3]
        Explanation:
            All three hits share timestamp 1; the deque stores every one
            (no per-timestamp de-duplication); cutoff = -299; size = 3.

    Example 3:
        ops:  ["hit","get_hits","get_hits"]
        args: [[1],  [300],      [301]]
        out:  [None, 1,          0]
        Explanation:
            get_hits(300): cutoff = 0; 1 > 0, so t=1 stays; size = 1.
            get_hits(301): cutoff = 1; 1 ≤ 1, so t=1 is popped; size = 0.
            This is the half-open boundary: a hit at t' = t - 300 is out.

    Standard library:
        collections.deque — O(1) ``append`` on the right, O(1) ``popleft``
        on the left. A plain list would popleft in O(n) (every element
        shifts), turning the amortized story into a quadratic one under
        bursty traffic.

    Pseudocode:
        data:
            hits — a deque of timestamps in arrival order.

        hit(t):
            append t to the right end of hits.

        get_hits(t):
            cutoff = t - 300
            while hits is not empty and hits[0] <= cutoff:
                popleft
            return the size of hits.

    Complexity:
        Storage: O(H) where H is the number of timestamps currently
            stored. Worst-case unbounded between reads — this is the
            first weakness Tier 2 fixes (storage collapses to O(window)
            regardless of hit volume).
        hit():      O(1).
        get_hits(): amortized O(1) per call. Each timestamp is popped at
            most once over its lifetime, so the total purge work across
            N hits is O(N), spread across reads.
    """

    def __init__(self):
        self._ts = deque()

    def hit(self, timestamp: int) -> None:
        self._ts.append(timestamp)

    def get_hits(self, timestamp: int) -> int:
        while self._ts and timestamp - self._ts[0] >= 300:
            self._ts.popleft()

        return len(self._ts)


class BucketCounter:
    """Tier 2: circular array of 300 second-buckets; storage is O(window).

    Each of the 300 slots remembers the latest second it was written
    (``times[i]``) and the count of hits at that second (``counts[i]``).
    A timestamp ``t`` lives in slot ``t % 300``; a write at ``t`` either
    bumps the count (slot already matches ``t``) or overwrites the slot
    (it held data from a previous epoch — stale).

    Input:
        hit(timestamp: int) -> None       — record a hit at ``timestamp``.
        get_hits(timestamp: int) -> int   — count of hits in (timestamp-300, timestamp].
    Output:
        get_hits returns the sum of counts across the 300 slots whose
        stored timestamp is still within the window.

    Example 1:
        ops:  ["hit","hit","hit","get_hits","hit","get_hits","get_hits"]
        args: [[1],  [2],  [3],  [4],        [300],[300],     [301]]
        out:  [None, None, None, 3,          None, 4,         3]
        Explanation:
            After hit(1)/hit(2)/hit(3) slots 1, 2, 3 each hold (t, 1).
            get_hits(4): the three slots have 4-t in {3, 2, 1}, all < 300; sum = 3.
            hit(300): slot 300 % 300 = 0 was untouched (times[0]=0, counts[0]=0);
                stored timestamp 0 ≠ 300, so overwrite — slot 0 now holds (300, 1).
            get_hits(300): slots 1,2,3,0 contribute 1+1+1+1; sum = 4.
            get_hits(301): slot 1 has 301-1 = 300, NOT < 300 → skipped; sum = 3.

    Example 2:
        ops:  ["hit","hit","get_hits"]
        args: [[5],  [305],[305]]
        out:  [None, None, 1]
        Explanation:
            hit(5):   slot 5 % 300 = 5 holds (5, 1).
            hit(305): slot 305 % 300 = 5 — SAME slot. Stored timestamp 5 ≠ 305,
                so overwrite: slot 5 now holds (305, 1). The old t=5 hit is gone.
            get_hits(305): slot 5 has 305-305 = 0 < 300; sum = 1.
            (This is the slot-collision case — the overwrite-on-stale rule
            is what keeps the bucket sound.)

    Example 3:
        ops:  ["hit","hit","hit","get_hits"]
        args: [[10], [10], [10], [10]]
        out:  [None, None, None, 3]
        Explanation:
            All three hits land in slot 10 % 300 = 10 at the same timestamp.
            First write: stored time 0 ≠ 10 → overwrite to (10, 1).
            Second write: stored time 10 == 10 → bump to (10, 2).
            Third write: stored time 10 == 10 → bump to (10, 3).
            get_hits(10): slot 10 has 10-10 = 0 < 300 → contributes 3; sum = 3.

    Standard library:
        plain list — used as a fixed-size array. No deque needed; the
        bucket structure does not grow with hit volume, so amortized
        analysis is irrelevant — every operation is bounded by 300.

    Pseudocode:
        data:
            times  — list of 300 ints, all 0.
            counts — list of 300 ints, all 0.

        hit(t):
            slot = t mod 300
            if times[slot] == t:
                counts[slot] += 1
            else:
                # slot holds stale data from a previous epoch — overwrite.
                times[slot]  = t
                counts[slot] = 1

        get_hits(t):
            total = 0
            for slot in 0 .. 299:
                if t - times[slot] < 300:
                    total += counts[slot]
            return total

    Complexity:
        Storage: O(W) where W = 300 — independent of hit volume. This is
            the headline win over Tier 1: a million hits/sec for an hour
            still costs 600 ints.
        hit():      O(1).
        get_hits(): O(W) = O(300) — a fixed-size sweep over all slots.
            Worst-case AND best-case are the same: no amortization story,
            no purge surprises.
    """

    def __init__(self):
        self._times = [0] * 300
        self._counts = [0] * 300

    def hit(self, timestamp: int) -> None:
        slot = timestamp % 300
        if self._times[slot] == timestamp:
            self._counts[slot] += 1
        else:
            self._times[slot] = timestamp
            self._counts[slot] = 1

    def get_hits(self, timestamp: int) -> int:
        total = 0
        for slot in range(0, 300):
            if timestamp - self._times[slot] < 300:
                total += self._counts[slot]
        return total


class ConcurrentCounter:
    """Tier 3: bucketed counter + per-bucket lock for safe parallel hit().

    Same 300-slot ``times`` / ``counts`` arrays as Tier 2, plus a parallel
    list of 300 ``threading.Lock`` instances — one per slot. Two threads
    writing to different slots take different locks and proceed in
    parallel; only same-slot writes serialize. Reads also acquire each
    slot's lock briefly to avoid observing a torn ``(times[slot],
    counts[slot])`` pair mid-update.

    Input:
        hit(timestamp: int) -> None       — record a hit at ``timestamp``.
                                            Safe to call concurrently from
                                            any number of threads.
        get_hits(timestamp: int) -> int   — count of hits in (timestamp-300, timestamp].
                                            Safe to call concurrently with hits;
                                            does not return a strict snapshot.
    Output:
        get_hits returns the sum of counts across the 300 slots whose
        stored timestamp is still within the window, with per-slot lock
        coverage during the read so each slot's pair is seen consistently.

    Example 1 (single-threaded — identical to Tier 2):
        ops:  ["hit","hit","hit","get_hits","hit","get_hits","get_hits"]
        args: [[1],  [2],  [3],  [4],        [300],[300],     [301]]
        out:  [None, None, None, 3,          None, 4,         3]
        Explanation:
            Lock acquisition adds latency per op but does not change
            semantics — single-threaded behavior is identical to
            BucketCounter.

    Example 2 (concurrent writes to ONE slot — same timestamp):
        Thread A: hit(100), hit(100), hit(100)
        Thread B: hit(100), hit(100), hit(100)
        Main:     wait for both threads, then get_hits(100)
        Output:   6
        Explanation:
            All 6 hits land in slot 100. Without the per-bucket lock in
            hit(), the read-modify-write of ``counts[100] += 1`` would
            race: both threads can read the same value, increment to the
            same new value, and one write overwrites the other (a "lost
            update"). The lock serializes the two threads through that
            slot so all 6 increments land.

    Example 3 (concurrent writes to DIFFERENT slots — the win case):
        Thread A: hit(0),   hit(1),   ..., hit(149)     # writes slots 0..149
        Thread B: hit(150), hit(151), ..., hit(299)     # writes slots 150..299
        Main:     wait for both, then get_hits(299)
        Output:   300
        Explanation:
            The two threads acquire 150 disjoint locks each — they never
            wait for each other. Wall time is roughly half that of a
            single-threaded run. This is the headline advantage over a
            coarse global lock: throughput scales with slot-distinct
            traffic, up to 300× in the limit.

    Standard library:
        threading.Lock — a non-reentrant mutex. Acquired with the ``with``
            statement, which guarantees release on exit (including via
            exceptions). 300 instances are cheap (~64 bytes each).
        A reader-writer lock would be a slight win for the read path
            (many readers, one writer per bucket) but Python's stdlib
            does not provide one — would have to roll one from
            ``Condition`` + counter. Not worth it at this granularity.

    Pseudocode:
        data:
            times  — list of 300 ints, all 0.
            counts — list of 300 ints, all 0.
            locks  — list of 300 threading.Lock instances.

        hit(t):
            slot = t mod 300
            with locks[slot]:
                if times[slot] == t:
                    counts[slot] += 1
                else:
                    times[slot]  = t
                    counts[slot] = 1

        get_hits(t):
            total = 0
            for slot in 0 .. 299:
                with locks[slot]:
                    if t - times[slot] < 300:
                        total += counts[slot]
            return total

    Why per-bucket, not one global lock:
        A single global lock serializes EVERY hit through one mutex —
        throughput is capped at one hit per lock-acquire cost, no matter
        how many cores you have. With 300 independent locks and traffic
        spread across slots, N hits to N distinct slots run in parallel
        across N cores. Real-world traffic is usually well-spread (hits
        cluster in the current second or two, not the same single slot),
        so the parallelism shows up.

    Why get_hits also locks per slot:
        Without the lock, a reader could observe ``times[slot]`` and
        ``counts[slot]`` from different mutations — e.g., the new
        timestamp paired with the old count after an overwrite. That
        gives the wrong contribution for that slot. Holding the slot's
        lock during the two reads makes the pair atomic.
        Note: get_hits does NOT return a snapshot of the whole counter
        at a single instant — hits to OTHER slots can land between the
        300 lock acquisitions. For a rate counter this drift is fine; a
        strict snapshot would require holding all 300 locks at once,
        which serializes the whole counter and defeats the purpose.

    Complexity:
        Storage: O(W) where W = 300 — same as Tier 2, plus 300 Lock objects.
        hit():      O(1) — one lock acquire + 1–2 array writes.
        get_hits(): O(W) = O(300) lock acquires + sums; the lock cost
            dominates the arithmetic.
        Contention: same-slot writes serialize; different-slot writes
            run in parallel. Uniform traffic across slots scales nearly
            linearly with worker count up to ~300.
    """

    def __init__(self):
        self._times = [0] * 300
        self._counts = [0] * 300
        self._locks = [threading.Lock() for _ in range(300)]

    def hit(self, timestamp: int) -> None:
        slot = timestamp % 300
        with self._locks[slot]:
            if self._times[slot] == timestamp:
                self._counts[slot] += 1
            else:
                self._times[slot] = timestamp
                self._counts[slot] = 1

    def get_hits(self, timestamp: int) -> int:
        ans = 0
        for slot in range(0, 300):
            with self._locks[slot]:
                if timestamp - self._times[slot] < 300:
                    ans += self._counts[slot]
        return ans


class DistributedCounter:
    """Tier 4: per-shard local counters + a fan-out / fan-in aggregator.

    The single-machine bucket counter, lifted to N independent shards.
    Each shard owns a ConcurrentCounter; hits route to ONE shard
    (dispatched by thread identity, mirroring how a real load balancer
    pins a client to a server); get_hits queries EVERY shard and sums
    their local windowed counts. The bucket shape from Tier 2 is now
    also the wire format — what each shard returns when asked.

    Single-process simulation of the system-design follow-up. In production:
      - Each shard runs on a separate server with its own counter.
      - The dispatcher is a load balancer (consistent hashing on client
        ID, or round-robin across a fleet).
      - The aggregator is a service that fans an RPC to every shard
        on get_hits, sums the responses, applies a timeout per shard.
      - The shard's bucket state is periodically flushed to a durable
        store (Redis sorted set keyed by second, or a time-series DB)
        so a shard restart doesn't lose the in-flight window.

    Input:
        hit(timestamp: int) -> None       — record a hit at ``timestamp``.
                                            Routed to one shard.
        get_hits(timestamp: int) -> int   — count of hits in (timestamp-300, timestamp]
                                            across ALL shards.
    Output:
        get_hits returns the sum of every shard's local windowed count.

    Example 1 (single-threaded, num_shards=4):
        ops:  ["hit","hit","hit","get_hits","hit","get_hits","get_hits"]
        args: [[1],  [2],  [3],  [4],        [300],[300],     [301]]
        out:  [None, None, None, 3,          None, 4,         3]
        Explanation:
            All ops come from one thread, so they route to the same
            shard. Behavior is identical to a single ConcurrentCounter.
            get_hits sums across all 4 shards: one contributes the
            actual hits; the other 3 contribute 0.

    Example 2 (multi-threaded — the scatter-gather case):
        Thread A (pinned to shard 0): hit(10), hit(10), hit(10)    # 3 hits
        Thread B (pinned to shard 2): hit(10), hit(10)             # 2 hits
        Main:                         get_hits(10)
        Output: 5
        Explanation:
            Each shard owns its OWN slot-10. Thread A's hits land in
            shard 0's slot-10 (state: (10, 3)); Thread B's in shard
            2's slot-10 (state: (10, 2)); shards 1 and 3 are untouched.
            get_hits queries all 4 shards: 3 + 0 + 2 + 0 = 5.
            (Crucially, the slots from different shards do NOT collide
            — that is exactly the point of sharding the state.)

    Example 3 (failure-isolation rationale):
        Thread A → shard 0: hit(100), hit(100)
        Thread B → shard 3: hit(100)
        Imagine shard 3 crashes; if get_hits ignored unreachable shards
        and returned a partial result:
            get_hits(100) → 2   (instead of 3)
        The production design pays this cost explicitly: per-shard
        timeout in fan-out, plus a degraded-mode flag in the response.

    Standard library:
        threading.get_ident() — returns the OS-level thread identifier
            of the calling thread. Used as the dispatch key so each
            thread is pinned to one shard. Hash randomization in
            ``hash()`` would also work; ``get_ident() % num_shards`` is
            sufficient given that thread IDs are usually high integers
            with decent distribution.

    Pseudocode:
        data:
            shards     — list of N ConcurrentCounter instances.
            num_shards — N.

        hit(t):
            shard_id = pick_shard()
            shards[shard_id].hit(t)

        get_hits(t):
            total = 0
            for s in shards:
                total += s.get_hits(t)
            return total

        pick_shard():
            return threading.get_ident() mod num_shards

    Dispatcher trade-offs:
        thread_id mod N (used here):
            + Deterministic per-thread; no shared state; no lock for dispatch.
            + Mirrors real "client pinned to server" load balancing.
            - Distribution depends on thread-ID arithmetic; usually fine,
              occasionally clumpy if many threads share residues.

        round-robin (shared counter, lock):
            + Perfect distribution by construction.
            - The shared counter is a contention point — every hit serializes
              through one mutex, partially defeating the per-shard parallelism.

        random.randrange(N):
            + Statistically uniform.
            - Random state has its own internal lock; each thread can hold
              its own Random instance to avoid that, but adds complexity.

        timestamp mod N:
            - HOT SPOTTING: at any one second every hit goes to one shard.
              Never use this for a real counter.

    Aggregator trade-offs:
        Pull on read (used here):
            + No shared state between shards.
            + Reads are exactly as fresh as the moment they ran.
            - Read latency scales with N (serial fan-out); parallel fan-out
              with RPC helps but is out of scope for a single-process sim.

        Push to central on every hit:
            + Reads are O(1) — they just read the central store.
            - Every hit pays a round-trip; total write throughput drops to
              the central store's capacity.

        Periodic push (e.g., 1 Hz):
            + Reads are O(1) and writes don't pay per-hit cost.
            - Reads lag by up to the push interval; not exact.

    Complexity:
        Storage: O(N · W) — N shards × 300-slot bucket each.
        hit():      O(1) — dispatcher is O(1), inner ConcurrentCounter is O(1).
        get_hits(): O(N · W) — N shards × O(W) read each. Per-shard reads
            are independent and can be parallelized in production (e.g.,
            asyncio.gather over RPC clients); the simulation does them serially.
        Contention: nearly absent for cross-shard traffic — two threads
            pinned to different shards never compete. Within one shard,
            ConcurrentCounter's per-bucket locking still applies.
    """

    def __init__(self, num_shards: int = 4):
        self._num_shards = num_shards
        self._shards = [ConcurrentCounter() for _ in range(num_shards)]

    def _pick_shard(self) -> int:
        return threading.get_ident() % self._num_shards

    def hit(self, timestamp: int) -> None:
        shard = self._shards[self._pick_shard()]
        shard.hit(timestamp)
        return None

    def get_hits(self, timestamp: int) -> int:
        ans = 0
        for sh in self._shards:
            ans += sh.get_hits(timestamp)
        return ans
