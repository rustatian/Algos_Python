"""Tests for the Producer-Consumer / DB-backed queue ladder.

Tier 1 (BoundedBlockingQueue) is concurrency-tested with real threads.
Tiers 2-3 (the DB-backed queue) are single-threaded logic — the lock makes
claim atomic — and the lease/monitor uses a FakeClock so timeouts are
deterministic.
"""

import threading

from producer_consumer_service import (
    BoundedBlockingQueue,
    DBBackedQueue,
    JobStatus,
    LeasedQueue,
)


class FakeClock:
    """A manually-advanced monotonic clock for deterministic lease tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ----------------------------------------------------------------------
# Tier 1 — BoundedBlockingQueue.
# ----------------------------------------------------------------------


def test_queue_fifo_order() -> None:
    q = BoundedBlockingQueue(capacity=10)
    for i in range(5):
        q.enqueue(i)
    assert [q.dequeue() for _ in range(5)] == [0, 1, 2, 3, 4]


def test_queue_size() -> None:
    q = BoundedBlockingQueue(capacity=10)
    q.enqueue("a")
    q.enqueue("b")
    assert q.size() == 2


def test_enqueue_blocks_when_full_until_dequeue() -> None:
    """A producer on a full queue must block until a consumer makes room."""
    q = BoundedBlockingQueue(capacity=1)
    q.enqueue("first")  # queue now full

    enqueued = threading.Event()

    def producer() -> None:
        q.enqueue("second")  # blocks until there is room
        enqueued.set()

    t = threading.Thread(target=producer)
    t.start()
    assert not enqueued.wait(timeout=0.1)  # still blocked — queue is full
    assert q.dequeue() == "first"  # make room
    assert enqueued.wait(timeout=1.0)  # producer unblocked
    t.join()
    assert q.dequeue() == "second"


def test_dequeue_blocks_when_empty_until_enqueue() -> None:
    q = BoundedBlockingQueue(capacity=1)
    got: list[str] = []
    done = threading.Event()

    def consumer() -> None:
        got.append(q.dequeue())  # blocks until an item arrives
        done.set()

    t = threading.Thread(target=consumer)
    t.start()
    assert not done.wait(timeout=0.1)  # still blocked — queue empty
    q.enqueue("hello")
    assert done.wait(timeout=1.0)
    t.join()
    assert got == ["hello"]


def test_queue_many_producers_and_consumers() -> None:
    """Stress: every produced item is consumed exactly once, none lost."""
    q = BoundedBlockingQueue(capacity=5)
    produced = list(range(200))
    consumed: list[int] = []
    consumed_lock = threading.Lock()

    def producer(items: list[int]) -> None:
        for x in items:
            q.enqueue(x)

    def consumer(count: int) -> None:
        for _ in range(count):
            x = q.dequeue()
            with consumed_lock:
                consumed.append(x)

    prod = threading.Thread(target=producer, args=(produced,))
    cons = [threading.Thread(target=consumer, args=(100,)) for _ in range(2)]
    prod.start()
    for c in cons:
        c.start()
    prod.join()
    for c in cons:
        c.join()

    assert sorted(consumed) == produced


# ----------------------------------------------------------------------
# Tier 2 — DBBackedQueue.
# ----------------------------------------------------------------------


def test_submit_returns_pending_job() -> None:
    q = DBBackedQueue()
    jid = q.submit("work")
    assert q.status(jid) is JobStatus.PENDING


def test_claim_marks_processing() -> None:
    q = DBBackedQueue()
    jid = q.submit("work")
    job = q.claim()
    assert job is not None
    assert job.id == jid
    assert q.status(jid) is JobStatus.PROCESSING


def test_complete_marks_success() -> None:
    q = DBBackedQueue()
    jid = q.submit("work")
    job = q.claim()
    q.complete(job.id, result="done")
    assert q.status(jid) is JobStatus.SUCCESS


def test_fail_marks_failed() -> None:
    q = DBBackedQueue()
    jid = q.submit("work")
    job = q.claim()
    q.fail(job.id)
    assert q.status(jid) is JobStatus.FAILED


def test_claim_returns_none_when_no_pending() -> None:
    q = DBBackedQueue()
    assert q.claim() is None  # nothing submitted
    q.submit("work")
    q.claim()  # the only job is now PROCESSING
    assert q.claim() is None  # nothing left PENDING


def test_two_claims_get_distinct_jobs() -> None:
    """The atomic claim (SKIP LOCKED) must never hand the same job twice."""
    q = DBBackedQueue()
    q.submit("a")
    q.submit("b")
    first = q.claim()
    second = q.claim()
    assert first is not None and second is not None
    assert first.id != second.id  # no double-claim


def test_claim_is_fifo() -> None:
    q = DBBackedQueue()
    j1 = q.submit("a")
    j2 = q.submit("b")
    assert q.claim().id == j1
    assert q.claim().id == j2


# ----------------------------------------------------------------------
# Tier 3 — LeasedQueue: leases + retries.
# ----------------------------------------------------------------------


def test_lease_not_expired_leaves_processing_job_alone() -> None:
    clock = FakeClock()
    q = LeasedQueue(clock=clock, lease_seconds=30)
    jid = q.submit("work")
    q.claim()
    clock.advance(10)  # within the lease
    assert q.sweep_stuck() == 0
    assert q.status(jid) is JobStatus.PROCESSING


def test_expired_lease_requeues_job() -> None:
    clock = FakeClock()
    q = LeasedQueue(clock=clock, lease_seconds=30)
    jid = q.submit("work")
    q.claim()  # a worker claims, then "dies"
    clock.advance(31)  # lease expires
    assert q.sweep_stuck() == 1
    assert q.status(jid) is JobStatus.PENDING  # claimable again
    # And it can indeed be claimed by another worker.
    assert q.claim().id == jid


def test_fail_retries_until_max_attempts() -> None:
    clock = FakeClock()
    q = LeasedQueue(clock=clock, lease_seconds=30, max_attempts=3)
    jid = q.submit("flaky")
    # Attempt 1 and 2 fail -> requeued PENDING.
    for _ in range(2):
        job = q.claim()
        q.fail(job.id)
        assert q.status(jid) is JobStatus.PENDING
    # Attempt 3 fails -> attempts hit the cap -> terminal FAILED.
    job = q.claim()
    q.fail(job.id)
    assert q.status(jid) is JobStatus.FAILED


def test_expired_lease_respects_max_attempts() -> None:
    clock = FakeClock()
    q = LeasedQueue(clock=clock, lease_seconds=10, max_attempts=1)
    jid = q.submit("work")
    q.claim()  # attempt 1 (max_attempts=1)
    clock.advance(11)
    q.sweep_stuck()  # lease expired, but already at the attempt cap
    assert q.status(jid) is JobStatus.FAILED  # not requeued — parked


def test_sweep_only_touches_expired_jobs() -> None:
    clock = FakeClock()
    q = LeasedQueue(clock=clock, lease_seconds=30)
    old = q.submit("old")
    q.claim()  # claimed at t=0
    clock.advance(31)
    new = q.submit("new")
    q.claim()  # claimed at t=31, still fresh
    assert q.sweep_stuck() == 1  # only the old one expired
    assert q.status(old) is JobStatus.PENDING
    assert q.status(new) is JobStatus.PROCESSING
