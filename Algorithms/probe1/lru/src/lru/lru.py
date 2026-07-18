"""LRU Cache — the state-management foundation (LeetCode #146).

A fixed-capacity cache with O(1) ``get`` and ``put``. When it is full and a
new key arrives, it evicts the **least-recently-used** entry. Both ``get``
and ``put`` count as a "use" and move the entry to the most-recently-used
end.

The canonical structure is a **doubly-linked list + hash map**:

    dict:  key -> node            (O(1) lookup of a node by key)
    list:  MRU <-> ... <-> LRU    (O(1) move-to-front and pop-from-back)

The dict finds a node in O(1); the linked list reorders it in O(1) by
splicing pointers (no array shifting). Together they give O(1) for every
operation — the reason this exact pairing is the textbook answer.

This package ports the problem as a tiered learning ladder:

Tier 1: SimpleLRU      — single-threaded DLL + dict; the #146 baseline.
Tier 2: ThreadSafeLRU  — Tier 1 under one lock; safe concurrent get/put.
Tier 3: TTLLRU         — per-entry TTL on top of LRU; two eviction reasons.
Tier 4: DistributedCache — HLD only (see README); sharded + replicated.

Input:
    __init__(capacity: int)
        capacity — the maximum number of entries; must be >= 1.
    get(key) -> value | None
        Return the value and mark the key most-recently-used; None on miss.
    put(key, value) -> None
        Insert/overwrite; mark MRU; evict the LRU entry if over capacity.

Output:
    get returns the stored value or None for a miss.
    put mutates the cache (and may evict one entry) as a side effect.

Example 1 (eviction order):
    c = SimpleLRU(capacity=2)
    c.put("a", 1); c.put("b", 2)
    c.get("a")            -> 1        # "a" is now MRU, "b" is LRU
    c.put("c", 3)                     # over capacity -> evict LRU ("b")
    c.get("b")            -> None     # "b" was evicted
    c.get("a")            -> 1        # "a" survived (it was used)

Example 2 (put on an existing key refreshes recency, does not grow):
    c = SimpleLRU(capacity=2)
    c.put("a", 1); c.put("b", 2)
    c.put("a", 10)                    # overwrite + mark MRU; size still 2
    c.put("c", 3)                     # evict LRU ("b"), not "a"
    c.get("a")            -> 10
    c.get("b")            -> None

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import threading
import time
from typing import Any, Callable


class _Node:
    """A doubly-linked-list node holding one cache entry.

    Carries ``key`` as well as ``value`` so that when we evict the
    tail node we know which dict key to delete — the eviction starts
    from the node, not the key.
    """

    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key: Any = None, value: Any = None) -> None:
        self.key = key
        self.value = value
        self.prev: "_Node | None" = None
        self.next: "_Node | None" = None


class SimpleLRU:
    """Tier 1: single-threaded LRU cache with O(1) get/put.

    Doubly-linked list ordered most-recently-used (just after ``head``)
    to least-recently-used (just before ``tail``), plus a dict mapping
    each key to its node. Two sentinel nodes (``head``/``tail``) remove
    the empty-list and single-element edge cases — every real node always
    has a non-None prev and next.

    Input:
        __init__(capacity: int) — max entries (>= 1).
        get(key) -> value | None
        put(key, value) -> None
    Output:
        get returns the value (and marks MRU) or None; put inserts/updates
        and evicts the LRU entry when capacity is exceeded.

    Example 1 (LRU eviction):
        c = SimpleLRU(2)
        c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
        c.get("b")  -> None    # "b" was least-recently-used -> evicted

    Example 2 (overwrite refreshes recency):
        c = SimpleLRU(2)
        c.put("a", 1); c.put("b", 2); c.put("a", 10); c.put("c", 3)
        c.get("a")  -> 10      # "a" survived; "b" evicted

    Standard library:
        dict — key -> node, O(1) lookup. (We build the linked list by hand
            rather than use collections.OrderedDict so the O(1) splicing is
            explicit — this is the interview-expected form. OrderedDict's
            move_to_end would do the same job in one call.)

    Pseudocode:
        data:
            cache : dict[key, node]
            head, tail : sentinel nodes; head.next is MRU, tail.prev is LRU.

        get(key):
            if key not in cache: return None
            node = cache[key]; move_to_front(node); return node.value

        put(key, value):
            if key in cache:
                node = cache[key]; node.value = value; move_to_front(node)
                return
            node = Node(key, value); cache[key] = node; add_to_front(node)
            if len(cache) > capacity:
                lru = tail.prev; unlink(lru); del cache[lru.key]

    Why two sentinel nodes:
        With dummy head and tail, insertion and removal never touch a None
        neighbor — there is no "is this the only node / the first node?"
        branching. Every splice is the same four pointer assignments.

    Complexity:
        get, put: O(1) — one dict op plus O(1) pointer splices.
        Storage: O(capacity).
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._cache: dict[Any, _Node] = {}
        # Sentinels: head <-> tail with no real nodes between them yet.
        self._head = _Node()
        self._tail = _Node()
        self._head.next = self._tail
        self._tail.prev = self._head

    def _unlink(self, node: _Node) -> None:
        """Splice a node out of the list (its neighbors join hands)."""
        node.prev.next = node.next  # type: ignore[union-attr]
        node.next.prev = node.prev  # type: ignore[union-attr]

    def _add_to_front(self, node: _Node) -> None:
        """Insert a node just after head — the most-recently-used slot."""
        node.prev = self._head
        node.next = self._head.next
        self._head.next.prev = node  # type: ignore[union-attr]
        self._head.next = node

    def _move_to_front(self, node: _Node) -> None:
        self._unlink(node)
        self._add_to_front(node)

    def get(self, key: Any) -> Any | None:
        node = self._cache.get(key)
        if node is None:
            return None
        self._move_to_front(node)  # reading counts as a use
        return node.value

    def put(self, key: Any, value: Any) -> None:
        node = self._cache.get(key)
        if node is not None:
            node.value = value
            self._move_to_front(node)
            return
        node = _Node(key, value)
        self._cache[key] = node
        self._add_to_front(node)
        if len(self._cache) > self._capacity:
            # Evict the least-recently-used node: the one before the tail.
            lru = self._tail.prev
            self._unlink(lru)  # type: ignore[arg-type]
            del self._cache[lru.key]  # type: ignore[union-attr]

    def __len__(self) -> int:
        return len(self._cache)


