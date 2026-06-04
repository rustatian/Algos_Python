"""Tests for the ID Allocator tiers.

Tiers 1-4 share most of the contract — those tests are parametrized over all
four classes. The "strict-contract" tests (lowest-free-id first, non-power-of-2
max_id) apply to the Tier 2/3/4 classes that guarantee that ordering. The
concurrency tests at the bottom apply only to Tier 4 (ThreadSafeAllocator).
"""

import threading

import pytest

from id_allocator.id_allocator import (
    Allocator,
    BitmapAllocator,
    SegmentTreeAllocator,
    ThreadSafeAllocator,
)

# All allocators satisfy the basic correctness contract.
ALLOCATORS = [Allocator, BitmapAllocator, SegmentTreeAllocator, ThreadSafeAllocator]

# allocate() returns the *globally* lowest free id. Tier 4's sharding
# deliberately breaks this: a sharded allocator returns the lowest id within
# whichever shard it picked, not the global minimum.
LOWEST_FIRST_ALLOCATORS = [BitmapAllocator, SegmentTreeAllocator]

# Correctly avoid handing out ids >= max_id when max_id isn't a power of two.
BOUNDED_ALLOCATORS = [BitmapAllocator, SegmentTreeAllocator, ThreadSafeAllocator]


# ---------------------------------------------------------------------------
# Basic single-id behavior — shared by all allocators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_allocate_returns_id_in_range(alloc_cls: type) -> None:
    a = alloc_cls(max_id=10)
    got = a.allocate()
    assert got is not None
    assert 0 <= got < 10


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_two_allocations_are_distinct(alloc_cls: type) -> None:
    a = alloc_cls(max_id=10)
    x = a.allocate()
    y = a.allocate()
    assert x != y


# ---------------------------------------------------------------------------
# Reclamation: released ids become available again — shared
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_released_id_is_reusable(alloc_cls: type) -> None:
    a = alloc_cls(max_id=2)
    x = a.allocate()
    y = a.allocate()
    a.release(x)
    z = a.allocate()
    assert z == x
    assert y != z


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_release_does_not_yield_a_still_allocated_id(alloc_cls: type) -> None:
    a = alloc_cls(max_id=3)
    x = a.allocate()
    y = a.allocate()
    a.release(x)
    z = a.allocate()
    assert z != y


# ---------------------------------------------------------------------------
# Boundary: exhaustion — shared
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_allocate_exhaustion_returns_none(alloc_cls: type) -> None:
    a = alloc_cls(max_id=3)
    a.allocate()
    a.allocate()
    a.allocate()
    assert a.allocate() is None


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_full_cycle_drain_and_refill(alloc_cls: type) -> None:
    n = 5
    a = alloc_cls(max_id=n)
    first = [a.allocate() for _ in range(n)]
    assert sorted(first) == list(range(n))

    for id_ in first:
        a.release(id_)

    second = [a.allocate() for _ in range(n)]
    assert sorted(second) == list(range(n))


@pytest.mark.parametrize("alloc_cls", ALLOCATORS)
def test_no_double_issue_under_churn(alloc_cls: type) -> None:
    n = 20
    a = alloc_cls(max_id=n)
    live: set[int] = set()

    for _ in range(n):
        got = a.allocate()
        assert got is not None
        live.add(got)

    for round_ in range(100):
        victim = next(iter(live))
        live.remove(victim)
        a.release(victim)

        got = a.allocate()
        assert got is not None
        assert got not in live, f"round {round_}: allocator double-issued {got}"
        live.add(got)


# ---------------------------------------------------------------------------
# Stricter contracts: lowest-free-id-first and non-power-of-2 safety.
# Tier 1 satisfies neither. Tier 4 satisfies bounded-safety but NOT
# lowest-first, because sharding picks a shard before it picks an id.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("alloc_cls", LOWEST_FIRST_ALLOCATORS)
def test_returns_lowest_free_id_first(alloc_cls: type) -> None:
    a = alloc_cls(max_id=4)
    # Drain the range.
    for _ in range(4):
        a.allocate()
    # Release out of order: 2 first, then 0.
    a.release(2)
    a.release(0)
    # Lowest free is 0, even though 2 was released first.
    assert a.allocate() == 0
    assert a.allocate() == 2


