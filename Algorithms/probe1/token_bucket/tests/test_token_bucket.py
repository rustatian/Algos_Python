"""Tests for the token bucket ladder.

  - Tier 1 (SimpleTokenBucket) and Tier 2 (ConcurrentTokenBucket) are
    synchronous; their tests use `time.sleep` and `threading.Thread`.
  - Tier 4 (TokenBucket) is async; those tests are marked
    @pytest.mark.asyncio (strict mode — no auto-detection) and use
    `await asyncio.sleep(t)` so the event loop keeps scheduling tasks.

The shared-behavior tests are parametrized over both SimpleTokenBucket
and ConcurrentTokenBucket via SYNC_BUCKETS — both must satisfy the
fail-fast try_acquire contract identically when used from a single
thread.

Timing notes:
  - Tests sleep comfortably past 1 second so they pass under both
    int-second and sub-second refill math.
  - Concurrency tests run for at most a few hundred milliseconds; long
    durations don't catch new bugs.
"""

import asyncio
import threading
import time

import pytest

from token_bucket.token_bucket import (
    ConcurrentTokenBucket,
    SimpleTokenBucket,
    TokenBucket,
)

# Tier 1 and Tier 2 share the try_acquire contract.
SYNC_BUCKETS = [SimpleTokenBucket, ConcurrentTokenBucket]


# ===========================================================================
# Tier 1 / Tier 2 — shared try_acquire contract (single-threaded).
# ===========================================================================


@pytest.mark.parametrize("bucket_cls", SYNC_BUCKETS)
def test_bucket_starts_full(bucket_cls: type) -> None:
    """A fresh bucket starts at max_capacity — the initial burst is allowed.
    Guava / Bucket4j / Resilience4j all match this default.
    """
    b = bucket_cls(max_capacity=5, fill_rate=10)
    assert b.try_acquire(5) is True


@pytest.mark.parametrize("bucket_cls", SYNC_BUCKETS)
def test_try_acquire_more_than_available_returns_false(bucket_cls: type) -> None:
    """try_acquire is atomic — a request bigger than the balance returns
    False and leaves the bucket untouched (no partial spend).
    """
    b = bucket_cls(max_capacity=5, fill_rate=0)
    assert b.try_acquire(10) is False
    # Bucket was NOT partially drained — full 5 still available.
    assert b.try_acquire(5) is True


@pytest.mark.parametrize("bucket_cls", SYNC_BUCKETS)
def test_try_acquire_consumes_tokens(bucket_cls: type) -> None:
    """Successful try_acquire deducts the requested count; a follow-up
    request for the remaining balance succeeds.
    """
    b = bucket_cls(max_capacity=10, fill_rate=0)
    assert b.try_acquire(3) is True
    assert b.try_acquire(7) is True  # exact remainder
    assert b.try_acquire(1) is False  # nothing left


@pytest.mark.parametrize("bucket_cls", SYNC_BUCKETS)
def test_lazy_refill_after_elapsed_time(bucket_cls: type) -> None:
    """After draining, waiting long enough for fill_rate to earn n tokens
    lets try_acquire(n) succeed again.
    """
    b = bucket_cls(max_capacity=10, fill_rate=10)
    assert b.try_acquire(10) is True  # drain
    assert b.try_acquire(1) is False  # immediate retry: still empty
    time.sleep(1.1)
    # 1.1 sec at fill_rate=10 → ~11 tokens earned, capped at 10.
    assert b.try_acquire(5) is True


@pytest.mark.parametrize("bucket_cls", SYNC_BUCKETS)
def test_refill_caps_at_max_capacity(bucket_cls: type) -> None:
    """Earned tokens cap at max_capacity — a long wait does NOT let the
    bucket exceed its capacity.
    """
    b = bucket_cls(max_capacity=5, fill_rate=100)
    assert b.try_acquire(5) is True  # drain
    time.sleep(1.1)  # would earn 110 tokens
    assert b.try_acquire(5) is True  # cap = 5
    assert b.try_acquire(1) is False  # confirms cap


