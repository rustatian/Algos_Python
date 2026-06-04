"""Producer-Consumer / Image-Processing Service (DB-backed queue).

A producer submits work; consumers (workers) process it asynchronously.
Two designs sit on a ladder. The in-memory **bounded blocking queue** is
the classic concurrency primitive (LeetCode #1188): a producer blocks when
the queue is full, a consumer blocks when it is empty. The **DB-backed
queue** is the production pattern: the producer COMMITS the job to a
durable store and returns 200 immediately (not after the work); a worker
atomically claims a pending row (``SELECT ... FOR UPDATE SKIP LOCKED``),
marks it PROCESSING, does the work, then marks it SUCCESS/FAILED. A
background monitor requeues rows stuck in PROCESSING past a lease — the
worker that held them is presumed dead.

This package ports the problem as a tiered learning ladder:

Tier 1: BoundedBlockingQueue — in-memory; blocks on full/empty (#1188).
Tier 2: DBBackedQueue        — durable job rows; claim / complete / fail.
Tier 3: LeasedQueue          — Tier 2 + leases (requeue dead workers) + retries.
Tier 4: DistributedTaskQueue — HLD only (see README); Celery/SQS at scale.

Input (Tier 2/3 surface):
    submit(payload) -> job_id      — enqueue durably, return immediately.
    claim() -> Job | None          — atomically take one pending job.
    complete(job_id, result=None)  — mark SUCCESS.
    fail(job_id)                   — mark FAILED (Tier 3: retry until a cap).
    status(job_id) -> JobStatus
Output:
    submit returns the new job id; claim returns a Job (or None if idle);
    status reports PENDING / PROCESSING / SUCCESS / FAILED.

Example (the DB-backed lifecycle):
    q = DBBackedQueue()
    jid = q.submit("resize:img1")     # producer: durable, returns now
    job = q.claim()                   # worker: job.status is PROCESSING
    q.complete(job.id, "thumb1")
    q.status(jid)                     -> JobStatus.SUCCESS

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import collections
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable


class BoundedBlockingQueue:
    """Tier 1: a fixed-capacity blocking queue (LeetCode #1188).

    Producers block on ``enqueue`` while the queue is full; consumers block
    on ``dequeue`` while it is empty. Two Conditions over one lock signal
    the two directions, so a producer wakes a waiting consumer and vice
    versa.

    Input:
        __init__(capacity: int)
        enqueue(item) -> None   — blocks while full.
        dequeue() -> item       — blocks while empty.
        size() -> int
    Output:
        FIFO order; enqueue/dequeue block rather than fail when at a bound.

    Standard library:
        collections.deque — O(1) append/popleft FIFO buffer.
        threading.Condition — two of them on one lock: ``not_full`` (a
            producer waits here) and ``not_empty`` (a consumer waits here).

    Pseudocode:
        enqueue(item):
            with not_full:
                while len(items) == capacity: not_full.wait()
                items.append(item); not_empty.notify()
        dequeue():
            with not_empty:
                while not items: not_empty.wait()
                item = items.popleft(); not_full.notify(); return item

    Why two Conditions sharing one lock:
        Producers and consumers wait for opposite events (space vs. an
        item). Separate Conditions let enqueue wake only consumers and
        dequeue wake only producers — no spurious wakes of the wrong side.
        Both guard the same buffer via the shared lock, so it is never read
        and written at once.

    Why ``while`` (not ``if``) around wait():
        After waking, the awaited condition may no longer hold (another
        thread raced in, or the wake was spurious). Re-checking in a loop is
        mandatory for correctness.

    Complexity:
        enqueue/dequeue: O(1) plus blocking; throughput bounded by the lock.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._items: collections.deque[Any] = collections.deque()
        self._lock = threading.Lock()
        self._not_full = threading.Condition(self._lock)
        self._not_empty = threading.Condition(self._lock)

    def enqueue(self, item: Any) -> None:
        with self._not_full:
            while len(self._items) >= self._capacity:
                self._not_full.wait()
            self._items.append(item)
            self._not_empty.notify()

    def dequeue(self) -> Any:
        with self._not_empty:
            while not self._items:
                self._not_empty.wait()
            item = self._items.popleft()
            self._not_full.notify()
            return item

    def size(self) -> int:
        with self._lock:
            return len(self._items)


class JobStatus(StrEnum):
    """Lifecycle of a job row in the DB-backed queue."""

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Job:
    """One job row. ``claimed_at`` / ``attempts`` support Tier 3's leasing."""

    id: int
    payload: Any
    status: JobStatus = JobStatus.PENDING
    result: Any = None
    claimed_at: float | None = None
    attempts: int = 0


class DBBackedQueue:
    """Tier 2: a durable job queue with atomic claim (the production pattern).

    The producer ``submit``s a job — committed and acknowledged immediately,
    BEFORE any work — and a worker later ``claim``s it. The claim is atomic:
    the lock plays the role of ``SELECT ... FOR UPDATE SKIP LOCKED``, so two
    workers never grab the same job. The worker then ``complete``s or
    ``fail``s it.

    Input / Output:
        submit(payload) -> job_id
        claim() -> Job | None       — first PENDING job, now PROCESSING.
        complete(job_id, result=None) -> None    — mark SUCCESS.
        fail(job_id) -> None                     — mark FAILED (terminal here).
        status(job_id) -> JobStatus

    Example:
        q = DBBackedQueue()
        jid = q.submit("work")
        job = q.claim();  q.complete(job.id)
        q.status(jid)  -> JobStatus.SUCCESS

    Standard library:
        dict — the "table" of job rows. threading.Lock — the row/table lock
            that makes claim atomic.

    Pseudocode:
        submit(payload):  insert row(PENDING); return id     # commit, ack now
        claim():          with lock: pick first PENDING row;
                          set PROCESSING, claimed_at=now, attempts+=1; return it
        complete(id):     row.status = SUCCESS
        fail(id):         row.status = FAILED

    Why the producer commits BEFORE the work (returns 200 on commit):
        Durability and decoupling: once the row is committed, the job
        survives a crash and the producer is freed immediately. The work
        happens asynchronously on a worker — the request latency is the DB
        write, not the (possibly slow) processing.

    Why claim must be atomic (SKIP LOCKED):
        Many workers poll concurrently. Without the lock/row-lock, two could
        read the same PENDING row and both process it — duplicated work.
        Claiming under the lock (skipping rows another worker holds) gives
        each job to exactly one worker.

    The weakness this tier still has:
        If a worker dies after ``claim`` but before ``complete``/``fail``,
        the row is stuck in PROCESSING forever — no one retries it. Tier 3
        fixes that with leases.

    Complexity:
        submit/complete/fail/status: O(1). claim: O(N) scan for the first
        PENDING row (a real DB uses an index on status).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._jobs: dict[int, Job] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._clock = clock

    def submit(self, payload: Any) -> int:
        with self._lock:
            job_id = self._next_id
            self._next_id += 1
            self._jobs[job_id] = Job(id=job_id, payload=payload)
            return job_id

    def claim(self) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status is JobStatus.PENDING:
                    job.status = JobStatus.PROCESSING
                    job.claimed_at = self._clock()
                    job.attempts += 1
                    return job
            return None

    def complete(self, job_id: int, result: Any = None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.SUCCESS
            job.result = result

    def fail(self, job_id: int) -> None:
        with self._lock:
            self._jobs[job_id].status = JobStatus.FAILED

    def status(self, job_id: int) -> JobStatus:
        with self._lock:
            return self._jobs[job_id].status


class LeasedQueue(DBBackedQueue):
    """Tier 3: Tier 2 plus leases (recover dead workers) and retries.

    Two resilience additions over Tier 2:

      1. **Lease + monitor.** A claimed job carries ``claimed_at``. If it
         stays PROCESSING past ``lease_seconds``, the worker is presumed
         dead; ``sweep_stuck()`` requeues the job (back to PENDING) so
         another worker retries it. This is the background-monitor row.
      2. **Bounded retries.** A ``fail`` (or an expired lease) requeues the
         job until ``max_attempts`` is reached, after which it is parked in
         FAILED — so a poison job cannot loop forever.

    A monotonic clock is injected so the lease/monitor logic is testable
    without real waiting.

    Input:
        __init__(clock=time.monotonic, lease_seconds=30.0, max_attempts=3)
        sweep_stuck() -> int   — requeue/expire PROCESSING jobs past lease;
                                 returns how many it acted on.
        (submit / claim / complete / status inherited; fail overridden.)
    Output:
        sweep_stuck returns the count of jobs it requeued or failed.

    Example (a dead worker's job is recovered):
        clock = FakeClock()
        q = LeasedQueue(clock=clock, lease_seconds=30)
        jid = q.submit("work"); q.claim()      # worker claims, then "dies"
        clock.advance(31)
        q.sweep_stuck()                        -> 1   (lease expired -> requeued)
        q.status(jid)                          -> PENDING   (claimable again)

    Pseudocode:
        fail(id):
            attempts >= max_attempts ? status=FAILED : (status=PENDING; claimed_at=None)
        sweep_stuck():
            for job in jobs where status==PROCESSING and now-claimed_at >= lease:
                attempts >= max_attempts ? status=FAILED : (status=PENDING; claimed_at=None)

    Why a lease rather than a heartbeat:
        A lease needs no liveness traffic — the monitor only looks at
        ``claimed_at`` + a timeout. A worker that crashes, hangs, or is
        network-partitioned all look the same: its lease simply expires and
        the job is reclaimed. (Real systems often add heartbeats to extend a
        long job's lease; the timeout is the backstop.)

    Why bound retries:
        A job that always fails (corrupt input) would otherwise be requeued
        forever, burning workers. Capping attempts parks it in FAILED for a
        human / dead-letter queue.

    Complexity:
        sweep_stuck: O(N) over job rows (a real DB indexes status +
        claimed_at). Other operations as Tier 2.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        lease_seconds: float = 30.0,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(clock=clock)
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts

    def _requeue_or_fail(self, job: Job) -> None:
        """Send a job back to PENDING for retry, or to FAILED if out of tries."""
        if job.attempts >= self._max_attempts:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.PENDING
            job.claimed_at = None

    def fail(self, job_id: int) -> None:
        with self._lock:
            self._requeue_or_fail(self._jobs[job_id])

    def sweep_stuck(self) -> int:
        with self._lock:
            now = self._clock()
            acted = 0
            for job in self._jobs.values():
                if (
                    job.status is JobStatus.PROCESSING
                    and job.claimed_at is not None
                    and now - job.claimed_at >= self._lease_seconds
                ):
                    self._requeue_or_fail(job)
                    acted += 1
            return acted