@pytest.mark.parametrize("alloc_cls", BOUNDED_ALLOCATORS)
def test_handles_non_power_of_two_max_id(alloc_cls: type) -> None:
    """max_id=10 → bitmap rounds up to 2 bytes (16 bits); segment tree rounds
    up to 16 leaves. Both must avoid handing out ids 10..15 as 'free'.
    """
    a = alloc_cls(max_id=10)
    seen = set()
    for _ in range(10):
        got = a.allocate()
        assert got is not None
        assert 0 <= got < 10  # must not leak into the unused upper range
        seen.add(got)
    assert seen == set(range(10))
    assert a.allocate() is None  # truly exhausted


# ---------------------------------------------------------------------------
# Segment-tree-specific: stress the O(log N) path with a max_id larger than
# the small examples above, so a broken propagate-up surfaces.
# ---------------------------------------------------------------------------


def test_segment_tree_large_range_no_double_issue() -> None:
    """Allocate everything from a 1000-id range, then churn 500 rounds.

    A broken AND-propagate would let the descent revisit an allocated leaf
    or miss a freed one. This test runs enough operations that any drift
    in the internal summaries will surface as a double-issue or a missed id.
    """
    n = 1000
    a = SegmentTreeAllocator(max_id=n)
    live: set[int] = set()

    for _ in range(n):
        got = a.allocate()
        assert got is not None
        live.add(got)

    assert live == set(range(n))
    assert a.allocate() is None

    for round_ in range(500):
        victim = next(iter(live))
        live.remove(victim)
        a.release(victim)

        got = a.allocate()
        assert got is not None
        assert got == victim, (
            f"round {round_}: expected lowest-free {victim}, got {got}"
        )
        live.add(got)


# ---------------------------------------------------------------------------
# Tier 4 only: thread-safety under concurrent access.
#
# These tests are deterministic for a correctly-locked allocator (they always
# pass). For an unlocked one they are probabilistic — 8 threads with a barrier
# and thousands of iterations make a race very likely to surface, but not
# guaranteed on every run.
# ---------------------------------------------------------------------------


def test_concurrent_allocate_drains_each_id_exactly_once() -> None:
    """8 threads race to drain a 2000-id allocator.

    If allocate() is not locked, two threads can descend the tree at the same
    time and land on the same leaf — one id handed to two callers. That shows
    up here as a duplicate, so sorted(results) won't equal range(n). A lost id
    (two threads both think they took different leaves but corrupt the tree)
    shows up as a missing value.
    """
    n = 2000
    a = ThreadSafeAllocator(max_id=n)
    results: list[int] = []
    results_lock = threading.Lock()

    def worker() -> None:
        local: list[int] = []
        while True:
            got = a.allocate()
            if got is None:
                break
            local.append(got)
        with results_lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == list(range(n))


def test_concurrent_churn_never_double_issues() -> None:
    """8 threads each allocate / briefly hold / release, in a tight loop.

    `held` is the set of ids currently checked out by some worker. The worker
    records its id into `held` right after allocate() and removes it right
    BEFORE release() — that ordering means `held` never reports an id as held
    after it has been returned, so a hit on `if got in held` is always a real
    double-issue, never a false alarm.
    """
    n = 128
    a = ThreadSafeAllocator(max_id=n)
    held: set[int] = set()
    held_lock = threading.Lock()
    errors: list[str] = []
    start = threading.Barrier(8)

    def worker() -> None:
        start.wait()  # all 8 threads released at once → maximize contention
        for _ in range(2000):
            got = a.allocate()
            if got is None:
                continue
            with held_lock:
                if got in held:
                    errors.append(f"id {got} held by two threads at once")
                held.add(got)
            with held_lock:
                held.discard(got)
            a.release(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors[:5]


def test_concurrent_churn_leaves_allocator_uncorrupted() -> None:
    """After heavy concurrent churn, the allocator must still be able to
    hand out exactly the full range — a race that corrupted the tree's
    internal summaries would lose or duplicate capacity.
    """
    n = 256
    a = ThreadSafeAllocator(max_id=n)

    def worker() -> None:
        for _ in range(1000):
            got = a.allocate()
            if got is not None:
                a.release(got)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    drained: list[int] = []
    while True:
        x = a.allocate()
        if x is None:
            break
        drained.append(x)
    assert sorted(drained) == list(range(n))
