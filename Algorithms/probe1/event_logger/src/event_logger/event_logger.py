"""Event Logger — batching, fsync, and group commit (WAL design).

Appending durable log records is bottlenecked by ``fsync`` — forcing the
OS to flush to stable storage costs a disk rotation / flash program, on the
order of milliseconds. Calling ``fsync`` once per event caps throughput at
roughly the device's IOPS (~hundreds/sec on spinning disks). The fix is
**group commit**: many concurrent appenders share ONE ``fsync``, so a
batch of N events costs one flush instead of N. Postgres and MySQL/InnoDB
implement exactly this for their write-ahead logs.

This package ports the problem as a tiered learning ladder:

Tier 1: SimpleLogger      — one fsync per event; durable but slow (the baseline).
Tier 2: BatchedLogger     — buffer + size/explicit-trigger flush; one fsync per
                            batch. Fast, but events are not durable until flushed.
Tier 3: GroupCommitLogger — concurrent appenders BLOCK until durable; a
                            background flusher does one fsync per batch and
                            wakes exactly the appenders whose events landed.
Tier 4: DistributedLog    — HLD only (see README); replicated WAL (Kafka/Raft).

The durable sink is injected (a small ``write`` + ``fsync`` Protocol), so
tests use an in-memory sink that counts fsyncs and models durability — no
real disk, fully deterministic.

Input (shared surface):
    __init__(sink)
    append(event: str) -> None
        Record one event. Tier 1/3 return only once the event is durable;
        Tier 2 returns after buffering (durable on the next flush).
Output:
    append returns None; the event is persisted to the sink.

Example 1 (Tier 1 — one fsync each):
    sink = InMemorySink()
    log = SimpleLogger(sink)
    log.append("a"); log.append("b")
    sink.fsync_count        -> 2        # one flush per event

Example 2 (Tier 2 — one fsync per batch):
    log = BatchedLogger(sink, batch_size=3)
    log.append("a"); log.append("b"); log.append("c")   # batch full -> flush
    sink.fsync_count        -> 1        # three events, one flush

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import threading
import time
from typing import Protocol


class Sink(Protocol):
    """A durable destination: buffered ``write`` plus a ``fsync`` barrier.

    Models the OS file API. ``write`` stages bytes in the OS page cache
    (fast, NOT durable); ``fsync`` forces everything written so far to
    stable storage (slow, the durability point). The whole problem is
    minimizing ``fsync`` calls without losing durability.
    """

    def write(self, record: str) -> None: ...
    def fsync(self) -> None: ...


class InMemorySink:
    """A test/inspection sink that models write-then-fsync durability.

    ``write`` stages a record; ``fsync`` marks everything staged so far as
    durable and counts the flush. ``durable_records`` returns only what
    survived a crash at this instant (staged-but-unflushed records would be
    lost), which is what lets tests assert real durability semantics.
    """

    def __init__(self) -> None:
        self.records: list[str] = []  # staged (in "page cache")
        self.fsync_count: int = 0
        self._durable_upto: int = 0  # records[:_durable_upto] are on stable storage

    def write(self, record: str) -> None:
        self.records.append(record)

    def fsync(self) -> None:
        self.fsync_count += 1
        self._durable_upto = len(self.records)

    def durable_records(self) -> list[str]:
        return self.records[: self._durable_upto]


class SimpleLogger:
    """Tier 1: fsync after every event — durable, but throughput-bound.

    The naive correct logger: write the event, immediately fsync. Every
    append is durable the instant it returns, but the per-event fsync caps
    throughput at the device's flush rate.

    Input / Output:
        append(event: str) -> None — durable on return.

    Example:
        log = SimpleLogger(sink); log.append("x")
        sink.fsync_count -> 1   # one flush for the one event

    Pseudocode:
        append(event):  sink.write(event); sink.fsync()

    Why it is slow:
        N events cost N fsyncs. If a fsync is ~10 ms, that is ~100
        events/sec no matter how fast the CPU is — the disk barrier, not
        compute, is the ceiling. Tiers 2 and 3 amortize the barrier.

    Complexity:
        append: O(1) work + one fsync (the dominant real cost).
    """

    def __init__(self, sink: Sink) -> None:
        self._sink = sink

    def append(self, event: str) -> None:
        self._sink.write(event)
        self._sink.fsync()  # durable before we return


class BatchedLogger:
    """Tier 2: buffer events, flush a whole batch with ONE fsync.

    Accumulate events in memory; when the buffer reaches ``batch_size`` (or
    on an explicit ``flush()``), write them all and fsync once. N events
    cost ~N/batch_size fsyncs — the throughput win. The trade-off:
    buffered-but-unflushed events are NOT durable, so a crash loses them.
    Single-threaded.

    Input:
        __init__(sink, batch_size: int)
        append(event: str) -> None   — buffers; auto-flushes when full.
        flush() -> None              — write the buffer + one fsync.
    Output:
        Events become durable only at a flush (size-triggered or explicit).

    Example:
        log = BatchedLogger(sink, batch_size=3)
        log.append("a"); log.append("b")   # buffered; fsync_count == 0
        log.append("c")                     # batch full -> flush
        sink.fsync_count -> 1

    Pseudocode:
        append(event):
            buffer.append(event)
            if len(buffer) >= batch_size: flush()
        flush():
            for e in buffer: sink.write(e)
            if buffer: sink.fsync()      # one flush for the whole batch
            buffer.clear()

    Why durability is weaker than Tier 1:
        An event sitting in the buffer has not been fsynced, so a crash
        loses it. This is the classic throughput/durability trade — Tier 3
        recovers BOTH by making the appender wait for the batch's fsync.

    Complexity:
        append: O(1) amortized; one fsync per batch_size events.
    """

    def __init__(self, sink: Sink, batch_size: int) -> None:
        self._sink = sink
        self._batch_size = batch_size
        self._buffer: list[str] = []

    def append(self, event: str) -> None:
        self._buffer.append(event)
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        for event in self._buffer:
            self._sink.write(event)
        self._sink.fsync()  # ONE flush for the whole batch
        self._buffer.clear()


class GroupCommitLogger:
    """Tier 3: concurrent group commit — durable AND batched.

    Recovers Tier 1's durability and Tier 2's throughput at once. Many
    threads call ``append`` concurrently; each enqueues its event and BLOCKS
    until durable. A single background flusher coalesces whatever has
    accumulated, issues ONE fsync for the batch, then wakes exactly the
    appenders whose events made it to stable storage. This is the
    Postgres / InnoDB WAL commit path.

    Input:
        __init__(sink, batch_window: float = 0.005)
            batch_window — seconds the flusher lingers to let more events
            join the current batch (the group-commit "wait to form a group").
        append(event: str) -> None — returns only once the event is durable.
        close() -> None            — flush remaining events and stop the flusher.
    Output:
        append is durable on return; close() drains and joins the flusher.

    Example (the win — many appends, few fsyncs):
        log = GroupCommitLogger(sink, batch_window=0.02)
        # 50 threads each call log.append(...) at once.
        # All 50 events are durable; sink.fsync_count is a handful, not 50.

    Standard library:
        threading.Condition — the appenders wait on it for "my event is
            durable"; the flusher waits on it for "the buffer is non-empty",
            and notifies it after each fsync. One lock guards the buffer and
            the durability watermark.
        time.sleep — the batch window: a brief linger so concurrent
            appenders coalesce into one fsync instead of racing to flush.

    Pseudocode:
        state: buffer=[], next_seq=0, durable_seq=0   (seq < durable_seq is durable)

        append(event):
            with cond:
                seq = next_seq; next_seq += 1
                buffer.append(event); cond.notify_all()      # wake flusher
                while durable_seq <= seq: cond.wait()        # block until durable

        flusher loop:
            with cond: wait until buffer non-empty (or closed)
            sleep(batch_window)                              # let the group form
            with cond:
                batch = buffer; buffer = []
                end = next_seq
                for e in batch: sink.write(e)
                sink.fsync()                                 # ONE flush
                durable_seq = end; cond.notify_all()         # wake appenders

    Why a sequence number and a durability watermark:
        Each appender must know when ITS event is durable, not just "a"
        flush happened. Assigning monotonic seqs and advancing a single
        ``durable_seq`` watermark after each fsync lets every waiter check
        ``durable_seq > my_seq`` — the same idea as an LSN (log sequence
        number) in a real WAL.

    Why the batch window helps:
        Without a brief linger, the flusher would fsync the first event
        alone, then the next, etc. — degenerating toward Tier 1. Waiting a
        few milliseconds lets a burst of appenders join one batch, so one
        fsync covers them all. It trades a little latency for large
        throughput, exactly as real group commit does.

    Complexity:
        append: O(1) + the wait for the next group fsync. fsyncs per second
        are bounded by ~1/batch_window regardless of append rate — the
        throughput decoupling that is the whole point.
    """

    def __init__(self, sink: Sink, batch_window: float = 0.005) -> None:
        self._sink = sink
        self._batch_window = batch_window
        self._cond = threading.Condition()
        self._buffer: list[str] = []
        self._next_seq = 0  # seq for the next appended event
        self._durable_seq = 0  # every event with seq < _durable_seq is durable
        self._closed = False
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._flusher.start()

    def append(self, event: str) -> None:
        with self._cond:
            seq = self._next_seq
            self._next_seq += 1
            self._buffer.append(event)
            self._cond.notify_all()  # wake the flusher
            # Block until our event has been fsynced.
            while self._durable_seq <= seq:
                self._cond.wait()

    def _flush_loop(self) -> None:
        while True:
            with self._cond:
                while not self._buffer and not self._closed:
                    self._cond.wait()
                if self._closed and not self._buffer:
                    return
            # Linger briefly (outside the lock) so a burst of appenders can
            # join this batch — unless we are closing, where we drain at once.
            if self._batch_window and not self._closed:
                time.sleep(self._batch_window)
            with self._cond:
                if not self._buffer:
                    continue
                batch = self._buffer
                self._buffer = []
                end_seq = self._next_seq  # everything appended so far
                for event in batch:
                    self._sink.write(event)
                self._sink.fsync()  # ONE flush for the whole group
                self._durable_seq = end_seq
                self._cond.notify_all()  # wake every now-durable appender

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._flusher.join()