class ThreadSafeLRU(SimpleLRU):
    """Tier 2: Tier 1 made safe for concurrent threads with one lock.

    ``get`` and ``put`` both perform a read-modify-write of the linked
    list (splicing pointers) and the dict. Two threads interleaving those
    splices would corrupt the list — pointers crossing, the dict and list
    disagreeing on size. One lock around each public operation serializes
    them.

    Input / Output:
        Identical to SimpleLRU; the only change is that every call is
        atomic with respect to other threads.

    Standard library:
        threading.Lock — a non-reentrant mutex held for the whole
            operation. Acquired via ``with``, so it is released even if the
            body raises.

    Why the critical section is the *whole* operation (coarse-grained):
        The linked-list splice and the dict update must happen together —
        a reader that saw the list mid-splice could follow a half-updated
        pointer. There is no cheap finer-grained locking for a single
        shared list, so we lock the entire get/put. The cost is that the
        cache serializes all access; Tier 4's sharding is what restores
        parallelism (one lock per shard).

    Why a plain Lock and not RLock:
        The operations do not re-enter (get/put never call each other while
        holding the lock), so the cheaper non-reentrant Lock suffices.

    Complexity:
        get, put: O(1) plus lock acquire/release; throughput is bounded by
            the single lock under contention.
    """

    def __init__(self, capacity: int) -> None:
        super().__init__(capacity)
        self._lock = threading.Lock()

    def get(self, key: Any) -> Any | None:
        with self._lock:
            return super().get(key)

    def put(self, key: Any, value: Any) -> None:
        with self._lock:
            super().put(key, value)


class TTLLRU(SimpleLRU):
    """Tier 3: LRU cache where entries also expire after a TTL.

    Two independent reasons an entry can leave the cache now coexist:
      1. Capacity — the least-recently-used entry is evicted when full (LRU).
      2. Time — an entry past its TTL is treated as a miss and dropped on
         the next read of that key (lazy expiry).

    Entries are stored as ``(value, expires_at)`` on top of the Tier 1
    machinery; ``expires_at`` is None for entries with no TTL. A monotonic
    clock is injected so tests can fast-forward time deterministically.

    Input:
        __init__(capacity: int, clock: Callable[[], float] = time.monotonic)
        put(key, value, ttl_seconds: float | None = None) -> None
            ttl_seconds None => never expires (LRU eviction still applies).
        get(key) -> value | None — None on miss OR on an expired entry.
    Output:
        get returns the live value, or None if missing or expired (and an
        expired entry is evicted as a side effect).

    Example (expiry as a miss):
        clock = FakeClock()
        c = TTLLRU(capacity=10, clock=clock)
        c.put("k", "v", ttl_seconds=5)
        c.get("k")        -> "v"
        clock.advance(6)
        c.get("k")        -> None      # expired -> evicted

    Standard library:
        time.monotonic — default clock; non-decreasing, immune to wall-
            clock jumps, so a TTL measures real elapsed seconds.

    Pseudocode:
        put(key, value, ttl):
            expires_at = clock() + ttl if ttl is not None else None
            super().put(key, (value, expires_at))     # reuse LRU machinery

        get(key):
            entry = super().get(key)        # also bumps recency
            if entry is None: return None
            value, expires_at = entry
            if expires_at is not None and clock() >= expires_at:
                delete(key); return None    # lazy expiry: drop and miss
            return value

    Why lazy expiry (drop on read) rather than a timer per entry:
        A timer per key is heavy and racy. Checking expiry on read keeps
        get O(1) and needs no background machinery; the cost is that an
        expired-but-unread entry lingers until something reads it or it is
        evicted by capacity pressure. (A sweeper, as in kv_store's TTLKV,
        could reclaim those — out of scope for this tier.)

    Complexity:
        get, put: O(1) — same as Tier 1 plus a clock read.
    """

    def __init__(
        self, capacity: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        super().__init__(capacity)
        self._clock = clock

    def _delete(self, key: Any) -> None:
        node = self._cache.pop(key, None)
        if node is not None:
            self._unlink(node)

    def put(self, key: Any, value: Any, ttl_seconds: float | None = None) -> None:
        expires_at = self._clock() + ttl_seconds if ttl_seconds is not None else None
        # Store the (value, expires_at) pair; all LRU mechanics are reused.
        super().put(key, (value, expires_at))

    def get(self, key: Any) -> Any | None:
        entry = super().get(key)  # marks MRU if present
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            self._delete(key)  # lazy expiry: an expired entry is a miss
            return None
        return value