# ===========================================================================
# Tier 2 only — concurrency & blocking acquire.
# ===========================================================================


def test_concurrent_try_acquire_does_not_overspend() -> None:
    """200 threads race to try_acquire(1) on a 100-token bucket with no
    refill. Exactly 100 succeed; a missing Lock would let races overspend.
    """
    b = ConcurrentTokenBucket(max_capacity=100, fill_rate=0)
    successes: list[bool] = []
    successes_lock = threading.Lock()

    def attempt() -> None:
        ok = b.try_acquire(1)
        with successes_lock:
            successes.append(ok)

    threads = [threading.Thread(target=attempt) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Exactly 100 of the 200 attempts succeeded.
    assert sum(successes) == 100


def test_acquire_returns_immediately_when_tokens_available() -> None:
    """When the bucket has enough tokens, acquire returns without blocking."""
    b = ConcurrentTokenBucket(max_capacity=10, fill_rate=10)
    start = time.monotonic()
    b.acquire(5)
    elapsed = time.monotonic() - start
    # Should be effectively instant — give a generous bound.
    assert elapsed < 0.1


def test_acquire_blocks_until_refill_delivers_tokens() -> None:
    """A blocking acquire on a drained bucket must wait until the
    fill_rate has earned enough tokens. The thread's join completes
    only after enough time has elapsed.
    """
    b = ConcurrentTokenBucket(max_capacity=10, fill_rate=10)
    # Drain the bucket.
    assert b.try_acquire(10) is True

    done = threading.Event()
    elapsed_holder: list[float] = []

    def waiter() -> None:
        start = time.monotonic()
        b.acquire(5)
        elapsed_holder.append(time.monotonic() - start)
        done.set()

    t = threading.Thread(target=waiter)
    t.start()
    # 5 tokens at fill_rate=10 → ~0.5 sec.
    # Bounded wait — a bug would deadlock; this would time out.
    assert done.wait(timeout=2.0), "acquire(5) on drained bucket should complete"
    t.join()
    # Should have waited at LEAST ~0.4 sec (some tolerance for clock skew).
    assert elapsed_holder[0] >= 0.4


def test_multiple_waiters_each_get_their_tokens() -> None:
    """Three threads each block on acquire for different amounts. All
    must eventually return; the cumulative time taken roughly matches
    the slowest waiter's shortfall / fill_rate.
    """
    b = ConcurrentTokenBucket(max_capacity=10, fill_rate=10)
    assert b.try_acquire(10) is True  # drain

    done = [threading.Event() for _ in range(3)]

    def waiter(idx: int, n: int) -> None:
        b.acquire(n)
        done[idx].set()

    # Three waiters needing 2, 3, and 4 tokens respectively.
    threads = [
        threading.Thread(target=waiter, args=(0, 2)),
        threading.Thread(target=waiter, args=(1, 3)),
        threading.Thread(target=waiter, args=(2, 4)),
    ]
    for t in threads:
        t.start()

    # All three should complete within a reasonable bound. Worst case:
    # ~9 tokens total demand at rate=10 → ~0.9 sec; allow ample slack.
    for evt in done:
        assert evt.wait(timeout=3.0)
    for t in threads:
        t.join()


# ===========================================================================
# Tier 4 — async TokenBucket (existing tests below, unchanged).
# ===========================================================================


# ---------------------------------------------------------------------------
# get() input validation — kept because the failure mode is deadlock, not
# a visible error. A get() call that can never be satisfied (n > max_capacity)
# would block forever; an explicit ValueError surfaces the bug immediately.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# get() input validation — kept because the failure mode is deadlock, not
# a visible error. A get() call that can never be satisfied (n > max_capacity)
# would block forever; an explicit ValueError surfaces the bug immediately.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n", [0, -1, -100])
@pytest.mark.asyncio
async def test_get_rejects_zero_or_negative(n: int) -> None:
    bucket = TokenBucket(max_capacity=10, fill_rate=10)
    with pytest.raises(ValueError):
        await bucket.get(n)


@pytest.mark.asyncio
async def test_get_rejects_more_than_max_capacity() -> None:
    bucket = TokenBucket(max_capacity=5, fill_rate=10)
    with pytest.raises(ValueError):
        await bucket.get(6)


# ---------------------------------------------------------------------------
# fill() behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fill_adds_tokens_based_on_elapsed_time() -> None:
    bucket = TokenBucket(max_capacity=100, fill_rate=10)
    await asyncio.sleep(1.1)
    await bucket.fill()
    # At rate=10/sec for ~1.1s we should have 10–11 tokens depending on
    # whether the truncation happens before or after the rate multiplication.
    assert 10 <= len(bucket._bucket) <= 11


@pytest.mark.asyncio
async def test_fill_caps_at_max_capacity() -> None:
    bucket = TokenBucket(max_capacity=5, fill_rate=100)
    await asyncio.sleep(1.1)  # would earn 110 tokens at rate=100 — caps at 5
    await bucket.fill()
    assert len(bucket._bucket) == 5


@pytest.mark.asyncio
async def test_fill_produces_tokens_in_valid_range() -> None:
    """Each token must be an integer in [1, 100]."""
    bucket = TokenBucket(max_capacity=50, fill_rate=50)
    await asyncio.sleep(1.1)
    await bucket.fill()
    assert len(bucket._bucket) > 0
    for token in bucket._bucket:
        assert isinstance(token, int)
        assert 1 <= token <= 100


# ---------------------------------------------------------------------------
# get() behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_requested_number_of_tokens() -> None:
    bucket = TokenBucket(max_capacity=10, fill_rate=10)
    await asyncio.sleep(1.1)
    await bucket.fill()

    tokens = await bucket.get(3)
    assert len(tokens) == 3
    assert all(1 <= t <= 100 for t in tokens)


@pytest.mark.asyncio
async def test_get_reduces_bucket_size() -> None:
    bucket = TokenBucket(max_capacity=10, fill_rate=10)
    await asyncio.sleep(1.1)
    await bucket.fill()

    size_before = len(bucket._bucket)
    await bucket.get(3)
    size_after = len(bucket._bucket)
    assert size_after == size_before - 3


# ---------------------------------------------------------------------------
# Concurrency: get() blocks until fill() produces tokens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_blocks_until_fill_produces_tokens() -> None:
    """A consumer on an empty bucket must await until a producer fills it."""
    bucket = TokenBucket(max_capacity=5, fill_rate=10)

    # Spawn the consumer as a task — it will park on _allocated.wait().
    consumer_task = asyncio.create_task(bucket.get(1))

    # Yield so the consumer gets scheduled and reaches the wait.
    await asyncio.sleep(0.2)
    assert not consumer_task.done(), "consumer should block on an empty bucket"

    # Producer side: wait past the second boundary, then fill.
    await asyncio.sleep(1.0)
    await bucket.fill()

    # Bounded await — a buggy notify shouldn't hang the suite.
    result = await asyncio.wait_for(consumer_task, timeout=2.0)
    assert len(result) == 1
    assert 1 <= result[0] <= 100


@pytest.mark.asyncio
async def test_fill_blocks_when_full_unblocks_after_get() -> None:
    """The mirror of the test above: a producer on a full bucket must await
    until a consumer drains it. Exercises get()'s wake path on _do_allocate."""
    bucket = TokenBucket(max_capacity=1, fill_rate=10)

    # Fill the bucket to capacity first.
    await asyncio.sleep(1.1)
    await bucket.fill()
    assert len(bucket._bucket) == 1, "bucket should start at max capacity"

    # Spawn a second fill — it should park on _do_allocate.wait().
    producer_task = asyncio.create_task(bucket.fill())

    # Yield so the producer gets scheduled and reaches the wait.
    await asyncio.sleep(0.2)
    assert not producer_task.done(), "producer should block when bucket is full"

    # Consumer side: drain. The producer should unblock — if get() forgets to
    # notify _do_allocate, this hangs and times out.
    await bucket.get(1)

    await asyncio.wait_for(producer_task, timeout=2.0)
    assert producer_task.done(), "producer should have unblocked after get()"
