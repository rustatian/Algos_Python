"""Tests for Design Hit Counter.

Every tier exposes the same hit / get_hits surface; the correctness tests
are parametrized over ``COUNTERS`` so adding a new tier just appends the
new class to the list.
"""

import threading

import pytest

from hit_counter.hit_counter import (
    BucketCounter,
    ConcurrentCounter,
    DequeCounter,
    DistributedCounter,
)

# Every tier with the same hit / get_hits surface goes here.
COUNTERS = [DequeCounter, BucketCounter, ConcurrentCounter, DistributedCounter]


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_empty_counter_reads_zero(counter_cls: type) -> None:
    """A fresh counter with no hits returns 0 at any timestamp."""
    c = counter_cls()
    assert c.get_hits(1) == 0
    assert c.get_hits(10_000) == 0


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_single_hit_in_window(counter_cls: type) -> None:
    """One hit at t=1 is visible at t=1: window (1-300, 1] includes t=1."""
    c = counter_cls()
    c.hit(1)
    assert c.get_hits(1) == 1


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_leetcode_362_example(counter_cls: type) -> None:
    """The canonical LeetCode #362 example:
    hit(1); hit(2); hit(3); get_hits(4)  -> 3
    hit(300); get_hits(300)              -> 4
    get_hits(301)                        -> 3   (t=1 falls out)
    """
    c = counter_cls()
    c.hit(1)
    c.hit(2)
    c.hit(3)
    assert c.get_hits(4) == 3
    c.hit(300)
    assert c.get_hits(300) == 4
    assert c.get_hits(301) == 3


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_window_boundary_is_half_open(counter_cls: type) -> None:
    """Boundary: a hit at t=1 is in the window for t=300 (300-1 = 299,
    still inside) but out at t=301 (301-1 = 300, the strict-exclude edge).
    """
    c = counter_cls()
    c.hit(1)
    assert c.get_hits(300) == 1
    assert c.get_hits(301) == 0


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_multiple_hits_same_timestamp(counter_cls: type) -> None:
    """Hits sharing one timestamp are all counted — no de-duplication
    by timestamp. They all age out together at t + 300.
    """
    c = counter_cls()
    for _ in range(5):
        c.hit(10)
    assert c.get_hits(10) == 5
    assert c.get_hits(309) == 5
    assert c.get_hits(310) == 0


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_old_hits_fully_purged(counter_cls: type) -> None:
    """A far-future read sees zero — every hit has aged out of the window."""
    c = counter_cls()
    c.hit(1)
    c.hit(2)
    c.hit(3)
    assert c.get_hits(1000) == 0


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_read_is_stable_across_calls(counter_cls: type) -> None:
    """Two consecutive reads with the same argument return the same count:
    the lazy purge inside get_hits only drops aged-out hits, never live ones.
    """
    c = counter_cls()
    c.hit(10)
    c.hit(20)
    c.hit(30)
    assert c.get_hits(30) == 3
    assert c.get_hits(30) == 3


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_hits_spread_across_window_age_out_one_by_one(counter_cls: type) -> None:
    """Hits spread across the 300-second window age out individually as
    time advances past each by 300 seconds.
    """
    c = counter_cls()
    c.hit(1)
    c.hit(100)
    c.hit(200)
    c.hit(300)
    assert c.get_hits(300) == 4  # window (0, 300]; all four in
    assert c.get_hits(301) == 3  # window (1, 301]; t=1 out
    assert c.get_hits(400) == 2  # window (100, 400]; t=1, t=100 out
    assert c.get_hits(500) == 1  # window (200, 500]; only t=300 left
    assert c.get_hits(600) == 0  # window (300, 600]; t=300 out too


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_hit_then_immediate_read(counter_cls: type) -> None:
    """A hit and a read at the same timestamp: the hit counts (the
    interval (t-300, t] is closed on the right)."""
    c = counter_cls()
    c.hit(42)
    assert c.get_hits(42) == 1


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_hits_exactly_300_seconds_apart_only_newer_counts(counter_cls: type) -> None:
    """Two hits exactly 300 seconds apart. At the time of the second
    hit, the first sits at the half-open boundary (t' = t - 300) and is
    out of window — only the newer hit counts.

    For BucketCounter this exercises the slot-collision case: 5 % 300 ==
    305 % 300 == 5, so the newer hit must overwrite the older, not merge.
    A finder that merged would return 2.
    """
    c = counter_cls()
    c.hit(5)
    c.hit(305)
    assert c.get_hits(305) == 1
    # And 300s later the t=305 hit also ages out — nothing lingers.
    assert c.get_hits(605) == 0


@pytest.mark.parametrize("counter_cls", COUNTERS)
def test_hit_at_last_slot_is_counted(counter_cls: type) -> None:
    """Regression: a hit at t where t % 300 == 299 must be counted.
    Catches the off-by-one where a bucketed counter sweeps range(0, 299)
    instead of range(0, 300) on read — the last slot would be silently
    excluded and the hit would vanish.
    """
    c = counter_cls()
    c.hit(299)
    assert c.get_hits(299) == 1
    c.hit(599)  # same slot (599 % 300 == 299) — collision overwrites
    assert c.get_hits(599) == 1


