"""Tests for Reader-Writer Lock with semaphores.

Every tier exposes the same acquire_read / release_read / acquire_write
/ release_write surface; the correctness tests are parametrized over
RWLOCKS so adding a new tier just appends it to the list.

Tests use threading.Event for deterministic ordering: a thread reaches
a checkpoint and sets an Event; another thread waits for the Event
before proceeding. ``Event.wait(timeout=T)`` returns False on timeout
without raising — making "this should NOT happen within T seconds"
checkable as ``not event.wait(0.1)``.
"""

import threading

import pytest

from rwlock_semaphores.rwlock import (
    ReaderPriorityRWLock,
    WriterPriorityRWLock,
)

# Every tier with the same RW lock surface goes here.
RWLOCKS = [ReaderPriorityRWLock, WriterPriorityRWLock]

# Per-event timeouts. ACQUIRE_TIMEOUT is the upper bound for an
# operation that SHOULD succeed; BLOCK_TIMEOUT is how long we wait to
# confirm an operation is BLOCKED (must NOT succeed within).
ACQUIRE_TIMEOUT = 2.0
BLOCK_TIMEOUT = 0.1


# ---------------------------------------------------------------------------
# Single-threaded sanity — every lock must let one thread go through
# the acquire/release motions without deadlocking.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_single_thread_read_cycle(lock_cls: type) -> None:
    """A single thread can acquire_read then release_read."""
    lock = lock_cls()
    lock.acquire_read()
    lock.release_read()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_single_thread_write_cycle(lock_cls: type) -> None:
    """A single thread can acquire_write then release_write."""
    lock = lock_cls()
    lock.acquire_write()
    lock.release_write()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_single_thread_serial_writes(lock_cls: type) -> None:
    """A single thread can acquire/release write repeatedly."""
    lock = lock_cls()
    for _ in range(5):
        lock.acquire_write()
        lock.release_write()


# ---------------------------------------------------------------------------
# Concurrency contracts — the lock's reason for existing.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_two_readers_run_concurrently(lock_cls: type) -> None:
    """Two readers can both hold the lock simultaneously. Each enters
    its critical section before the other releases.
    """
    lock = lock_cls()
    r1_in = threading.Event()
    r2_in = threading.Event()
    proceed = threading.Event()

    def reader(in_event: threading.Event) -> None:
        lock.acquire_read()
        in_event.set()
        proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_read()

    t1 = threading.Thread(target=reader, args=(r1_in,))
    t2 = threading.Thread(target=reader, args=(r2_in,))
    t1.start()
    t2.start()

    # Both must be inside before either releases.
    assert r1_in.wait(ACQUIRE_TIMEOUT)
    assert r2_in.wait(ACQUIRE_TIMEOUT)

    proceed.set()
    t1.join()
    t2.join()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_writer_blocks_reader(lock_cls: type) -> None:
    """A writer holding the lock blocks a concurrent reader; the
    reader enters only after the writer releases.
    """
    lock = lock_cls()
    w_in = threading.Event()
    proceed = threading.Event()
    r_in = threading.Event()

    def writer() -> None:
        lock.acquire_write()
        w_in.set()
        proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_write()

    def reader() -> None:
        lock.acquire_read()
        r_in.set()
        lock.release_read()

    tw = threading.Thread(target=writer)
    tw.start()
    assert w_in.wait(ACQUIRE_TIMEOUT)

    tr = threading.Thread(target=reader)
    tr.start()

    # Reader must NOT enter while writer holds.
    assert not r_in.wait(BLOCK_TIMEOUT)

    proceed.set()
    tw.join()

    # After writer releases, reader enters.
    assert r_in.wait(ACQUIRE_TIMEOUT)
    tr.join()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_reader_blocks_writer(lock_cls: type) -> None:
    """A reader holding the lock blocks a concurrent writer; the
    writer enters only after the reader releases.
    """
    lock = lock_cls()
    r_in = threading.Event()
    proceed = threading.Event()
    w_in = threading.Event()

    def reader() -> None:
        lock.acquire_read()
        r_in.set()
        proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_read()

    def writer() -> None:
        lock.acquire_write()
        w_in.set()
        lock.release_write()

    tr = threading.Thread(target=reader)
    tr.start()
    assert r_in.wait(ACQUIRE_TIMEOUT)

    tw = threading.Thread(target=writer)
    tw.start()

    # Writer must NOT enter while reader holds.
    assert not w_in.wait(BLOCK_TIMEOUT)

    proceed.set()
    tr.join()

    # After reader releases, writer enters.
    assert w_in.wait(ACQUIRE_TIMEOUT)
    tw.join()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_writer_excludes_writer(lock_cls: type) -> None:
    """Two writers cannot hold the lock simultaneously."""
    lock = lock_cls()
    w1_in = threading.Event()
    proceed = threading.Event()
    w2_in = threading.Event()

    def writer1() -> None:
        lock.acquire_write()
        w1_in.set()
        proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_write()

    def writer2() -> None:
        lock.acquire_write()
        w2_in.set()
        lock.release_write()

    t1 = threading.Thread(target=writer1)
    t1.start()
    assert w1_in.wait(ACQUIRE_TIMEOUT)

    t2 = threading.Thread(target=writer2)
    t2.start()

    # Second writer must not enter while first holds.
    assert not w2_in.wait(BLOCK_TIMEOUT)

    proceed.set()
    t1.join()

    # After first releases, second enters.
    assert w2_in.wait(ACQUIRE_TIMEOUT)
    t2.join()


