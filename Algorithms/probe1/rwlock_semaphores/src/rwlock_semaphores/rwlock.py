"""Reader-Writer Lock with semaphores — the standard MT interview question.

A read-write lock allows **multiple concurrent readers** but only **one
writer** at a time, with strict mutual exclusion between readers and
writers. Either many readers run together OR a single writer runs alone;
never both.

Input:
    acquire_read() / release_read()  — paired calls; the caller is a "reader".
    acquire_write() / release_write() — paired calls; the caller is a "writer".
Output:
    No return value. The contract is the *blocking behavior*: callers
    block when the lock's state denies their request, and wake when
    state allows it.

Invariants:
    - When a writer holds the lock, no other thread (reader or writer)
      is inside.
    - When one or more readers hold the lock, no writer is inside, but
      any number of additional readers can join.
    - Acquire and release must be paired by the same caller; the lock
      is NOT reentrant — a thread holding the read lock that calls
      acquire_read() a second time will deadlock.

Example 1 (concurrent readers):
    Thread A: acquire_read(), ..., release_read()
    Thread B: acquire_read(), ..., release_read()    (concurrent with A)
    Output: both threads run their critical sections simultaneously —
        the second reader's acquire_read does not block on the first.

Example 2 (writer exclusion):
    Thread W: acquire_write(), ..., release_write()
    Thread R: acquire_read()                          (concurrent with W)
    Output: Thread R blocks inside acquire_read() until W's
        release_write() runs. Then R proceeds.

Example 3 (lock upgrade is disallowed):
    Thread A: acquire_read()
    Thread A: acquire_write()        # NOT a valid upgrade — would deadlock.
    Output: deadlock. If you might write, acquire write from the start.
        The pattern "release read, then acquire write" is correct but
        opens a state-gap; not modeled here.

Modeled on the classic "reader-writer lock with semaphores"
interview question — "the standard MT question they ask everybody"
per Blind. The classical algorithm comes from Courtois, Heymans, and
Parnas (1971), "Concurrent Control with Readers and Writers."

Related LeetCode concurrency problems: #1226 (Dining Philosophers),
#1117 (Building H2O), #1188 (Bounded Blocking Queue), #1114 (Print in
Order).

This package ports the problem as a tiered learning ladder. Each tier
exposes the same acquire_read / release_read / acquire_write /
release_write surface; what changes is the *fairness contract* —
which side can starve which.

Tier 1a: WriterPriorityRWLock  — writers block new readers; readers can starve.
Tier 1b: ReaderPriorityRWLock  — readers proceed freely; writers can starve.
Tier 3:  FairRWLock            — FIFO waiter queue; neither starves.
Tier 4:  DistributedRWLock     — cross-machine RW lock (the system-design follow-up).

Tiers 1a and 1b are siblings — both are valid "Tier 1" answers to
"implement a basic RW lock," differing only in which side can starve.
1a (writer-priority) corresponds to Courtois et al.'s "second
readers-writers problem"; 1b (reader-priority) to the "first." The
classical textbook ladder places these as Tier 1 and Tier 2; this port
treats them as fairness variants at the same conceptual tier, with
Tier 3 (FIFO) being the structural fairness fix and Tier 4 being the
distributed extension.

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import threading


class WriterPriorityRWLock:
    """Tier 1a: writer-priority RW lock.

    Solution to Courtois et al.'s "second readers-writers problem":
    once any writer is queued, NEW readers wait until the writer has
    completed — even if other readers are currently holding the lock.
    Existing readers finish naturally; once they all release, the
    queued writer enters; writers chain through until none are left;
    then waiting readers stream in.

    This fixes Tier 1b's reader starvation problem: a continuous stream
    of readers can no longer keep writers out. The trade-off is
    symmetric — a continuous stream of writers can now starve readers.
    Tier 3 (FIFO) fixes both directions.

    Mechanism: Tier 1b's ``resource`` + ``reader_count_lock`` +
    ``reader_count``, plus a parallel set for writers and a single new
    lock that gates new readers:

      - ``write_count`` + ``write_count_lock`` — symmetric to the
        reader-side bookkeeping, but counts writers waiting OR active.
      - ``reader_gate`` — held by the FIRST writer to arrive
        (acquired in acquire_write) and released by the LAST writer
        to leave (released in release_write). Readers acquire-then-
        release it at entry as a checkpoint. While ANY writer holds
        the reader_gate, new readers block.

    Input / Output:
        Same surface and semantics as ReaderPriorityRWLock — only the
        fairness contract differs. Reads can starve under continuous
        writer arrivals.

    Example 1 (the priority shift — the killer test):
        Thread R1: acquire_read()                # gets in
        Thread W:  acquire_write()               # blocks behind R1
                                                  # also CLOSES reader_gate
        Thread R2: acquire_read()                # BLOCKS — reader_gate is closed
                                                  # (in Tier 1b, R2 would join R1)
        Thread R1: release_read()                # W now gets the lock
        Thread W:  release_write()               # reopens reader_gate
        Thread R2: now gets in

    Example 2 (writers chain through readers):
        Thread R1, R2: acquire_read()            # two readers inside
        Thread W1: acquire_write()               # closes reader_gate, blocks
        Thread W2: acquire_write()               # also blocks; reader_gate
                                                  # already closed (write_count=2)
        Thread R1, R2: release_read()            # W1 enters
        Thread W1: release_write()               # W2 immediately enters
                                                  # (reader_gate still closed —
                                                  # write_count was 2, now 1)
        Thread W2: release_write()               # write_count = 0 →
                                                  # reader_gate opens

    Example 3 (reader starvation — Tier 1a's blind spot):
        Continuous arrival of writers: write_count stays > 0, so
        reader_gate stays closed, so readers wait forever. The mirror
        of Tier 1b's writer-starvation problem. Tier 3 fixes this with
        FIFO queueing.

    Standard library:
        threading.Lock / threading.Semaphore(1) — both work for the
            binary-mutex roles. Tier 1a uses four locks (``resource``,
            ``reader_count_lock``, ``write_count_lock``,
            ``reader_gate``) plus two int counters.

    Pseudocode:
        data:
            resource           — semaphore (initially 1); writer exclusive gate.
            reader_count_lock  — semaphore (initially 1); protects reader_count.
            reader_count       — int (initially 0).

            write_count        — int (initially 0); writers waiting OR active.
            write_count_lock   — semaphore (initially 1); protects write_count.
            reader_gate        — semaphore (initially 1); closed while any writer is queued.

        acquire_read():
            reader_gate.acquire()                # block if writers are queued
            reader_count_lock.acquire()
            reader_count += 1
            if reader_count == 1:
                resource.acquire()                # first reader locks writers out
            reader_count_lock.release()
            reader_gate.release()                 # let other readers through

        release_read():
            reader_count_lock.acquire()
            reader_count -= 1
            if reader_count == 0:
                resource.release()
            reader_count_lock.release()

        acquire_write():
            write_count_lock.acquire()
            write_count += 1
            if write_count == 1:
                reader_gate.acquire()             # first writer closes the gate
            write_count_lock.release()
            resource.acquire()                    # wait for current readers to drain

        release_write():
            resource.release()
            write_count_lock.acquire()
            write_count -= 1
            if write_count == 0:
                reader_gate.release()             # last writer reopens the gate
            write_count_lock.release()

    Why readers acquire-then-release reader_gate (don't hold it):
        Readers must CHECK the gate but not own it. If a reader held
        it, no other reader could enter. Acquire-then-release means
        the reader treats it as a checkpoint — "is the gate open?
        good, I pass through; the next reader can also pass."

    Why the FIRST writer (not every writer) closes reader_gate:
        Once a writer has closed the gate, additional writers piggyback
        on the same closed state. If every writer acquired reader_gate,
        the second writer would deadlock waiting for the first to
        release it. write_count tracks the "is the gate closed?" state.

    Why acquire reader_gate INSIDE write_count_lock and OUTSIDE resource:
        - Inside write_count_lock: the check-and-acquire of reader_gate
          must be atomic with the increment of write_count. Otherwise
          two writers could both observe write_count==0, both increment
          to 1, and both try to acquire reader_gate.
        - OUTSIDE resource: resource is the long-held lock (writers may
          wait for current readers to drain). Holding write_count_lock
          across that wait would block subsequent writers from even
          registering themselves, defeating the priority.

    Complexity:
        Storage: O(1) — four locks, two ints.
        acquire_read / release_read: O(1) lock ops; one extra
            acquire-release of reader_gate vs. Tier 1b.
        acquire_write / release_write: O(1) lock ops; bookkeeping
            around write_count.
        Fairness: writer-priority. Continuous writer arrival starves readers.
    """

    def __init__(self):
        self._resource = threading.Semaphore(1)
        self._reader_count_lock = threading.Semaphore(1)
        self._write_count_lock = threading.Lock()
        self._reader_gate = threading.Semaphore(1)
        self._reader_count: int = 0
        self._writer_count: int = 0

    def acquire_read(self) -> None:
        # Layer 1: once we acquire the gate, we owe a release.
        self._reader_gate.acquire()
        try:
            # Layer 2: once we acquire the count_lock, we owe a release.
            self._reader_count_lock.acquire()
            try:
                # Layer 3: once we bump the counter, on failure below
                # we owe a decrement (the counter "committed" us to
                # holding resource).
                self._reader_count += 1
                if self._reader_count == 1:
                    try:
                        self._resource.acquire()
                    except BaseException:
                        # resource.acquire was interrupted before
                        # actually taking the lock — undo the bump so
                        # the next reader doesn't think a "first reader"
                        # is already inside.
                        self._reader_count -= 1
                        raise
            finally:
                self._reader_count_lock.release()
        finally:
            self._reader_gate.release()
        return None

    def release_read(self) -> None:
        self._reader_count_lock.acquire()
        self._reader_count -= 1
        if self._reader_count == 0:
            self._resource.release()
        self._reader_count_lock.release()
        return None

    def acquire_write(self) -> None:
        self._write_count_lock.acquire()
        self._writer_count += 1
        if self._writer_count == 1:
            self._reader_gate.acquire()
        self._write_count_lock.release()
        self._resource.acquire()
        return None

    def release_write(self) -> None:
        self._resource.release()
        self._write_count_lock.acquire()
        self._writer_count -= 1
        if self._writer_count == 0:
            self._reader_gate.release()
        self._write_count_lock.release()


class ReaderPriorityRWLock:
    """Tier 1b: classic reader-priority RW lock.

    Solution to Courtois et al.'s "first readers-writers problem":
    readers proceed as long as another reader holds the lock — they
    never wait for queued writers. A continuous stream of readers can
    keep writers waiting indefinitely. This is the simplest correct
    RW lock and the textbook "what's an RW lock?" answer.

    The mechanism: a *resource* mutex gates writers (exclusive
    ownership); a *count* mutex protects an integer ``reader_count``.
    The **first** reader in acquires the resource mutex (gating
    writers out); the **last** reader out releases it (letting one
    writer in). Writers always acquire the resource mutex directly.

    Input:
        acquire_read() -> None
        release_read() -> None
        acquire_write() -> None
        release_write() -> None
    Output:
        No return — only blocking/non-blocking behavior.

    Example 1 (two readers run concurrently):
        Thread A: acquire_read(), Thread B: acquire_read()
        Both immediately inside the lock; reader_count is 2.
        Both eventually release_read(); reader_count returns to 0;
        next writer is unblocked.

    Example 2 (writer waits for all readers):
        Thread A: acquire_read()                    # reader_count = 1
        Thread W: acquire_write()                   # blocks on resource mutex
        Thread B: acquire_read()                    # reader_count = 2 (joins A)
        Thread A: release_read()                    # reader_count = 1; W still blocked
        Thread B: release_read()                    # reader_count = 0; resource released; W unblocks
        Thread W proceeds.

    Example 3 (writer starvation — the design's blind spot):
        Thread W: acquire_write()                   # holds the lock
        Thread W: release_write()
        Streams of readers arrive faster than Thread W can re-acquire:
        reader_count is never zero between W's release and re-acquire,
        so a second W call could wait forever. Tier 1a fixes this by
        giving writers priority.

    Standard library:
        threading.Lock / threading.Semaphore(1) — both work for the
            binary-mutex roles. Tier 1b uses just two locks plus an
            int counter: ``resource`` (writer gate) and
            ``reader_count_lock`` (protects reader_count).

    Pseudocode:
        data:
            resource          — semaphore (initially 1); gates writers.
            reader_count_lock — semaphore (initially 1); protects reader_count.
            reader_count      — int (initially 0); number of active readers.

        acquire_read():
            reader_count_lock.acquire()
            reader_count += 1
            if reader_count == 1:
                resource.acquire()                  # first reader locks out writers
            reader_count_lock.release()

        release_read():
            reader_count_lock.acquire()
            reader_count -= 1
            if reader_count == 0:
                resource.release()                  # last reader lets writers in
            reader_count_lock.release()

        acquire_write():
            resource.acquire()                       # exclusive ownership

        release_write():
            resource.release()

    Why the reader_count_lock is needed:
        Without it, two readers can both observe reader_count == 0 and
        both decide they are the "first reader" — both call
        resource.acquire(), one blocks on the other. With
        reader_count_lock, the read-modify-write of reader_count is
        atomic.

    Why the first reader (not every reader) acquires resource:
        Once any reader is inside, the resource is "occupied by
        readers" — additional readers join the existing occupation
        without re-acquiring. If every reader acquired resource,
        readers would serialize through it, defeating concurrency.

    Complexity:
        Storage: O(1) — two locks and an int.
        acquire_read / release_read: O(1) lock operations.
        acquire_write / release_write: O(1) lock operations.
        Throughput: N readers can run concurrently; writers block all.
    """

    def __init__(self):
        # `resource` is the writers' exclusive gate. A writer holds it for
        # the whole write; on the reader side the FIRST reader takes it and
        # the LAST reader frees it, so the reader cohort as a whole excludes
        # writers while individual readers come and go freely.
        self._resource = threading.Semaphore(1)
        # Protects the read-modify-write of reader_count so two readers can
        # never both believe they are the "first reader."
        self._reader_count_lock = threading.Semaphore(1)
        self._reader_count: int = 0

    def acquire_read(self) -> None:
        self._reader_count_lock.acquire()
        try:
            self._reader_count += 1
            if self._reader_count == 1:
                # First reader in locks writers out on behalf of the cohort.
                try:
                    self._resource.acquire()
                except BaseException:
                    # acquire() was interrupted before it actually took the
                    # lock — undo the bump so the next reader doesn't assume
                    # a first reader is already inside.
                    self._reader_count -= 1
                    raise
        finally:
            self._reader_count_lock.release()
        return None

    def release_read(self) -> None:
        self._reader_count_lock.acquire()
        self._reader_count -= 1
        if self._reader_count == 0:
            # Last reader out lets a waiting writer in.
            self._resource.release()
        self._reader_count_lock.release()
        return None

    def acquire_write(self) -> None:
        # Writers are deliberately simple: take the exclusive resource
        # directly, with no priority bookkeeping. That simplicity is exactly
        # why a steady stream of readers can starve writers in this tier.
        self._resource.acquire()
        return None

    def release_write(self) -> None:
        self._resource.release()
        return None