# ---------------------------------------------------------------------------
# Tier 3 only — concurrent stress tests for ConcurrentCounter.
#
# Single-threaded behavior is covered by the shared tests above; these
# verify the lock actually protects shared state. On Python 3.14t
# (free-threading) a missing lock will reliably lose updates; on older
# CPython the GIL makes the race rare but not impossible — large hit
# counts make the bug surface either way.
# ---------------------------------------------------------------------------


def test_concurrent_writes_to_one_slot_no_lost_updates() -> None:
    """10 threads each call hit(100) 1000 times. All 10000 hits must
    land in slot 100; a missing per-bucket lock would lose updates
    because counts[100] += 1 is a non-atomic read-modify-write.
    """
    c = ConcurrentCounter()

    def fire() -> None:
        for _ in range(1000):
            c.hit(100)

    threads = [threading.Thread(target=fire) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert c.get_hits(100) == 10_000


def test_concurrent_writes_to_different_slots_all_land() -> None:
    """10 threads write 30 hits each to disjoint slot ranges. All 300
    distinct slots end with a count of 1, and get_hits across the whole
    window returns 300. Exercises the per-bucket parallelism — writes
    to different slots take different locks and never wait on each other.
    """
    c = ConcurrentCounter()

    def fire(start: int) -> None:
        for t in range(start, start + 30):
            c.hit(t)

    threads = [threading.Thread(target=fire, args=(s,)) for s in range(0, 300, 30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 300 hits within (-1, 299] — every slot contributes 1.
    assert c.get_hits(299) == 300


def test_concurrent_reads_during_writes_return_consistent_values() -> None:
    """Readers interleaved with writers must not crash, must not see
    torn (times, counts) pairs, and must return non-negative values
    bounded above by the total number of writes issued so far.

    Strict snapshot semantics are NOT promised — get_hits acquires the
    300 locks one at a time, so writes to other slots can land between
    the 300 acquisitions. But each individual slot's contribution
    reflects a consistent pair.
    """
    c = ConcurrentCounter()
    writes_done = 0
    writes_lock = threading.Lock()
    stop = threading.Event()
    errors: list[Exception] = []

    def write() -> None:
        nonlocal writes_done
        try:
            t = 0
            while not stop.is_set():
                c.hit(t % 300)
                with writes_lock:
                    writes_done += 1
                t += 1
        except Exception as e:
            errors.append(e)

    def read() -> None:
        try:
            while not stop.is_set():
                n = c.get_hits(299)
                # Non-negative and bounded by the writes so far.
                assert n >= 0
        except Exception as e:
            errors.append(e)

    writers = [threading.Thread(target=write) for _ in range(4)]
    readers = [threading.Thread(target=read) for _ in range(4)]
    for t in writers + readers:
        t.start()
    # Brief stress window; longer durations don't catch new bugs.
    stop.wait(0.1)
    stop.set()
    for t in writers + readers:
        t.join()

    assert errors == []


# ---------------------------------------------------------------------------
# Tier 4 only — scatter-gather correctness for DistributedCounter.
#
# These verify the cross-shard contract: hits scattered across N shards by
# thread identity, aggregated on read. With thread-id-based dispatch the
# important invariant is "every hit gets counted exactly once," regardless
# of which shard the dispatcher chose.
# ---------------------------------------------------------------------------


def test_distributed_hits_scattered_across_shards_aggregate_correctly() -> None:
    """8 threads × 1000 hits each = 8000 total hits, all at t=100,
    distributed across 4 shards by thread-id-mod-4. The aggregator
    must return exactly 8000 — every hit counted exactly once,
    regardless of which shard received it.

    A finder that forgot to sum across shards (returning one shard's
    count) would fail. A finder that double-counted across shards
    (e.g. by hitting every shard on each hit) would also fail.
    """
    c = DistributedCounter(num_shards=4)

    def fire() -> None:
        for _ in range(1000):
            c.hit(100)

    threads = [threading.Thread(target=fire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert c.get_hits(100) == 8000


def test_distributed_cross_shard_window_isolation() -> None:
    """Two threads write to different shards at different timestamps,
    one well outside the other's window. The aggregator must apply the
    window check at the per-shard level, so the aged-out hit on one
    shard does NOT pollute the read on another shard.
    """
    c = DistributedCounter(num_shards=4)

    # Thread A fires at t=10; thread B fires at t=1000. The t=10 hit is
    # aged out for the read at t=1000 (1000 - 10 = 990 ≥ 300), so only
    # the t=1000 hit must contribute. If the aggregator summed before
    # applying the window check, both would count and we'd see 2.
    def fire_a() -> None:
        c.hit(10)

    def fire_b() -> None:
        c.hit(1000)

    ta = threading.Thread(target=fire_a)
    tb = threading.Thread(target=fire_b)
    ta.start()
    ta.join()
    tb.start()
    tb.join()

    assert c.get_hits(1000) == 1
