"""SnapshotSet — a snapshot-isolated set (systems interview).

A generic set whose `snapshot()` hands back a *version-stable* view: the
snapshot's reads reflect the set's contents AT THE MOMENT the snapshot
was taken, and stay frozen there no matter how the live set is mutated
afterwards. This is the set-flavored sibling of LeetCode #1146 (Snapshot
Array) and #981 (Time-Based KV Store): #1146 snapshots an indexed array
of values, we snapshot membership of a set.

The live set carries the ordinary operations; the snapshot is the new
idea:

    add(x) / remove(x) / contains(x)  →  live, mutable set
    items() / __iter__                →  iterate the *live* set
    snapshot() -> Snapshot            →  a frozen, isolated view

Input (live set):
    add(x: str) -> None
        Insert x. Idempotent — adding a present element is a no-op.
    remove(x: str) -> None
        Drop x. Idempotent — removing an absent element is a no-op.
    contains(x: str) -> bool
        Is x in the *live* set right now?
    items() -> set[str] / __iter__ -> Iterator[str]
        The live set's current contents.
    snapshot() -> Snapshot
        A handle whose contains(x) / items() / iterator() report the
        live set's contents as they were *when snapshot() was called*.

Output (snapshot handle):
    Snapshot.contains(x: str) -> bool   — membership as-of the snapshot.
    Snapshot.items() -> set[str]        — the frozen contents.
    Snapshot.iterator() -> Iterator[str] — iterate the frozen contents.

Example 1 (the core guarantee — isolation from later mutation):
    s = SimpleSnapshotSet()
    s.add("a"); s.add("b")
    snap = s.snapshot()          # snap captures {a, b}
    s.add("c")                   # mutate the LIVE set afterwards
    s.remove("a")
    sorted(s.items())            -> ["b", "c"]      (live moved on)
    sorted(snap.items())         -> ["a", "b"]      (snapshot frozen)
    snap.contains("a")           -> True            (a was present then)
    snap.contains("c")           -> False           (c added after)

Example 2 (independent snapshots see independent points in time):
    s = SimpleSnapshotSet()
    s.add(1)
    snap1 = s.snapshot()         # {1}
    s.add(2)
    snap2 = s.snapshot()         # {1, 2}
    s.add(3)
    sorted(snap1.items())        -> [1]
    sorted(snap2.items())        -> [1, 2]
    sorted(s.items())            -> [1, 2, 3]

Example 3 (a snapshot of the empty set stays empty forever):
    s = SimpleSnapshotSet()
    snap = s.snapshot()          # {}
    s.add("late")
    snap.contains("late")        -> False
    list(snap.iterator())        -> []

Four tiers escalate the snapshot *mechanism*; all share the live-set
surface and the isolation guarantee.

Tier 1 — SimpleSnapshotSet:      copy-on-snapshot — snapshot() copies the
                                 whole set. Dead simple, O(N) per snap.
Tier 2 — CoWSnapshotSet:         copy-on-write versioning — snapshot() is
                                 an O(1) captured version number; reads
                                 binary-search per-element history (the
                                 LeetCode #1146 technique). No full copy.
Tier 3 — GCSnapshotSet:          Tier 2 plus refcounted garbage collection
                                 — released snapshots let old versions be
                                 reclaimed, bounding memory growth.
Tier 4 — DistributedSnapshotSet: HLD only (see README) — MVCC across
                                 nodes, the system-design follow-up.

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

from bisect import bisect_right
from typing import Iterator


class SimpleSnapshot:
    """The frozen view handed back by SimpleSnapshotSet.snapshot().

    Holds its own immutable copy of the set's contents — a ``frozenset``
    taken at snapshot time — so nothing the live set does afterwards can
    reach it. Every read just consults that private copy.

    Input:
        contains(x: str) -> bool — membership in the frozen snapshot.
        items() -> set[str]      — a fresh copy of the frozen contents.
        iterator() -> Iterator[str] — iterate the frozen contents.
    Output:
        Reads reflect the live set exactly as it was when snapshot()
        was called; later live mutations never change them.

    Example:
        s = SimpleSnapshotSet(); s.add("a")
        snap = s.snapshot()
        s.add("b")                  # live set moves on
        snap.contains("a")  -> True
        snap.contains("b")  -> False   # added after the snapshot
        sorted(snap.items()) -> ["a"]
    """

    def __init__(self, contents: frozenset[str]) -> None:
        # A frozenset is immutable, so storing the reference is enough —
        # there is nothing to defensively copy and no way for a caller to
        # mutate it. (items() still hands out a fresh mutable set so a
        # caller can't even try.)
        self._contents = contents

    def contains(self, x: str) -> bool:
        return x in self._contents

    def items(self) -> set[str]:
        # Return a mutable copy so the caller can do as they like without
        # touching the snapshot's frozen contents.
        return set(self._contents)

    def iterator(self) -> Iterator[str]:
        return iter(self._contents)

    def __iter__(self) -> Iterator[str]:
        return iter(self._contents)


class SimpleSnapshotSet:
    """Tier 1: snapshot-isolated set by *copying on snapshot*.

    Input:
        add(x: str) -> None       — insert x (idempotent).
        remove(x: str) -> None    — drop x (idempotent).
        contains(x: str) -> bool  — membership in the live set.
        items() -> set[str]       — a copy of the live contents.
        __iter__() -> Iterator[str] — iterate the live contents.
        snapshot() -> SimpleSnapshot — a frozen view of the live set
            as it is right now.
    Output:
        Live reads track the live set; the Snapshot's reads stay frozen
        at the contents present when snapshot() was called.

    Example 1 (isolation — the whole point):
        s = SimpleSnapshotSet()
        s.add("a"); s.add("b")
        snap = s.snapshot()          # snapshot captures {a, b}
        s.add("c"); s.remove("a")    # mutate the live set after
        sorted(s.items())     -> ["b", "c"]
        sorted(snap.items())  -> ["a", "b"]
        snap.contains("a")    -> True

    Example 2 (idempotent add / remove):
        s = SimpleSnapshotSet()
        s.add(7); s.add(7)           # second add changes nothing
        s.contains(7)         -> True
        s.remove(7); s.remove(7)     # second remove is a no-op
        s.contains(7)         -> False

    Example 3 (two snapshots, two frozen points in time):
        s = SimpleSnapshotSet()
        s.add(1); snap1 = s.snapshot()   # {1}
        s.add(2); snap2 = s.snapshot()   # {1, 2}
        sorted(snap1.items()) -> [1]
        sorted(snap2.items()) -> [1, 2]

    The baseline: keep the live set as a plain ``set``; on snapshot()
    freeze a full copy into a ``frozenset`` and wrap it in a Snapshot.
    Because the snapshot owns a *separate* copy, later live mutations
    cannot reach it — isolation is structural, nothing clever required.

    Standard library:
        set — the live set. O(1) expected add / discard / membership.
        frozenset — an immutable snapshot of the live set. Copying into
            one is O(N); afterwards it cannot be mutated by anyone.

    Pseudocode:
        data:
            live — set[str] (the mutable working set).

        add(x):     live.add(x)
        remove(x):  live.discard(x)        # discard, not remove: no raise
        contains(x): return x in live
        items():    return set(live)        # fresh copy — caller can't alias
        __iter__(): return iter(set(live))  # iterate a copy — see "Why"

        snapshot():
            return Snapshot(frozenset(live))   # O(N) deep copy, frozen

    Why ``set.discard`` (not ``set.remove``):
        ``remove`` raises KeyError on an absent element; ``discard`` is
        the no-op-if-missing form. The contract makes remove idempotent,
        so discard matches it without a manual ``if x in live`` guard.

    Why snapshot copies into a ``frozenset`` (not just ``set(live)``):
        A frozenset cannot be mutated at all — so the snapshot's contents
        are immutable by construction, and ``items()`` handing out copies
        is the only path to a mutable view. A plain ``set(live)`` copy
        would also be isolated from the live set, but could be mutated by
        whoever holds the Snapshot. frozenset states the intent: frozen.

    Why ``__iter__`` iterates a copy of the live set:
        Iterating the live ``set`` directly would raise "set changed size
        during iteration" if the caller adds/removes mid-loop. Iterating a
        snapshot of the contents sidesteps that. (This is live iteration,
        not snapshot isolation — for a stable view across time, take a
        snapshot.)

    Complexity:
        add, remove, contains: O(1) expected.
        items, __iter__:       O(N) — copy the live set.
        snapshot():            O(N) — copy the whole set into a frozenset.
        Storage: O(N) live + O(N) per outstanding snapshot — every
            snapshot keeps a full, independent copy. With K snapshots of
            an N-element set that is O(K·N): the weakness Tier 2 attacks.
    """

    def __init__(self) -> None:
        self._live: set[str] = set()

    def add(self, x: str) -> None:
        self._live.add(x)

    def remove(self, x: str) -> None:
        # discard, not remove — absent element is a silent no-op.
        self._live.discard(x)

    def contains(self, x: str) -> bool:
        return x in self._live

    def items(self) -> set[str]:
        # Hand out a copy so the caller cannot mutate our live set.
        return set(self._live)

    def __iter__(self) -> Iterator[str]:
        # Iterate a copy: protects the caller from "set changed size
        # during iteration" if they mutate the live set mid-loop.
        return iter(set(self._live))

    def snapshot(self) -> SimpleSnapshot:
        # The whole mechanism: copy everything into an immutable frozenset.
        # O(N), but the snapshot is now structurally isolated.
        return SimpleSnapshot(frozenset(self._live))


# A version record for the copy-on-write tiers. Each element keeps a list
# of these, one per membership change: "at version `version`, the element
# became present (True) or absent (False)". The list is kept sorted by
# version (it is append-only and versions only increase), so a read can
# binary-search it. This is the per-key version history of LeetCode #1146,
# specialized to a boolean (present/absent) instead of an arbitrary value.
#
# We store it as a plain tuple (version, present) to keep the structure
# transparent; ``_VersionRecord`` is just an alias for documentation.
_VersionRecord = tuple[int, bool]


class CoWSnapshot:
    """The frozen view handed back by CoWSnapshotSet.snapshot().

    Unlike Tier 1's snapshot, this holds NO copy of the data — only a
    captured version number and a back-reference to the parent set. Every
    read asks the parent "what did element x look like at version V?",
    which the parent answers by binary-searching x's version history.
    Isolation comes from the version, not a copy: no write ever lands at a
    version ≤ V (snapshot() freezes the version by bumping the global
    counter), so the answer for version V is permanently fixed.

    Input:
        contains(x: str) -> bool    — membership as-of the captured version.
        items() -> set[str]         — every element present at that version.
        iterator() -> Iterator[str] — iterate those elements.
    Output:
        Reads reflect the parent set as it was at the captured version;
        later live mutations occur at higher versions and never change it.

    Example:
        s = CoWSnapshotSet(); s.add("a")
        snap = s.snapshot()         # captures version V with {a} present
        s.add("b")                  # lands at version > V
        snap.contains("a")  -> True
        snap.contains("b")  -> False
    """

    def __init__(self, owner: "CoWSnapshotSet", version: int) -> None:
        self._owner = owner
        self._version = version

    def contains(self, x: str) -> bool:
        return self._owner._present_at(x, self._version)

    def items(self) -> set[str]:
        return self._owner._items_at(self._version)

    def iterator(self) -> Iterator[str]:
        return iter(self._owner._items_at(self._version))

    def __iter__(self) -> Iterator[str]:
        return iter(self._owner._items_at(self._version))


class CoWSnapshotSet:
    """Tier 2: snapshot isolation by copy-on-write *versioning*.

    Input:
        add(x: str) -> None       — insert x at the current version.
        remove(x: str) -> None    — drop x at the current version.
        contains(x: str) -> bool  — membership in the live set.
        items() -> set[str]       — a copy of the live contents.
        __iter__() -> Iterator[str] — iterate the live contents.
        snapshot() -> CoWSnapshot — an O(1) handle capturing the
            current version; its reads look up membership as-of that
            version.
    Output:
        Live reads track the live set; a Snapshot's reads stay frozen at
        the version captured when snapshot() was called.

    Example 1 (isolation, with no copy taken):
        s = CoWSnapshotSet()
        s.add("a"); s.add("b")
        snap = s.snapshot()          # O(1): just captures the version
        s.add("c"); s.remove("a")
        sorted(s.items())     -> ["b", "c"]
        sorted(snap.items())  -> ["a", "b"]
        snap.contains("a")    -> True

    Example 2 (re-adding after a remove is a fresh interval):
        s = CoWSnapshotSet()
        s.add("x")
        snap1 = s.snapshot()         # x present
        s.remove("x")
        snap2 = s.snapshot()         # x absent
        s.add("x")
        snap3 = s.snapshot()         # x present again
        snap1.contains("x")  -> True
        snap2.contains("x")  -> False
        snap3.contains("x")  -> True

    Example 3 (many snapshots are cheap — O(1) each):
        s = CoWSnapshotSet()
        s.add(1)
        snaps = [s.snapshot() for _ in range(1000)]   # all share history
        s.add(2)
        all(snap.contains(1) for snap in snaps)  -> True
        any(snap.contains(2) for snap in snaps)  -> False

    The Tier 1 weakness is that each snapshot copies the whole set. Here
    a snapshot is just a captured integer version, taken in O(1) and
    sharing one append-only history with every other snapshot.

    The model (LeetCode #1146, SnapshotArray, specialized to membership):
    a global ``version`` counter, and per element an append-only list of
    ``(version, present)`` records — one per membership flip. A write at
    the current version appends a record (or rewrites the last one if it
    is already at this version). snapshot() captures the current version
    and bumps the counter, so subsequent writes land strictly higher and
    the snapshot's version can never be overwritten. A read "is x present
    at version V?" binary-searches x's history for the last record with
    ``record_version ≤ V`` and returns its present-flag.

    Standard library:
        bisect.bisect_right — binary search over a sorted list. Given x's
            history sorted by version, ``bisect_right(versions, V)`` is the
            insertion point just past V; the record at index-1 is the
            latest one with ``version ≤ V`` — the membership in effect at
            version V. O(log H) over H history entries.
        dict / set — the history map and the live set.

    Pseudocode:
        data:
            version — int, the global clock (starts at 0).
            history — dict[str, list[(version, present)]], append-only,
                      sorted by version per element.
            live    — set[str], the current membership (a convenience cache
                      so live reads stay O(1); the history is the source
                      of truth for snapshots).

        _record(x, present):           # append or rewrite at current version
            h = history.setdefault(x, [])
            if h and h[-1].version == version:
                h[-1] = (version, present)     # same version → overwrite
            else:
                h.append((version, present))

        add(x):
            if x in live: return            # idempotent: no version churn
            live.add(x);   _record(x, True)

        remove(x):
            if x not in live: return        # idempotent
            live.discard(x); _record(x, False)

        contains(x):  return x in live      # live read, O(1)
        items():      return set(live)

        snapshot():
            v = version
            version += 1                    # freeze v: future writes land > v
            return Snapshot(self, v)

        present_at(x, V):                   # was x present at version V?
            h = history.get(x)
            if not h: return False
            i = bisect_right([rec.version for rec in h], V)
            if i == 0: return False         # x's first record is after V
            return h[i - 1].present

        items_at(V):
            return {x for x in history if present_at(x, V)}

    Why snapshot() *increments* the version (instead of every write
    incrementing it):
        Bumping per write would explode the history (a record per op) and
        make snapshots that nobody took. Bumping on snapshot() means a
        version number only exists because a snapshot pinned it, and all
        writes between two snapshots collapse onto one version — exactly
        SnapshotArray's ``snap()`` semantics. The bump is what guarantees
        isolation: no write can ever modify a version a snapshot holds.

    Why "rewrite the last record if it's at the current version":
        Several adds/removes can happen at the same version (between two
        snapshots). Only the net result at that version matters to any
        snapshot, so we overwrite rather than append duplicates — keeping
        one record per (element, version) and the history compact.

    Why keep a live ``set`` alongside the history:
        Live contains/iter are the common path and should stay O(1)/O(N);
        deriving them from the history would cost a binary search per
        element. The set is a cache of "membership at the current
        version"; the history is the source of truth for past versions.

    Complexity:
        add, remove: O(1) amortized (set op + list append).
        contains, items (live): O(1) / O(N).
        snapshot(): O(1).
        Snapshot.contains(x): O(log H_x) — binary-search x's history.
        Snapshot.items(): O(E · log H) — search every element's history.
        Storage: O(total membership changes) — the history is
            append-only and is NEVER reclaimed, even after every snapshot
            referencing an old version is gone. That unbounded growth is
            the weakness Tier 3 attacks with refcounted GC.
    """

    def __init__(self) -> None:
        self._version = 0
        self._history: dict[str, list[_VersionRecord]] = {}
        self._live: set[str] = set()

    def _record(self, x: str, present: bool) -> None:
        # Append a (version, present) record for x — or, if x's last record
        # is already at the current version (multiple writes between two
        # snapshots), overwrite it so there is one record per version.
        h = self._history.setdefault(x, [])
        if h and h[-1][0] == self._version:
            h[-1] = (self._version, present)
        else:
            h.append((self._version, present))

    def add(self, x: str) -> None:
        if x in self._live:
            return  # idempotent — don't churn the history on a no-op add
        self._live.add(x)
        self._record(x, True)

    def remove(self, x: str) -> None:
        if x not in self._live:
            return  # idempotent — nothing to record
        self._live.discard(x)
        self._record(x, False)

    def contains(self, x: str) -> bool:
        return x in self._live

    def items(self) -> set[str]:
        return set(self._live)

    def __iter__(self) -> Iterator[str]:
        return iter(set(self._live))

    def snapshot(self) -> CoWSnapshot:
        # Capture the current version, then bump the global counter so
        # every future write lands at a strictly higher version. That bump
        # is what freezes this snapshot: no write can ever touch version v.
        v = self._version
        self._version += 1
        return CoWSnapshot(self, v)

    def _present_at(self, x: str, version: int) -> bool:
        # Was x present at the given version? Binary-search x's history for
        # the latest record at or before `version`; its present-flag is the
        # membership in effect then.
        h = self._history.get(x)
        if not h:
            return False
        # bisect_right on the version keys finds the insertion point just
        # past `version`; index-1 is the last record with version <= it.
        versions = [rec[0] for rec in h]
        i = bisect_right(versions, version)
        if i == 0:
            return False  # x's first record is after this version
        return h[i - 1][1]

    def _items_at(self, version: int) -> set[str]:
        # Every element whose membership-as-of `version` is present.
        return {x for x in self._history if self._present_at(x, version)}


class GCSnapshot:
    """The frozen view handed back by GCSnapshotSet.snapshot().

    Like Tier 2's CoWSnapshot — a captured version plus a back-reference,
    reads resolved by binary search — but it also participates in
    *reference counting*. While this handle is live it pins its version, so
    the owner may not garbage-collect history that handle could still need.
    Calling ``release()`` (or using the handle as a context manager) drops
    the pin, letting the owner reclaim versions no live snapshot references.

    Input:
        contains(x: str) -> bool    — membership as-of the captured version.
        items() -> set[str]         — elements present at that version.
        iterator() -> Iterator[str] — iterate those elements.
        release() -> None         — drop this snapshot's pin (idempotent).
        __enter__ / __exit__      — context-manager sugar for release().
    Output:
        Reads reflect the parent set as of the captured version. After
        release(), the handle must not be read again (its version may be
        reclaimed); reading a released handle raises RuntimeError.

    Example:
        s = GCSnapshotSet(); s.add("a")
        with s.snapshot() as snap:        # auto-released at block exit
            snap.contains("a")  -> True
        # snap is released here; its version is eligible for GC.
    """

    def __init__(self, owner: "GCSnapshotSet", version: int) -> None:
        self._owner = owner
        self._version = version
        self._released = False

    def _check_live(self) -> None:
        if self._released:
            raise RuntimeError("snapshot has been released")

    def contains(self, x: str) -> bool:
        self._check_live()
        return self._owner._present_at(x, self._version)

    def items(self) -> set[str]:
        self._check_live()
        return self._owner._items_at(self._version)

    def iterator(self) -> Iterator[str]:
        self._check_live()
        return iter(self._owner._items_at(self._version))

    def __iter__(self) -> Iterator[str]:
        self._check_live()
        return iter(self._owner._items_at(self._version))

    def release(self) -> None:
        # Idempotent: releasing twice is harmless, and we must not
        # decrement the owner's refcount more than once.
        if self._released:
            return
        self._released = True
        self._owner._release_version(self._version)

    def __enter__(self) -> "GCSnapshot":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class GCSnapshotSet:
    """Tier 3: Tier 2 versioning plus refcounted garbage collection.

    Input:
        add(x: str) -> None       — insert x at the current version.
        remove(x: str) -> None    — drop x at the current version.
        contains(x: str) -> bool  — membership in the live set.
        items() -> set[str]       — a copy of the live contents.
        __iter__() -> Iterator[str] — iterate the live contents.
        snapshot() -> GCSnapshot — an O(1) handle pinning the current
            version; ``release()`` (or its context manager) unpins it.
    Output:
        Same isolation guarantee as Tier 2; additionally, history older
        than every live snapshot is reclaimed once snapshots release,
        bounding memory.

    Example 1 (release lets old history be reclaimed):
        s = GCSnapshotSet()
        s.add("a")
        snap = s.snapshot()          # pins this version
        s.remove("a"); s.add("b")    # piles up history for "a" and "b"
        snap.release()               # nothing pins the old version now
        # The history for the long-dead "a"/"b" versions is collected;
        # only what the live set needs remains. (See live_history_size.)

    Example 2 (an unreleased snapshot keeps its view — GC is safe):
        s = GCSnapshotSet()
        s.add(1)
        keep = s.snapshot()          # stays live, pins its version
        s.add(2)
        throwaway = s.snapshot(); throwaway.release()
        sorted(keep.items())  -> [1] # still correct — its version is pinned
        s.contains(2)         -> True

    Example 3 (context-manager release):
        s = GCSnapshotSet()
        s.add("x")
        with s.snapshot() as snap:
            snap.contains("x")  -> True
        # snap auto-released at block exit; its version is collectible.

    Tier 2's history is append-only and never shrinks — K snapshots over a
    long-lived set grow it without bound, even after those snapshots are
    long gone. Tier 3 fixes that with reference counting: each live
    snapshot increments a count on its pinned version; ``release()``
    decrements it. The minimum version any live snapshot still pins is the
    ``gc horizon`` — every history record strictly older than the horizon
    (and superseded by a later record at or before it) can never be read
    by any surviving snapshot, so it is reclaimed.

    Standard library:
        bisect.bisect_right / bisect_left — binary search of the sorted
            per-element history (read path and the GC prune point).
        dict / set — history map, live set, and the refcount map
            ``pins: dict[version, count]``.

    Pseudocode:
        data:
            version, history, live  — as in Tier 2.
            pins — dict[int, int], live-snapshot refcount per version.

        snapshot():
            v = version
            version += 1
            pins[v] = pins.get(v, 0) + 1       # one more reader pins v
            return Snapshot(self, v)

        release_version(v):
            pins[v] -= 1
            if pins[v] == 0:
                del pins[v]
                gc()                            # the horizon may have moved

        gc_horizon():
            # the oldest version any live snapshot can still read
            return min(pins) if pins else version

        gc():
            h = gc_horizon()
            for x, records in history.items():
                # Find the last record at or before the horizon; everything
                # strictly before THAT is unreadable by any live snapshot
                # (a reader at the horizon already sees the kept record).
                keep_from = bisect_right(record_versions, h) - 1
                if keep_from > 0:
                    del records[:keep_from]     # drop superseded prefix
            # (also drop empty / all-absent histories no snapshot needs)

    Why refcount versions (not snapshots):
        Many snapshots can pin the *same* version (several snapshot()
        calls with no write between them all capture v). One counter per
        version, not per snapshot, collapses them; the version is
        collectible exactly when its count hits zero.

    Why the GC horizon is ``min(pinned versions)``:
        A live snapshot at version v needs, for every element, the record
        in effect at v — i.e. the latest record with ``version ≤ v``.
        Records strictly older than that are shadowed and unreadable. The
        oldest surviving reader sets the bar: anything a reader at
        ``min(pins)`` cannot see, no reader can see. With no live
        snapshots the horizon is the current version — all history is
        collectible down to what the live set needs.

    Why ``release()`` is explicit (and a context manager):
        Python has no destructor we can rely on promptly (``__del__`` is
        not deterministic). Snapshot lifetime must be caller-driven, so
        ``release()`` is explicit; the ``with`` form makes the common
        "use it then drop it" case clean and leak-free. This mirrors how a
        real MVCC store closes a read transaction to advance the GC
        watermark — see the README's Tier 4.

    Complexity:
        add, remove, contains, items, snapshot(): as Tier 2.
        release(): O(1) amortized; the gc() it can trigger is O(E · log H).
        Storage: O(live history reachable from the oldest live snapshot) —
            bounded by readers, no longer by total history. With no live
            snapshots it collapses to ~O(N) for the live set.
    """

    def __init__(self) -> None:
        self._version = 0
        self._history: dict[str, list[_VersionRecord]] = {}
        self._live: set[str] = set()
        # Refcount of live snapshots pinning each version. A version is
        # collectible once its count hits zero (the entry is then removed).
        self._pins: dict[int, int] = {}

    def _record(self, x: str, present: bool) -> None:
        h = self._history.setdefault(x, [])
        if h and h[-1][0] == self._version:
            h[-1] = (self._version, present)
        else:
            h.append((self._version, present))

    def add(self, x: str) -> None:
        if x in self._live:
            return
        self._live.add(x)
        self._record(x, True)

    def remove(self, x: str) -> None:
        if x not in self._live:
            return
        self._live.discard(x)
        self._record(x, False)

    def contains(self, x: str) -> bool:
        return x in self._live

    def items(self) -> set[str]:
        return set(self._live)

    def __iter__(self) -> Iterator[str]:
        return iter(set(self._live))

    def snapshot(self) -> GCSnapshot:
        # Capture and freeze the version (as Tier 2) and pin it: bump the
        # refcount so GC will not reclaim history this snapshot may read.
        v = self._version
        self._version += 1
        self._pins[v] = self._pins.get(v, 0) + 1
        return GCSnapshot(self, v)

    def _release_version(self, version: int) -> None:
        # Called by a Snapshot when it is released. Drop one pin; if the
        # version is now unpinned, it may have been the GC horizon, so run
        # collection to reclaim newly-unreachable history.
        count = self._pins.get(version, 0)
        if count <= 1:
            self._pins.pop(version, None)
        else:
            self._pins[version] = count - 1
        self._gc()

    def _gc_horizon(self) -> int:
        # The oldest version any live snapshot can still read. With no live
        # snapshots, the current version — all older history is collectible.
        if not self._pins:
            return self._version
        return min(self._pins)

    def _gc(self) -> None:
        # Reclaim history that no live snapshot can reach. For each element
        # keep the record in effect at the horizon plus everything after;
        # the strictly-older, shadowed prefix is unreadable and dropped.
        horizon = self._gc_horizon()
        empty_keys: list[str] = []
        for x, records in self._history.items():
            versions = [rec[0] for rec in records]
            # Index of the last record with version <= horizon. Everything
            # before it is shadowed for a reader sitting at the horizon.
            keep_from = bisect_right(versions, horizon) - 1
            if keep_from > 0:
                del records[:keep_from]
            # If the only surviving record says "absent" AND no snapshot
            # sits before the next change, the element carries no useful
            # history; drop it entirely when it is also not in the live set.
            if len(records) == 1 and not records[0][1] and x not in self._live:
                empty_keys.append(x)
            elif not records:
                empty_keys.append(x)
        for x in empty_keys:
            del self._history[x]

    def _present_at(self, x: str, version: int) -> bool:
        h = self._history.get(x)
        if not h:
            return False
        versions = [rec[0] for rec in h]
        i = bisect_right(versions, version)
        if i == 0:
            return False
        return h[i - 1][1]

    def _items_at(self, version: int) -> set[str]:
        return {x for x in self._history if self._present_at(x, version)}

    def live_history_size(self) -> int:
        """Total number of version records currently retained.

        A test/inspection hook (not part of the snapshot contract): it
        exposes how much history the set is holding, so the effect of GC —
        the whole point of Tier 3 — is observable. After releasing every
        snapshot it collapses toward the live set's size; without GC (Tier
        2) it would only ever grow.
        """
        return sum(len(records) for records in self._history.values())
