"""Tests for the Event Logger ladder.

Each tier has a distinct durability/throughput contract, so the tests are
per-tier rather than parametrized. The InMemorySink models write-then-fsync
durability and counts fsyncs, which is how we assert "one flush per batch"
(the whole point) deterministically. The Tier 3 concurrency tests use a
Barrier so all appenders fire at once, making the batching observable
without relying on luck.
"""

import threading

from event_logger import (
    BatchedLogger,
    GroupCommitLogger,
    InMemorySink,
    SimpleLogger,
)


# ----------------------------------------------------------------------
# Tier 1 — SimpleLogger: one fsync per event.
# ----------------------------------------------------------------------


def test_simple_logger_is_durable_immediately() -> None:
    sink = InMemorySink()
    log = SimpleLogger(sink)
    log.append("a")
    assert sink.durable_records() == ["a"]  # durable the instant append returns


def test_simple_logger_one_fsync_per_event() -> None:
    sink = InMemorySink()
    log = SimpleLogger(sink)
    for e in ("a", "b", "c"):
        log.append(e)
    assert sink.fsync_count == 3
    assert sink.durable_records() == ["a", "b", "c"]


# ----------------------------------------------------------------------
# Tier 2 — BatchedLogger: one fsync per batch.
# ----------------------------------------------------------------------


def test_batched_logger_buffers_until_full() -> None:
    sink = InMemorySink()
    log = BatchedLogger(sink, batch_size=3)
    log.append("a")
    log.append("b")
    assert sink.fsync_count == 0  # still buffered
    assert sink.durable_records() == []


def test_batched_logger_flushes_full_batch_with_one_fsync() -> None:
    sink = InMemorySink()
    log = BatchedLogger(sink, batch_size=3)
    log.append("a")
    log.append("b")
    log.append("c")  # batch full -> single flush
    assert sink.fsync_count == 1
    assert sink.durable_records() == ["a", "b", "c"]


def test_batched_logger_explicit_flush() -> None:
    sink = InMemorySink()
    log = BatchedLogger(sink, batch_size=100)
    log.append("a")
    log.append("b")
    log.flush()
    assert sink.fsync_count == 1
    assert sink.durable_records() == ["a", "b"]


def test_batched_logger_flush_empty_is_noop() -> None:
    sink = InMemorySink()
    log = BatchedLogger(sink, batch_size=10)
    log.flush()  # nothing buffered
    assert sink.fsync_count == 0


def test_batched_logger_amortizes_fsyncs() -> None:
    sink = InMemorySink()
    log = BatchedLogger(sink, batch_size=10)
    for i in range(100):
        log.append(str(i))
    # 100 events / batch_size 10 -> 10 fsyncs (vs 100 for Tier 1).
    assert sink.fsync_count == 10
    assert len(sink.durable_records()) == 100


# ----------------------------------------------------------------------
# Tier 3 — GroupCommitLogger: durable AND batched, under concurrency.
# ----------------------------------------------------------------------


def test_group_commit_single_append_is_durable() -> None:
    sink = InMemorySink()
    log = GroupCommitLogger(sink, batch_window=0.0)
    log.append("only")
    assert sink.durable_records() == ["only"]  # blocked until durable
    log.close()


def test_group_commit_all_events_durable_under_concurrency() -> None:
    """Every concurrent append must be durable once it returns; none lost."""
    sink = InMemorySink()
    log = GroupCommitLogger(sink, batch_window=0.01)
    n = 50
    barrier = threading.Barrier(n)

    def appender(i: int) -> None:
        barrier.wait()  # all fire together to exercise batching
        log.append(f"event-{i}")

    threads = [threading.Thread(target=appender, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()

    durable = sink.durable_records()
    assert len(durable) == n
    assert set(durable) == {f"event-{i}" for i in range(n)}


def test_group_commit_coalesces_into_fewer_fsyncs_than_events() -> None:
    """The whole point: N concurrent appends share far fewer than N fsyncs."""
    sink = InMemorySink()
    log = GroupCommitLogger(sink, batch_window=0.03)
    n = 40
    barrier = threading.Barrier(n)

    def appender(i: int) -> None:
        barrier.wait()
        log.append(f"e{i}")

    threads = [threading.Thread(target=appender, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.close()

    assert len(sink.durable_records()) == n
    assert sink.fsync_count >= 1
    # Batching MUST have coalesced: fewer flushes than events.
    assert sink.fsync_count < n


def test_group_commit_close_flushes_and_stops() -> None:
    sink = InMemorySink()
    log = GroupCommitLogger(sink, batch_window=0.005)
    log.append("a")
    log.close()  # must drain and join cleanly
    assert sink.durable_records() == ["a"]
    assert not log._flusher.is_alive()  # flusher thread has stopped