@pytest.mark.parametrize("lock_cls", RWLOCKS)
def test_writer_waits_for_all_readers(lock_cls: type) -> None:
    """A writer blocks until EVERY reader has released — including
    readers that joined after the writer started waiting.
    """
    lock = lock_cls()
    r1_in = threading.Event()
    r2_in = threading.Event()
    r1_proceed = threading.Event()
    r2_proceed = threading.Event()
    w_in = threading.Event()

    def reader(in_event: threading.Event, proceed_event: threading.Event) -> None:
        lock.acquire_read()
        in_event.set()
        proceed_event.wait(ACQUIRE_TIMEOUT)
        lock.release_read()

    def writer() -> None:
        lock.acquire_write()
        w_in.set()
        lock.release_write()

    t1 = threading.Thread(target=reader, args=(r1_in, r1_proceed))
    t2 = threading.Thread(target=reader, args=(r2_in, r2_proceed))
    t1.start()
    t2.start()
    assert r1_in.wait(ACQUIRE_TIMEOUT)
    assert r2_in.wait(ACQUIRE_TIMEOUT)

    tw = threading.Thread(target=writer)
    tw.start()

    # Writer is blocked until both readers release.
    assert not w_in.wait(BLOCK_TIMEOUT)

    r1_proceed.set()
    t1.join()

    # One reader released; writer still waits for the other.
    assert not w_in.wait(BLOCK_TIMEOUT)

    r2_proceed.set()
    t2.join()

    # Both readers gone; writer enters.
    assert w_in.wait(ACQUIRE_TIMEOUT)
    tw.join()


# ---------------------------------------------------------------------------
# Tier 2 only — the priority shift. The single discriminator between
# Tier 1 (reader-priority) and Tier 2 (writer-priority).
#
# Under Tier 1, a reader arriving while a writer waits joins the existing
# readers. Under Tier 2, that reader blocks until the writer has finished.
# ---------------------------------------------------------------------------


