"""ID Allocator — concurrent-allocation interview problem (LeetCode #1845).

Design a class that hands out integer ids from a fixed range [0, max_id)
and reclaims them on release, so a freed id can be allocated again.

Input:
    max_id : int — constructor argument; ids are drawn from [0, max_id).
    allocate() takes no arguments.
    release(id) takes the int id to return to the pool.
Output:
    allocate() -> int | None — an id not currently in use, or None once
        every id in [0, max_id) is allocated.
    release(id) -> None.

Example 1:
    Input:
        ["Allocator", "allocate", "allocate", "release", "allocate"]
        [[3],         [],         [],         [0],       []]
    Output:
        [null,        0,          1,          null,      0]
    Explanation:
        Allocator(3)   // ids are drawn from [0, 3)
        allocate()     // -> 0
        allocate()     // -> 1
        release(0)     // 0 goes back to the pool
        allocate()     // -> 0   (the freed id is reused)

Example 2:
    Input:
        ["Allocator", "allocate", "allocate", "allocate"]
        [[2],         [],         [],         []]
    Output:
        [null,        0,          1,          null]
    Explanation:
        range [0, 2) holds two ids; once both are out, the third
        allocate() finds nothing free and returns null.

Constraints:
    - 0 <= id < max_id.
    - release(id) is only ever called with a currently-allocated id;
      validating that is out of scope (learning-mode rule).
    - The examples show the lowest-free-id-first behaviour of Tiers 2-3.
      Tier 1 returns ids in freelist order and Tier 4 returns a
      shard-local id — see each tier's docstring.

Four tiers trade memory, allocate-speed, and ordering against one
another, all behind the same allocate() / release() contract.

Tier 1 — Allocator:            freelist (deque) + bump counter; O(1) both.
Tier 2 — BitmapAllocator:      one packed bit per id; O(n) lowest-free scan.
Tier 3 — SegmentTreeAllocator: AND-summary tree; O(log n) lowest-free.
Tier 4 — ThreadSafeAllocator:  sharded Tier-3 trees, one lock per shard.

See README.md for the full tier table.
"""

import collections
import math
import random
import threading


class Allocator:
    """Tier 1: O(1) allocate and release over [0, max_id).

    Input:
        max_id : int — constructor arg; ids are drawn from [0, max_id).
        allocate() takes no arguments; release(id) takes the int to free.
    Output:
        allocate() -> int | None — a free id, or None once [0, max_id) is
            fully allocated.
        release(id) -> None.

    Example 1:
        Input:
            ["Allocator", "allocate", "allocate", "release", "allocate"]
            [[3],         [],         [],         [0],       []]
        Output:
            [null,        0,          1,          null,      0]
        Explanation:
            allocate() -> 0, allocate() -> 1, release(0) puts 0 back on
            the freelist, and the next allocate() reuses it -> 0.

    Example 2:
        Input:
            ["Allocator", "allocate", "allocate", "allocate"]
            [[2],         [],         [],         []]
        Output:
            [null,        0,          1,          null]
        Explanation:
            range [0, 2) holds two ids; the third allocate() finds the
            range exhausted and returns null.

    Example 3:  # Tier 1 reuses ids in freelist order, NOT lowest-first
        Input:
            ["Allocator", "allocate", "allocate", "allocate",
             "release", "release", "allocate", "allocate"]
            [[3], [], [], [], [1], [0], [], []]
        Output:
            [null, 0, 1, 2, null, null, 1, 0]
        Explanation:
            ids 0,1,2 are allocated, then 1 is released before 0. The
            freelist is FIFO, so the id released *first* is reused first:
            allocate() returns 1, then 0 — not the lowest free id. Tiers
            2-4 differ here (see SegmentTreeAllocator).

    Hands out integer ids and reclaims them on release. Three pieces of
    state: a freelist (deque) of released-and-reusable ids, a bump counter
    (max_seen) marking the highest id ever minted, and an in-use set so
    release knows an id is genuinely live.

    Allocation prefers the freelist; only when it is empty does the bump
    counter mint a brand-new id. That lazy minting is what keeps init
    O(1) — the range is never pre-filled.

    Standard library:
        collections.deque — a double-ended queue; append() and popleft()
            are both O(1), so it serves as the freelist (push a released
            id on one end, pop the next reuse off the other). A plain list
            would make popleft() O(n).
        set — O(1) membership; _in_use answers "is this id live?" so
            release() can ignore an id that was never allocated.

    Pseudocode:
        allocate():
            if freelist not empty:        # reuse a released id
                id = freelist.popleft()
            elif max_seen < max_id:       # mint a fresh id, lazily
                id = max_seen
                max_seen += 1
            else:
                return None               # whole range is handed out
            in_use.add(id)
            return id

        release(id):
            if id in in_use:
                in_use.remove(id)
                freelist.append(id)       # now reusable by allocate()

    Complexity: allocate and release are both O(1); construction is O(1)
    (no pre-fill). Space O(k) for k currently-or-formerly allocated ids.

    Trade-off: O(1) everywhere, but it spends a whole set on liveness and
    hands ids out in no useful order — Tier 2 trades that for packed
    storage and a lowest-id-first guarantee.
    """

    def __init__(self, max_id: int) -> None:
        self._max_id = max_id
        self._pool: collections.deque[int] = collections.deque()
        self._in_use: set[int] = set()
        self._max_seen = 0

    def allocate(self) -> int | None:
        if len(self._pool) > 0:
            id = self._pool.popleft()
            self._in_use.add(id)
            return id

        if self._max_seen >= self._max_id:
            return None

        id = self._max_seen
        self._max_seen += 1
        self._in_use.add(id)

        return id

    def release(self, id: int) -> None:
        if id in self._in_use:
            self._in_use.remove(id)
            self._pool.append(id)


