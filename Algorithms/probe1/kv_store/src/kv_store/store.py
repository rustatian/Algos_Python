"""KV Store — progressive design (systems interview).

A key-value store that grows in capability across four tiers, each
layer adding one new dimension of complexity:

    put/get/delete   →   TTL   →   versioned reads   →   distributed

This package ports the problem as a tiered learning ladder. Every
tier shares the same put/get/delete surface; what changes is how
values are stored and what extra operations are exposed.

Tier 1: SimpleKV       — in-memory dict; put/get/delete; the baseline.
Tier 2: TTLKV          — (value, expires_at) tuples; lazy expiry; background sweeper.
Tier 3: SnapshotKV     — copy-on-write versioning (LeetCode #981 shape).
Tier 4: DistributedKV  — sharded + replicated; WAL; the system-design follow-up.

Input:
    put(key: str, value: Any) -> None
        Store a value at the given key. Overwrites any existing value.
    get(key: str) -> Any | None
        Return the value at the key, or None if the key is missing.
    delete(key: str) -> None
        Remove the key. No-op if not present.

Output:
    get returns the stored value or None for missing. None is a
    sentinel for "key not in store" — callers who store None as a
    value must distinguish "absent" from "present-as-None" through
    a separate mechanism (a "missing" sentinel, or wrap reads in
    contains-then-get).

Example 1 (basic put / get / delete):
    kv = SimpleKV()
    kv.put("user:42:name", "Alice")
    kv.get("user:42:name")      -> "Alice"
    kv.delete("user:42:name")
    kv.get("user:42:name")      -> None

Example 2 (overwrite semantics):
    kv = SimpleKV()
    kv.put("k", "v1")
    kv.put("k", "v2")           # second put replaces first
    kv.get("k")                  -> "v2"

Example 3 (falsy values are preserved — not confused with "missing"):
    kv = SimpleKV()
    kv.put("count", 0)
    kv.get("count")              -> 0    (NOT None — 0 is a real value)
    kv.put("flag", False)
    kv.get("flag")               -> False (NOT None — False is a real value)
    Explanation: a naive ``return self._store.get(key) or None`` would
    turn 0 / False / "" into None. The contract is "None means absent" —
    a stored 0 must remain 0.

Modeled on the classic "KV Store — progressive design" interview
question. Related LeetCode references: #981 (Time-Based KV Store —
exact match for Tier 3), #146 (LRU Cache — eviction foundation),
#1797 (Authentication Manager — TTL pattern), #460 (LFU Cache —
advanced eviction), #432 (All O'one Data Structure).

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import time
from typing import Any, Callable


class SimpleKV:
    """Tier 1: in-memory dict with put / get / delete.

    The textbook baseline. Single-threaded; no expiry, no versioning,
    no concurrency. Every operation is O(1) on a Python ``dict``.

    Input:
        __init__()
        put(key: str, value: Any) -> None
        get(key: str) -> Any | None
        delete(key: str) -> None
    Output:
        get returns the stored value or None. None is the sentinel
        for "key is not in the store."

    Example 1 (basic CRUD):
        kv = SimpleKV()
        kv.put("a", 1); kv.put("b", 2)
        kv.get("a")  -> 1
        kv.get("c")  -> None
        kv.delete("a")
        kv.get("a")  -> None

    Example 2 (idempotent delete):
        kv = SimpleKV()
        kv.delete("never_existed")    # no exception; no-op
        kv.put("k", "v")
        kv.delete("k")
        kv.delete("k")                # second delete also a no-op

    Example 3 (falsy values are real values):
        kv = SimpleKV()
        kv.put("zero", 0)
        kv.put("empty_list", [])
        kv.put("none_value", None)    # storing None EXPLICITLY
        kv.get("zero")        -> 0
        kv.get("empty_list")  -> []
        kv.get("none_value")  -> None  # AMBIGUOUS — same as "missing"
        Explanation: storing None makes the contract ambiguous with
        "missing." Production KVs solve this by adding a separate
        ``contains(key) -> bool``. Tier 1 doesn't bother — None is
        treated as "absent" — but a real KV would.

    Standard library:
        dict — Python's hash table. O(1) expected for get/put/delete.

    Pseudocode:
        data:
            store — dict[str, Any] (initially empty).

        put(key, value):
            store[key] = value

        get(key):
            return store.get(key)            # returns None on miss

        delete(key):
            store.pop(key, None)             # no-op on miss

    Why ``dict.pop(key, None)`` instead of ``del store[key]``:
        ``del`` raises KeyError on missing keys. ``pop(key, None)`` is
        the idiomatic "drop if present" — same as set.discard. Makes
        delete idempotent without a manual ``if key in store`` guard.

    Why ``dict.get(key)`` instead of ``store[key]``:
        Mirror of the above. ``store[key]`` raises on miss; ``get(key)``
        returns None. Production code may want the exception (to
        distinguish missing from None-value); this learning tier
        accepts the ambiguity.

    Complexity:
        Storage: O(N) where N is the number of stored keys.
        put, get, delete: O(1) expected (hash collisions are worst-case O(N)).
    """

    def __init__(self):
        self._store: dict[str, Any] = {}

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value

    def get(self, key: str) -> Any | None:
        return self._store.get(key)

    def delete(self, key: str) -> None:
        self._store.pop(key, None)


class TTLKV:
    """Tier 2: KV store with per-key TTL (time-to-live).

    Adds expiry to the Tier 1 surface. Each value carries an
    ``expires_at`` deadline measured against an injected monotonic
    clock. Reads that find an expired value evict-and-return-None
    ("lazy expiry"). An explicit ``sweep_expired()`` method drops
    expired keys that nobody has read ("active expiry") — useful
    for reclaiming memory or before iterating live keys.

    Input:
        __init__(clock: Callable[[], float] = time.monotonic)
            clock — zero-arg callable returning the current monotonic
                time in seconds. Pass a fake clock in tests; defaults
                to ``time.monotonic`` (immune to wall-clock jumps).
        put(key: str, value: Any, ttl_seconds: float | None = None) -> None
            ttl_seconds — relative seconds until expiry.
                None = no expiry (key lives forever).
                A second put on the same key replaces value AND TTL.
        get(key: str) -> Any | None
            Returns the stored value, or None if missing OR expired.
            Expired-at-read also evicts the key as a side effect.
        delete(key: str) -> None
            Same as Tier 1. Works on missing or expired keys (no-op).
        sweep_expired() -> int
            Drops every key whose expiry has passed. Returns the
            count of keys evicted. O(N) in the store size.

    Output:
        get returns the stored value, None on miss, None on expired.
        sweep_expired returns the number of evicted keys.

    Example 1 (basic TTL — value disappears after expiry):
        clock = FakeClock(t=0.0)
        kv = TTLKV(clock=clock)
        kv.put("session:42", "Alice", ttl_seconds=5)
        kv.get("session:42")        -> "Alice"     (t=0, fresh)
        clock.advance(3)
        kv.get("session:42")        -> "Alice"     (t=3, 2s remaining)
        clock.advance(3)
        kv.get("session:42")        -> None        (t=6, expired)

    Example 2 (lazy expiry — read evicts as a side effect):
        clock = FakeClock(t=0.0)
        kv = TTLKV(clock=clock)
        kv.put("k", "v", ttl_seconds=5)
        clock.advance(10)           # k still IN storage, but expired
        kv.get("k")                  -> None       (evicts as side effect)
        # No second get needed — k is already gone from internal storage.
        Explanation: lazy means the eviction happens *when somebody
        notices* — i.e. on the next read. Until that read, expired
        entries take up memory. That's the trade-off vs scanning on
        every put.

    Example 3 (sweep_expired — active eviction of unread keys):
        clock = FakeClock(t=0.0)
        kv = TTLKV(clock=clock)
        kv.put("a", 1, ttl_seconds=1)
        kv.put("b", 2, ttl_seconds=5)
        kv.put("c", 3)               # no TTL; never expires
        clock.advance(3)
        kv.sweep_expired()           -> 1        (a evicted; b, c remain)
        clock.advance(5)
        kv.sweep_expired()           -> 1        (b evicted; c remains)
        kv.get("c")                  -> 3

    Example 4 (overwrite resets TTL):
        clock = FakeClock(t=0.0)
        kv = TTLKV(clock=clock)
        kv.put("k", "v1", ttl_seconds=5)
        kv.put("k", "v2")            # no TTL — overwrites both value AND expiry
        clock.advance(100)
        kv.get("k")                  -> "v2"     (still here — no expiry)
        Explanation: matches Redis SET semantics — a fresh put resets
        the expiry state. Re-applying TTL requires re-passing
        ttl_seconds. (Production Redis has SET ... KEEPTTL to opt out;
        we don't.)

    Standard library:
        dict — same hash table as Tier 1.
        time.monotonic — the default clock. Non-decreasing; not
            affected by NTP / DST / manual clock changes.
        typing.Callable — type hint for the injected clock.

    Pseudocode:
        data:
            store — dict[str, (value, expires_at)]
                expires_at is None for non-expiring values.
            clock — zero-arg callable returning current monotonic time.

        is_expired(entry):
            value, expires_at = entry
            return expires_at is not None and clock() >= expires_at

        put(key, value, ttl_seconds=None):
            expires_at = (clock() + ttl_seconds) if ttl_seconds is not None else None
            store[key] = (value, expires_at)

        get(key):
            entry = store.get(key)
            if entry is None:
                return None
            if is_expired(entry):
                store.pop(key, None)            # lazy eviction
                return None
            value, _ = entry
            return value

        delete(key):
            store.pop(key, None)

        sweep_expired():
            expired_keys = [k for k, e in store.items() if is_expired(e)]
            for k in expired_keys:
                store.pop(k, None)
            return len(expired_keys)

    Why a tuple ``(value, expires_at)`` instead of two parallel dicts:
        Single hash lookup. Two dicts would require synchronized
        insert / delete across both and double the lookups. The tuple
        co-locates the bookkeeping with the value.

    Why ``expires_at = None`` (not ``math.inf``) for non-expiring:
        Distinguishes "never expires" from a numerical sentinel that
        could compare unexpectedly. ``is_expired`` short-circuits on
        the None check before any arithmetic — clearer intent.

    Why ``time.monotonic`` (not ``time.time``):
        ``time.time`` is wall-clock — affected by NTP adjustments,
        DST, and manual clock changes. A leap-second adjustment could
        make a 5-second TTL expire in 4 seconds or 6. ``monotonic``
        is guaranteed non-decreasing.

    Why an injected clock (not hardcoded ``time.monotonic``):
        Tests fast-forward time by calling ``clock.advance(seconds)``
        instead of ``time.sleep(seconds)``. Fully deterministic,
        instant, no thread races. The injected-callable pattern is
        the standard "dependency for time" pattern in production
        Python — see e.g. ``cachetools``, ``apscheduler``.

    Why an explicit ``sweep_expired()`` (not a background thread):
        A background thread would introduce concurrency (Tier 4
        territory) one tier earlier than planned. Lazy-on-read
        already prevents serving stale data; ``sweep_expired()``
        only matters for memory reclamation and live-key iteration,
        both of which a caller can schedule when convenient.

    Why lazy + sweeper (not eager-on-every-op):
        Eager expiry on every put / get would scan the whole store —
        O(N) on each operation. Lazy keeps reads O(1) and defers
        cleanup. ``sweep_expired()`` lets the caller pick when to
        pay the O(N) cost (e.g., off-peak, or before snapshotting).

    Complexity:
        Storage: O(N) — one tuple slot per key.
        put, get, delete: O(1) expected.
        sweep_expired: O(N) — must visit every key.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        # Each entry is (value, expires_at). expires_at is None for keys
        # that never expire; otherwise it is an absolute time on `clock`.
        self._store: dict[str, tuple[Any, float | None]] = {}
        self._clock = clock

    def _is_expired(self, entry: tuple[Any, float | None]) -> bool:
        _value, expires_at = entry
        # `>=` (not `>`): a TTL of N seconds expires exactly at t = N.
        return expires_at is not None and self._clock() >= expires_at

    def put(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        expires_at = self._clock() + ttl_seconds if ttl_seconds is not None else None
        # A fresh put replaces value AND expiry (Redis SET semantics).
        self._store[key] = (value, expires_at)

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._is_expired(entry):
            # Lazy eviction: a read that finds an expired value drops it.
            self._store.pop(key, None)
            return None
        value, _expires_at = entry
        return value

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def sweep_expired(self) -> int:
        # Two-phase: collect the expired keys first, then delete them. You
        # cannot pop from a dict while iterating it (RuntimeError), so the
        # comprehension materializes the hit-list before the mutation pass.
        expired = [k for k, entry in self._store.items() if self._is_expired(entry)]
        for k in expired:
            self._store.pop(k, None)
        return len(expired)


class TransactionError(Exception):
    """Raised on an illegal transaction state transition.

    Two cases: ``begin()`` while a transaction is already open, and
    ``commit()`` / ``rollback()`` while no transaction is open. (A
    'flat single-level' store has no nesting, so a second ``begin``
    is illegal rather than a push onto a stack.)
    """


# A unique sentinel marking "deleted inside the current transaction." It must
# be distinct from every real value AND from None (None is a legal stored
# value and the missing-key signal), so we use a fresh object() compared with
# `is` — never `==`.
_TOMBSTONE: Any = object()


class TransactionalKV:
    """Tier 3a: KV store with flat (single-level) transactions.

    Adds ``begin()`` / ``commit()`` / ``rollback()`` to the
    put / get / delete surface. A transaction buffers its writes and
    deletes in an *overlay* dict; reads consult the overlay first,
    then fall through to the committed *base* store. ``commit()``
    merges the overlay into the base; ``rollback()`` discards it.

    Flat single-level: at most one transaction is open at a time.
    ``begin()`` while already in a transaction raises TransactionError;
    so do ``commit()`` / ``rollback()`` with no transaction open. (This
    is the depth-≤-1 case of the general overlay-stack — a nested
    variant would promote the single overlay to a list[dict].)

    Outside a transaction the store is in "autocommit" mode: writes go
    straight to the base, exactly like SimpleKV.

    Input:
        __init__()
        put(key: str, value: Any) -> None
            In a transaction: buffer the write in the overlay.
            Otherwise: write straight to the base store.
        get(key: str) -> Any | None
            Overlay first (honouring delete-tombstones), then base.
            None on miss.
        delete(key: str) -> None
            In a transaction: record a tombstone in the overlay.
            Otherwise: drop from the base store.
        begin() -> None
            Open a transaction. Raises if one is already open.
        commit() -> None
            Apply the overlay to the base, then close. Raises if no
            transaction is open.
        rollback() -> None
            Discard the overlay, then close. Raises if no transaction
            is open.
    Output:
        get returns the value visible at the current transaction
        level, or None if missing / deleted-in-transaction.

    Example 1 (commit makes writes durable):
        kv = TransactionalKV()
        kv.put("a", 1)              # autocommit → base = {a:1}
        kv.begin()
        kv.put("a", 2)              # buffered → overlay = {a:2}
        kv.get("a")     -> 2        # overlay hit
        kv.commit()                 # base = {a:2}, overlay closed
        kv.get("a")     -> 2        # base hit

    Example 2 (rollback discards uncommitted writes):
        kv = TransactionalKV()
        kv.put("a", 1)
        kv.begin()
        kv.put("a", 99)             # overlay = {a:99}
        kv.get("a")     -> 99
        kv.rollback()               # overlay discarded
        kv.get("a")     -> 1        # base never changed

    Example 3 (delete-in-transaction uses a TOMBSTONE):
        kv = TransactionalKV()
        kv.put("a", 1)              # base = {a:1}
        kv.begin()
        kv.delete("a")              # overlay = {a: TOMBSTONE}
        kv.get("a")     -> None     # tombstone — does NOT fall through to base!
        kv.rollback()
        kv.get("a")     -> 1        # delete was rolled back
        Explanation: the overlay can't represent "deleted" by *omitting*
        the key — omission means "not touched in this txn, fall through
        to base." A distinct TOMBSTONE sentinel is required so get()
        knows to stop and return None instead of reading the base.

    Example 4 (illegal state transitions raise):
        kv = TransactionalKV()
        kv.commit()                 # raises TransactionError (no txn)
        kv.begin()
        kv.begin()                  # raises TransactionError (already open)

    Standard library:
        dict — base store and overlay. O(1) expected per op.

    Pseudocode:
        data:
            base       — dict[str, Any]; committed values.
            txn        — dict[str, Any] | None; the overlay.
                         None means "no transaction open" (autocommit).
            TOMBSTONE  — a unique sentinel object marking a delete
                         buffered inside a transaction. Create once with
                         ``object()``; compare with ``is``.

        put(key, value):
            if txn is not None:
                txn[key] = value
            else:
                base[key] = value

        get(key):
            if txn is not None and key in txn:
                v = txn[key]
                return None if v is TOMBSTONE else v
            return base.get(key)

        delete(key):
            if txn is not None:
                txn[key] = TOMBSTONE         # record, don't pop
            else:
                base.pop(key, None)

        begin():
            if txn is not None:
                raise TransactionError("transaction already open")
            txn = {}

        commit():
            if txn is None:
                raise TransactionError("no transaction open")
            for key, v in txn.items():
                if v is TOMBSTONE:
                    base.pop(key, None)
                else:
                    base[key] = v
            txn = None

        rollback():
            if txn is None:
                raise TransactionError("no transaction open")
            txn = None

    Why a single nullable overlay (not a list stack):
        'Flat single-level' caps open transactions at one, so the
        stack has depth ≤ 1. A nullable dict captures that exactly and
        keeps every method a two-branch check (in-txn vs autocommit).
        The list[dict] form only pays off once nesting is allowed.

    Why a TOMBSTONE sentinel (not None, not key removal):
        get() falls through to base when a key is *absent* from the
        overlay. So a buffered delete cannot be "remove key from
        overlay" — that would re-expose the base value. It also can't
        be ``overlay[key] = None`` because None is a legal stored value
        AND the missing-key signal. A fresh ``object()`` is guaranteed
        distinct from every real value, so ``is TOMBSTONE`` is
        unambiguous.

    Why raise (rather than no-op) on illegal transitions:
        commit/rollback with no open transaction, or a double begin,
        signals a caller bug — the transaction bookkeeping is out of
        sync with what the caller thinks is happening. Raising surfaces
        it immediately; a silent no-op would mask it. (A no-op variant
        is defensible for REPL-style use; this tier chooses the strict
        contract.)

    Complexity:
        put, get, delete, begin, rollback: O(1) expected.
        commit: O(M) where M = number of keys touched in the overlay.
        Storage: O(N + M) — base plus the overlay's buffered changes.
    """

    def __init__(self):
        self._base: dict[str, Any] = {}
        # The overlay. None means "no transaction open" (autocommit mode);
        # otherwise it holds this transaction's buffered writes and tombstones.
        self._txn: dict[str, Any] | None = None

    def put(self, key: str, value: Any) -> None:
        if self._txn is not None:
            self._txn[key] = value
        else:
            self._base[key] = value

    def get(self, key: str) -> Any | None:
        # Overlay first (honouring tombstones), then fall through to base.
        if self._txn is not None and key in self._txn:
            buffered = self._txn[key]
            return None if buffered is _TOMBSTONE else buffered
        return self._base.get(key)

    def delete(self, key: str) -> None:
        if self._txn is not None:
            # Record a tombstone rather than removing the key: an *absent*
            # overlay key means "fall through to base", which would wrongly
            # re-expose the value we are trying to delete.
            self._txn[key] = _TOMBSTONE
        else:
            self._base.pop(key, None)

    def begin(self) -> None:
        if self._txn is not None:
            raise TransactionError("transaction already open")
        self._txn = {}

    def commit(self) -> None:
        if self._txn is None:
            raise TransactionError("no transaction open")
        # Apply every buffered change to the base in one pass. Use `is
        # _TOMBSTONE` (identity), never a truthiness test — a buffered 0 /
        # "" / False is a real value that must survive the commit.
        for key, buffered in self._txn.items():
            if buffered is _TOMBSTONE:
                self._base.pop(key, None)
            else:
                self._base[key] = buffered
        self._txn = None

    def rollback(self) -> None:
        if self._txn is None:
            raise TransactionError("no transaction open")
        # Discard the overlay wholesale — the base was never touched.
        self._txn = None