def test_writer_priority_blocks_new_readers_while_writer_queued() -> None:
    """The Tier 2 discriminator: while a writer is queued, NEW readers
    must wait until the writer has run and released — even though
    existing readers are still holding the lock.

    A Tier 1 ReaderPriorityRWLock would admit the second reader
    immediately; a correct Tier 2 keeps them out.
    """
    lock = WriterPriorityRWLock()
    r1_in = threading.Event()
    r1_proceed = threading.Event()
    w_in = threading.Event()
    w_proceed = threading.Event()
    r2_in = threading.Event()

    def reader1() -> None:
        lock.acquire_read()
        r1_in.set()
        r1_proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_read()

    def writer() -> None:
        lock.acquire_write()
        w_in.set()
        w_proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_write()

    def reader2() -> None:
        lock.acquire_read()
        r2_in.set()
        lock.release_read()

    # R1 enters and holds the read lock.
    t1 = threading.Thread(target=reader1)
    t1.start()
    assert r1_in.wait(ACQUIRE_TIMEOUT)

    # W queues — it can't get resource (R1 holds it), but it MUST close
    # the reader gate first. Give the thread time to reach that point.
    tw = threading.Thread(target=writer)
    tw.start()
    # W should not yet be inside (R1 holds resource).
    assert not w_in.wait(BLOCK_TIMEOUT)

    # R2 arrives — must block because W has closed the reader gate.
    tr2 = threading.Thread(target=reader2)
    tr2.start()
    assert not r2_in.wait(BLOCK_TIMEOUT)

    # R1 releases — W (not R2) gets the lock next.
    r1_proceed.set()
    t1.join()
    assert w_in.wait(ACQUIRE_TIMEOUT)
    # R2 is STILL blocked — W now holds the lock.
    assert not r2_in.is_set()

    # W releases — reader gate reopens; R2 finally enters.
    w_proceed.set()
    tw.join()
    assert r2_in.wait(ACQUIRE_TIMEOUT)
    tr2.join()


def test_writer_priority_writers_chain_through_consecutively() -> None:
    """Two queued writers run one after the other, each immediately
    after the previous releases — the reader gate stays closed across
    the chain (write_count stays > 0).
    """
    lock = WriterPriorityRWLock()
    r_in = threading.Event()
    r_proceed = threading.Event()
    w1_in = threading.Event()
    w1_proceed = threading.Event()
    w2_in = threading.Event()
    w2_proceed = threading.Event()
    r_late_in = threading.Event()

    def reader_holder() -> None:
        lock.acquire_read()
        r_in.set()
        r_proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_read()

    def writer1() -> None:
        lock.acquire_write()
        w1_in.set()
        w1_proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_write()

    def writer2() -> None:
        lock.acquire_write()
        w2_in.set()
        w2_proceed.wait(ACQUIRE_TIMEOUT)
        lock.release_write()

    def reader_late() -> None:
        lock.acquire_read()
        r_late_in.set()
        lock.release_read()

    # A reader holds the lock; both writers queue behind it.
    tr = threading.Thread(target=reader_holder)
    tr.start()
    assert r_in.wait(ACQUIRE_TIMEOUT)

    tw1 = threading.Thread(target=writer1)
    tw1.start()
    tw2 = threading.Thread(target=writer2)
    tw2.start()
    # Give both writers time to register (increment write_count to 2).
    assert not w1_in.wait(BLOCK_TIMEOUT)
    assert not w2_in.wait(BLOCK_TIMEOUT)

    # A late reader arrives — should be blocked behind the writers.
    trl = threading.Thread(target=reader_late)
    trl.start()
    assert not r_late_in.wait(BLOCK_TIMEOUT)

    # Reader releases — W1 takes the lock.
    r_proceed.set()
    tr.join()
    assert w1_in.wait(ACQUIRE_TIMEOUT)
    # W2 still waiting; late reader still waiting (gate still closed).
    assert not w2_in.is_set()
    assert not r_late_in.is_set()

    # W1 releases — W2 takes the lock immediately (gate stays closed).
    w1_proceed.set()
    tw1.join()
    assert w2_in.wait(ACQUIRE_TIMEOUT)
    # Late reader STILL blocked — W2 is holding the lock now.
    assert not r_late_in.wait(BLOCK_TIMEOUT)

    # W2 finally releases — last writer reopens the gate.
    w2_proceed.set()
    tw2.join()

    # Gate is now open; late reader finally enters.
    assert r_late_in.wait(ACQUIRE_TIMEOUT)
    trl.join()