class BitmapAllocator:
    """Tier 2: bitmap-backed allocator over [0, max_id).

    Input:
        max_id : int — constructor arg; ids are drawn from [0, max_id).
        allocate() takes no arguments; release(id) takes the int to free.
    Output:
        allocate() -> int | None — the *lowest* free id, or None once
            [0, max_id) is fully allocated.
        release(id) -> None.

    Example 1:
        Input:
            ["BitmapAllocator", "allocate", "allocate", "release", "allocate"]
            [[3],               [],         [],         [0],       []]
        Output:
            [null,              0,          1,          null,      0]
        Explanation:
            ids 0 and 1 are allocated; release(0) frees 0; the next
            allocate() returns it — the lowest free id.

    Example 2:
        Input:
            ["BitmapAllocator", "allocate", "allocate", "allocate"]
            [[2],               [],         [],         []]
        Output:
            [null,              0,          1,          null]
        Explanation:
            range [0, 2) holds two ids; the third allocate() finds the
            range exhausted and returns null.

    Example 3:  # lowest-free-id first, regardless of release order
        Input:
            ["BitmapAllocator", "allocate", "allocate", "allocate",
             "allocate", "release", "release", "allocate", "allocate"]
            [[4], [], [], [], [], [2], [0], [], []]
        Output:
            [null, 0, 1, 2, 3, null, null, 0, 2]
        Explanation:
            ids 0..3 are allocated, then 2 is released before 0. allocate()
            still returns the *lowest* free id first — 0, then 2 — even
            though 2 was freed earlier.

    State is one packed bit per id, held in a bytearray: bit (id % 8) of
    byte (id // 8) is 1 exactly when id is allocated. The bitmap itself
    answers "is id live?", so Tier 1's separate in-use set is gone.

    Standard library:
        bytearray — a mutable, fixed-length sequence of bytes (each an int
            0-255). ceil(max_id / 8) bytes hold one bit per id; unlike the
            immutable `bytes`, it can be updated in place with |= and &=.
        math.ceil — rounds the byte count up, so ids in the final partial
            byte still get a bit when max_id is not a multiple of 8.
        The bit operators | & ~ << >> are builtins, not a module.

    Bit twiddling (id i lives in byte i // 8, bit i % 8):
        set bit i:    bits[i // 8] |=  (1 << (i % 8))
        clear bit i:  bits[i // 8] &= ~(1 << (i % 8))
        read bit i:   (bits[i // 8] >> (i % 8)) & 1

    Pseudocode:
        allocate():
            if free_count == 0:
                return None
            for i in range(max_id):           # scan left to right
                if bit i is 0:                # first free id wins
                    set bit i
                    free_count -= 1
                    return i

        release(id):
            if bit id is 1:
                clear bit id
                free_count += 1

    Complexity: allocate O(n) — a left-to-right scan for the first 0 bit;
    release O(1). Space ~n / 8 bytes — the packed bitmap, far smaller than
    Tier 1's set of int objects.

    Contract upgrade (free with the left-to-right scan): allocate() always
    returns the *smallest* free id. Trade-off: that scan is O(n) — Tier 3
    layers a tree over the bits to bring it back to O(log n).
    """

    def __init__(self, max_id: int) -> None:
        self._max_id = max_id
        self._bits: bytearray = bytearray(math.ceil(max_id / 8))
        self._free_count = max_id

    def allocate(self) -> int | None:
        if self._free_count == 0:
            return None

        for i in range(self._max_id):
            bt = self._bits[i // 8]
            bit = i % 8
            if (bt >> bit) & 1 == 0:
                bt = bt | (1 << bit)
                self._free_count -= 1
                return i

        return None

    def release(self, id: int) -> None:
        bt = self._bits[id // 8]
        bit = id % 8
        if ((bt >> bit) & 1) == 1:
            bt &= ~(1 << bit)
            self._free_count += 1
        return None


class SegmentTreeAllocator:
    """Tier 3: segment-tree-of-bits allocator over [0, max_id).

    Input:
        max_id : int — constructor arg; ids are drawn from [0, max_id).
        allocate() takes no arguments; release(id) takes the int to free.
    Output:
        allocate() -> int | None — the *lowest* free id, or None once
            [0, max_id) is fully allocated.
        release(id) -> None.

    Example 1:
        Input:
            ["SegmentTreeAllocator", "allocate", "allocate", "release", "allocate"]
            [[3],                    [],         [],         [0],       []]
        Output:
            [null,                   0,          1,          null,      0]
        Explanation:
            ids 0 and 1 are allocated; release(0) frees 0; the next
            allocate() descends to it — the lowest free id.

    Example 2:
        Input:
            ["SegmentTreeAllocator", "allocate", "allocate", "allocate"]
            [[2],                    [],         [],         []]
        Output:
            [null,                   0,          1,          null]
        Explanation:
            range [0, 2) holds two ids; the third allocate() finds the
            root summary full and returns null.

    Example 3:  # lowest-free-id first, regardless of release order
        Input:
            ["SegmentTreeAllocator", "allocate", "allocate", "allocate",
             "allocate", "release", "release", "allocate", "allocate"]
            [[4], [], [], [], [], [2], [0], [], []]
        Output:
            [null, 0, 1, 2, 3, null, null, 0, 2]
        Explanation:
            ids 0..3 are allocated, then 2 is released before 0. The
            descent prefers the left (lower) child whenever it is not
            full, so allocate() returns 0, then 2 — even though 2 was
            freed earlier.

    A recursive segment tree whose every node summarizes its id range with
    one AND-bit: tree[i] == 1 exactly when *every* id in node i's range is
    allocated. allocate() rides that summary — at each node it descends
    into a child that is not full — reaching the lowest free id in
    O(log n) instead of Tier 2's O(n) scan.

    Structure: 1-indexed (root at 1, children of i at 2*i and 2*i+1). Node
    i covers [lo, hi]; its children cover [lo, mid] and [mid+1, hi]. The
    ranges are carried through the recursion, so max_id need not be a
    power of two — no padding, no phantom leaves.

    Standard library:
        bytearray — backs the tree as 4 * max_id bytes, one summary bit
            per node, mutable in place. 4 * n is the standard safe size
            for a recursive segment tree over n leaves.
        No explicit stack — the descent is ordinary recursion, so Python's
            call stack is the tree-walk stack and each call frame holds
            that node's [lo, hi] range.

    Pseudocode:
        allocate():
            if tree[1] == 1:                  # root full -> range exhausted
                return None
            return claim_lowest(i=1, lo=0, hi=max_id - 1)

        claim_lowest(i, lo, hi):
            if lo == hi:                      # a leaf reached -> that id
                tree[i] = 1
                return lo
            mid = (lo + hi) // 2
            if tree[left child] == 0:         # left not full -> lowest left
                id = claim_lowest(left, lo, mid)
            else:
                id = claim_lowest(right, mid + 1, hi)
            tree[i] = tree[left] & tree[right]   # refresh summary on unwind
            return id

        release(id):
            free(i=1, lo=0, hi=max_id - 1, target=id)

        free(i, lo, hi, target):
            if lo == hi:
                tree[i] = 0                   # clear the leaf
                return
            mid = (lo + hi) // 2
            recurse into the child whose [lo, hi] contains target
            tree[i] = tree[left] & tree[right]   # refresh summary on unwind

    Complexity: allocate and release both O(log n) — one root-to-leaf
    path, the summary refreshed as the recursion unwinds. Space O(n)
    (4 * n cells) — the tree buys back the O(log n) allocate.

    Contract: allocate() returns the *smallest* free id, because the
    descent takes the left child whenever it is not full.
    """

    def __init__(self, max_id: int) -> None:
        self._max_id = max_id
        self._cap = 4 * max_id
        self._tree: bytearray = bytearray(self._cap)

    def _free(self, i: int, lo: int, hi: int, target):
        if lo == hi:
            self._tree[i] = 0
            return
        mid = (lo + hi) // 2
        if target <= mid:
            self._free(2 * i, lo, mid, target)
        else:
            self._free(2 * i + 1, mid + 1, hi, target)

        self._tree[i] = self._tree[2 * i] & self._tree[2 * i + 1]

    def _claim_lowest(self, i: int, lo: int, hi: int) -> int:
        if lo == hi:
            self._tree[i] = 1
            return lo
        mid = (lo + hi) // 2
        if self._tree[2 * i] == 0:
            id = self._claim_lowest(2 * i, lo, mid)
        else:
            id = self._claim_lowest(2 * i + 1, mid + 1, hi)

        self._tree[i] = self._tree[2 * i] & self._tree[2 * i + 1]

        return id

    def allocate(self) -> int | None:
        if self._tree[1] == 1:
            return None

        return self._claim_lowest(1, 0, self._max_id - 1)

    def release(self, id: int) -> None:
        self._free(1, 0, self._max_id - 1, id)
        return None


class ThreadSafeAllocator:
    """Tier 4: thread-safe allocator for high-contention concurrent access.

    Input:
        max_id : int — constructor arg; ids are drawn from [0, max_id),
            partitioned into shards.
        allocate() takes no arguments; release(id) takes the int to free.
    Output:
        allocate() -> int | None — the lowest free id *within a randomly
            chosen shard*, or None once every shard is full.
        release(id) -> None.

    Example 1:
        Input:
            ["ThreadSafeAllocator", "allocate", "allocate", "allocate"]
            [[100],                 [],         [],         []]
        Output:
            [null, <id A>, <id B>, <id C>]
        Explanation:
            A, B, C are distinct ids, each in [0, 100). allocate() picks a
            random shard, then the lowest free id within it — so the ids
            are in range and distinct, but which ones depends on the
            random shard order: no lowest-first guarantee across shards.

    Example 2:
        Input:
            ["ThreadSafeAllocator", "allocate", "allocate", "allocate",
             "allocate"]
            [[3], [], [], [], []]
        Output:
            [null, <id>, <id>, <id>, null]
        Explanation:
            max_id=3 yields three single-id shards. The first three
            allocate() calls drain them — together returning {0, 1, 2} in
            some order — and the fourth finds every shard full -> null.

    Example 3:  # a released id is reusable; ordering stays shard-local
        Input:
            ["ThreadSafeAllocator", "allocate", "release", "allocate"]
            [[100],                 [],         [<id A>],  []]
        Output:
            [null, <id A>, null, <id>]
        Explanation:
            an allocated id, once released, is free to be handed out
            again. Unlike Tiers 2-3 the returned id need not be the global
            minimum — only the lowest free id in whatever shard the random
            pick lands on.

    Shards [0, max_id) across S independent SegmentTreeAllocators, each
    behind its own lock, so callers touching different shards never
    contend. Shard s owns global ids [s*shard_size, (s+1)*shard_size); a
    global id maps to (id // shard_size, id % shard_size).

    Why a lock at all, given the GIL: the GIL makes one *bytecode* atomic,
    but allocate() is many bytecodes — a recursive descent plus a summary
    refresh. The GIL can switch threads mid-method, so two threads could
    interleave a descent and land on the same leaf. The per-shard lock
    makes the whole shard operation atomic.

    Standard library:
        threading.Lock — one mutex per shard; `with lock:` admits one
            thread at a time, making a whole shard operation atomic.
        random.sample(population, k) — returns k distinct items in random
            order; with k = the shard count it is a random permutation, so
            threads scan shards in differing orders and do not all pile
            onto shard 0.
        math.ceil — sizes each shard so the shards together cover max_id.

    Pseudocode:
        allocate():
            for s in random_permutation(all shards):   # spread contention
                with shards[s].lock:
                    local = shards[s].tree.allocate()   # a Tier-3 allocate
                    if local is not None:
                        return s * shard_size + local
            return None                                 # every shard full

        release(id):
            s     = id // shard_size                    # shard that owns id
            local = id % shard_size
            with shards[s].lock:
                shards[s].tree.release(local)

    Trade-off: no global lock — different shards proceed in parallel, and
    contention arises only when two threads pick the same shard. The price
    is a relaxed contract: allocate() returns the lowest free id *within
    the chosen shard*, not the global minimum.
    """

    def __init__(self, max_id: int) -> None:
        self._S = 10
        self._shard_size = math.ceil(max_id / self._S)
        self._shards = []
        rem = max_id
        while rem > 0:
            cap_s = min(self._shard_size, rem)
            self._shards.append((threading.Lock(), SegmentTreeAllocator(cap_s)))
            rem -= cap_s

    def allocate(self) -> int | None:
        order = random.sample(range(len(self._shards)), len(self._shards))
        for i in order:
            lock_s, tree_s = self._shards[i]
            with lock_s:
                local = tree_s.allocate()
                if local is not None:
                    return i * self._shard_size + local
        return None

    def release(self, id: int) -> None:
        s = id // self._shard_size
        local_id = id % self._shard_size
        lock_s, tree_s = self._shards[s]
        with lock_s:
            tree_s.release(local_id)

        return None
