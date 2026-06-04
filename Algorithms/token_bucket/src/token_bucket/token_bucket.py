"""Token Bucket — distributed rate-limiter problem.

Design a token-bucket rate limiter. The bucket holds up to max_capacity
tokens and earns fill_rate tokens per second; callers acquire tokens to
pace rate-limited work, and either fail fast or wait when the bucket
runs dry. There is no background filler thread — tokens are computed
lazily from elapsed * fill_rate off a monotonic clock, capped at
max_capacity. This is the canonical interview pattern (Guava's
RateLimiter, Bucket4j, Resilience4j all use the same shape).

This package ports the problem as a tiered learning ladder:

Tier 1: SimpleTokenBucket      — synchronous, single-threaded, fail-fast only.
                                  Tokens are an integer count; lazy refill on each
                                  call from time.monotonic(). The Java-style
                                  baseline ("Bucket4j without the Lock").
Tier 2: ConcurrentTokenBucket  — adds threading.Lock + Condition for safe
                                  concurrent try_acquire and a blocking acquire
                                  that waits until tokens arrive. Still synchronous
                                  (threads, not coroutines). The Java-style
                                  baseline ("Bucket4j with synchronized").
Tier 4: TokenBucket            — async (asyncio) with a list of token *values*,
                                  blocking get/fill via two Conditions, and
                                  reserveN/Reservation for future claims. The
                                  rate-limiter-with-payloads variant; the full
                                  systems interview answer.

(There is no Tier 3 — the natural "blocking-via-Condition" tier is folded into
Tier 2; coroutine-based concurrency is Tier 4's whole point.)

The Tier 4 section below documents the existing class. Tier 1 and Tier 2 are
documented on their own classes.

Input:
    max_capacity : int — constructor arg; the bucket never holds more
        than this many tokens.
    fill_rate    : int — constructor arg; tokens earned per second.
    Per call: try_acquire(n) / get(n) / reserveN(n, max_wait) take an
    int count n; reserveN also takes max_wait seconds.
Output:
    try_acquire(n) -> list[int] — n tokens, or [] if n are not free now.
    reserveN(n, max_wait) -> Reservation | None — a future claim, or
        None if the wait would exceed max_wait.
    get(n) -> list[int] — n tokens, blocking until they are available.
    fill() -> None — top the bucket up with the tokens earned so far.

Every method is a coroutine: callers await it, and blocking is
cooperative (an asyncio.Lock plus two asyncio.Conditions), so a blocked
caller yields the event loop rather than freezing a thread.

Example 1:  # try_acquire — fail fast, then succeed after a refill
    bucket = TokenBucket(max_capacity=10, fill_rate=10)
    await bucket.try_acquire(3)   -> []          # empty bucket, fail fast
    # ... ~1 second passes ...
    await bucket.try_acquire(3)   -> [t1, t2, t3]
    Explanation: try_acquire never waits. On a fresh, empty bucket it
    returns []; after a second the lazy refill has earned ~10 tokens, so
    the retry succeeds. (Token values are random ints in [1, 100].)

Example 2:  # get / fill — blocking consumer and producer
    bucket = TokenBucket(max_capacity=5, fill_rate=10)
    task   = create_task(bucket.get(2))   # consumer parks: bucket empty
    await bucket.fill()                   # earns tokens, wakes the consumer
    await task                    -> [t1, t2]
    Explanation: get(2) on an empty bucket awaits on the _allocated
    condition; fill() adds the earned tokens and notifies it, so get()
    wakes, takes its 2 tokens, and returns.

Example 3:  # reserveN — claim future tokens
    bucket = TokenBucket(max_capacity=5, fill_rate=10)
    r = await bucket.reserveN(8, max_wait=2.0)   # 8 > 5 on hand -> a wait
    r.delay()                     -> ~0.3        # seconds still to wait
    await r.wait()                -> [t1, ..., t8]   # sleeps, then consumes
    Explanation: 8 tokens are not on hand, so reserveN sets them aside
    (in _promised) and returns a Reservation whose wait() sleeps out the
    shortfall's ETA before consuming. Had the ETA exceeded max_wait,
    reserveN would have returned None instead.

Constraints:
    - get(n) requires 0 < n <= max_capacity — a request that could never
      be met would otherwise block forever.
    - Token values are random integers in [1, 100]; only the *count*
      matters to the rate-limiting contract.

See README.md for the full method table.
"""

import asyncio
import collections
import random
import threading
import time


class SimpleTokenBucket:
    """Tier 1: synchronous, single-threaded, fail-fast token bucket.

    The Java-interview baseline (Guava RateLimiter / Bucket4j style with no
    Lock). Tokens are just an integer count; refill is computed on every
    call from elapsed * fill_rate off a monotonic clock, capped at
    max_capacity. No blocking, no concurrency primitives.

    Input:
        __init__(max_capacity: int, fill_rate: float)
            max_capacity — int, the most tokens the bucket can hold.
            fill_rate    — float, tokens earned per second.
        try_acquire(n: int = 1) -> bool
            Take n tokens if available; otherwise leave the bucket
            unchanged and return False. Never blocks.
    Output:
        try_acquire returns True if n tokens were taken, False otherwise.
        The bucket's token count mutates as a side effect.

    Starts FULL (tokens = max_capacity). This is the canonical choice —
    it lets the system absorb an initial burst up to max_capacity, then
    settle into the steady-state fill_rate. Guava, Bucket4j, and
    Resilience4j all default to "start full" for the same reason.

    Example 1 (initial burst, then rate-limited):
        b = SimpleTokenBucket(max_capacity=5, fill_rate=2)
        b.try_acquire(5)  -> True       # took all 5; bucket now empty
        b.try_acquire(1)  -> False      # nothing left; refill hasn't arrived
        # ... 1 second passes ...
        b.try_acquire(1)  -> True       # 2 tokens earned; took 1

    Example 2 (atomic check-and-take — no partial spend):
        b = SimpleTokenBucket(max_capacity=10, fill_rate=10)
        # bucket starts full with 10 tokens.
        b.try_acquire(20) -> False      # 20 > 10; refuses ALL — bucket still has 10
        b.try_acquire(10) -> True       # exact amount; takes all 10
        Explanation: try_acquire is all-or-nothing. A request bigger
        than the current balance returns False and leaves the bucket
        untouched (no partial fulfillment).

    Example 3 (refill cap at max_capacity):
        b = SimpleTokenBucket(max_capacity=5, fill_rate=100)
        b.try_acquire(5)  -> True       # bucket empty
        # ... 1 second passes (would earn 100 tokens) ...
        b.try_acquire(6)  -> False      # earned only min(100, 5) = 5; 6 > 5
        b.try_acquire(5)  -> True       # takes the 5 (the cap)

    Standard library:
        time.monotonic() — a clock that only moves forward, immune to
            wall-clock adjustments. The token-earning formula is a delta
            of it, so an NTP step neither grants nor revokes tokens.

    Pseudocode:
        data:
            tokens          — int, current token count (starts at max_capacity).
            max_capacity    — int.
            fill_rate       — float, tokens per second.
            last_refill     — float, monotonic timestamp of the last refill.

        __init__(max_capacity, fill_rate):
            tokens       = max_capacity         # start full
            last_refill  = monotonic()

        refill():
            now      = monotonic()
            elapsed  = now - last_refill
            earned   = int(elapsed * fill_rate)
            tokens   = min(max_capacity, tokens + earned)
            # Credit only what landed — preserve sub-second residual time
            # so a 0.4-sec-then-0.4-sec sequence still earns 1 token at
            # rate=2 (not 0+0 because of truncation each call).
            last_refill += earned / fill_rate

        try_acquire(n):
            refill()
            if tokens < n:
                return False                    # atomic — no partial spend
            tokens -= n
            return True

    Why "credit only what landed" matters:
        Naively setting last_refill = now after each refill drops the
        fractional time. At fill_rate=2 and two calls 0.4 sec apart,
        each refill earns int(0.8) = 0 tokens — the user never earns
        anything even after a full second. Crediting only int(elapsed *
        rate) / rate seconds back keeps the un-earned fractional time
        carried forward.

    Constraints (per CLAUDE.local.md — no input validation):
        max_capacity > 0 and fill_rate > 0 assumed.
        try_acquire is called with n >= 1.

    Complexity:
        Storage: O(1) — a single int and a float.
        try_acquire: O(1) — one clock read, one int add, one comparison.

    What this tier does NOT do (left to higher tiers):
        - Concurrency: two threads calling try_acquire concurrently can
          race on the read-modify-write of tokens. Tier 2 adds a Lock.
        - Blocking: there is no acquire() that waits. Callers retry
          themselves. Tier 2 adds a blocking acquire via Condition.wait().
        - Token payloads: tokens are an integer count, not a list of
          values. The rate-limiter-with-payloads variant is Tier 4.
    """

    def __init__(self, max_capacity: int, fill_rate: float) -> None:
        self._max_capacity = max_capacity
        self._fill_rate = fill_rate
        # Start full: the bucket can absorb an initial burst of up to
        # max_capacity before it has to throttle down to fill_rate.
        self._tokens = max_capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        """Credit the tokens earned since the last refill, capped at capacity.

        Tokens are earned lazily — there is no background thread. Each call
        looks at how much time has passed and converts it to whole tokens.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        earned = int(elapsed * self._fill_rate)
        if earned > 0:
            self._tokens = min(self._max_capacity, self._tokens + earned)
            # Advance last_refill by ONLY the time we actually credited, not
            # all the way to `now`. This carries the sub-token fractional
            # time forward so it is not silently truncated away on each call.
            # (Also sidesteps a divide-by-zero when fill_rate == 0: earned is
            # then 0, so we never reach this branch.)
            self._last_refill += earned / self._fill_rate

    def try_acquire(self, n: int = 1) -> bool:
        self._refill()
        # All-or-nothing: a request larger than the balance takes nothing.
        if self._tokens < n:
            return False
        self._tokens -= n
        return True


class ConcurrentTokenBucket:
    """Tier 2: thread-safe synchronous bucket; blocking acquire via Condition.

    Same algorithm as Tier 1 (lazy refill, integer token count, atomic
    check-and-take), plus two things Tier 1 cannot do safely:

      1. Multiple threads calling try_acquire concurrently — a Lock
         serializes the read-modify-write of tokens so no overspend.
      2. A blocking acquire(n) that sleeps until enough tokens are
         earned, woken by the next operation's refill via a Condition.

    Still synchronous (threading, not asyncio). This is the Java-interview
    baseline with synchronized / ReentrantLock — Bucket4j's standard
    single-machine mode.

    Input:
        __init__(max_capacity: int, fill_rate: float)
        try_acquire(n: int = 1) -> bool
            Take n tokens if available; never blocks. Thread-safe.
        acquire(n: int = 1) -> None
            Take n tokens, blocking the calling thread until they are
            available. Thread-safe.
    Output:
        try_acquire returns True if n tokens were taken, False otherwise.
        acquire returns once n tokens have been deducted from the bucket.

    Example 1 (concurrent try_acquire — no overspend):
        b = ConcurrentTokenBucket(max_capacity=100, fill_rate=0)
        # 200 threads each try_acquire(1) on a 100-token bucket.
        # Exactly 100 succeed; the other 100 see False.
        # A missing Lock would let races overspend.

    Example 2 (blocking acquire — waits for refill):
        b = ConcurrentTokenBucket(max_capacity=10, fill_rate=10)
        b.try_acquire(10)              # drain
        # Thread T: b.acquire(5)        # blocks on Condition.wait()
        # ... 0.5 seconds pass ...
        # Refill at the next operation earns ~5 tokens; T wakes, returns.

    Example 3 (acquire awakened by another try_acquire's refill):
        b = ConcurrentTokenBucket(max_capacity=10, fill_rate=10)
        b.try_acquire(10)              # bucket empty
        # Thread A: b.acquire(3)       # blocks
        # ... 0.5 sec ...
        # Thread B: b.try_acquire(1)   # triggers refill — bucket now has 5
        # The refill notifies the Condition; Thread A wakes, takes 3,
        # returns. Thread B's call then sees the remaining 2 (or whatever
        # is left after A took 3) and either succeeds or returns False.

    Standard library:
        threading.Lock — non-reentrant mutex. Acquired with ``with``;
            held during the read-modify-write of tokens so all check-
            and-take operations are atomic.
        threading.Condition — Lock + wait/notify queue. acquire() calls
            cond.wait() to release the lock and sleep; try_acquire and
            acquire both call cond.notify_all() after a refill so any
            blocked acquire that now has enough tokens can resume.
        time.monotonic() — same role as Tier 1.

    Pseudocode:
        data:
            tokens          — int, starts at max_capacity.
            max_capacity    — int.
            fill_rate       — float.
            last_refill     — float, monotonic timestamp.
            lock            — threading.Lock.
            not_empty       — threading.Condition(lock).

        refill():               # called with the lock held
            now      = monotonic()
            elapsed  = now - last_refill
            earned   = int(elapsed * fill_rate)
            tokens   = min(max_capacity, tokens + earned)
            last_refill += earned / fill_rate

        try_acquire(n):
            with lock:
                refill()
                if tokens < n:
                    return False
                tokens -= n
                not_empty.notify_all()      # an acquire waiting on a smaller
                                            # n might now succeed too
                return True

        acquire(n):
            with not_empty:                 # acquires the lock
                while True:
                    refill()
                    if tokens >= n:
                        tokens -= n
                        not_empty.notify_all()    # see comment in try_acquire
                        return
                    # Not enough; compute how long until we *might* have n.
                    shortfall = n - tokens
                    delay     = shortfall / fill_rate
                    # Wait at most `delay` seconds — the lock is released
                    # while waiting and re-acquired on wake.
                    not_empty.wait(timeout=delay)
                    # Loop: re-check after wake. wait() can return early
                    # (spurious wakeup, or another operation notified).

    Why the inner ``while True`` loop in acquire():
        - Spurious wakeups: Condition.wait() can return without a notify
          and without timeout elapsing (per the threading docs).
        - Multiple waiters: notify_all wakes everyone; only one may have
          enough tokens for its specific n. The others must re-check.
        - Re-check on every wake; the loop costs nothing if the wait was
          legitimate.

    Why the bounded wait timeout:
        Without a timeout, acquire() depends entirely on someone else
        calling notify (try_acquire after a refill). If no one else
        touches the bucket, the waiter never wakes up — even after the
        bucket has refilled passively. A bounded timeout makes the
        waiter self-poke after the expected refill time and re-check.
        The shortfall/fill_rate estimate is the *minimum* time the
        waiter could possibly succeed, so it never wakes too eagerly.

    Why notify_all in try_acquire:
        Many threads can be blocked in acquire(), each waiting for a
        different n. After a refill (or a deduction by another caller
        — which is part of refill's bookkeeping), some of them may now
        be satisfied; others not. notify_all wakes all of them so each
        can re-check; the loop filters out those still short.

    Complexity:
        Storage: O(1) — same as Tier 1 plus two locks.
        try_acquire: O(1) — one lock-acquire, refill, compare, mutate,
            notify, release.
        acquire: O(1) per loop iteration; total wait time bounded by
            shortfall / fill_rate per iteration.
        Contention: every operation serializes through the one lock.
            For very high QPS this becomes the bottleneck — Tier 4's
            cooperative async model avoids the kernel-context-switch
            cost of thread-blocking but trades it for an event loop.

    What this tier does NOT do (left to Tier 4):
        - Token payloads (tokens here are a count, not a list of values).
        - Future reservations (reserveN / Reservation).
        - Async / cooperative concurrency (this uses thread-blocking).
    """

    def __init__(self, max_capacity: int, fill_rate: float) -> None:
        self._max_capacity = max_capacity
        self._fill_rate = fill_rate
        self._tokens = max_capacity
        self._last_refill = time.monotonic()
        # One lock guards the whole read-modify-write of `tokens`. The
        # Condition shares that lock; blocked acquire() callers wait on it
        # and every successful operation notifies it so waiters re-check.
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)

    def _refill(self) -> None:
        """Identical math to Tier 1; always called with the lock held."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        earned = int(elapsed * self._fill_rate)
        if earned > 0:
            self._tokens = min(self._max_capacity, self._tokens + earned)
            self._last_refill += earned / self._fill_rate

    def try_acquire(self, n: int = 1) -> bool:
        with self._lock:
            self._refill()
            if self._tokens < n:
                return False
            self._tokens -= n
            # A waiter blocked on a smaller n might now be satisfiable.
            self._not_empty.notify_all()
            return True

    def acquire(self, n: int = 1) -> None:
        with self._not_empty:  # acquires self._lock
            while True:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    self._not_empty.notify_all()
                    return
                # Not enough yet. Estimate the soonest we *could* have n and
                # sleep at most that long — the lock is released while we
                # wait and re-acquired on wake. The bounded timeout lets us
                # self-poke even if nobody else touches the bucket (the
                # bucket refills passively from the clock, with no notify).
                shortfall = n - self._tokens
                timeout = (
                    shortfall / self._fill_rate if self._fill_rate > 0 else None
                )
                self._not_empty.wait(timeout=timeout)
                # Loop and re-check: wait() can return on a notify, on the
                # timeout, or spuriously — only the token check is authoritative.


class Reservation:
    """A future claim on tokens, handed back by TokenBucket.reserveN.

    Input:
        Not constructed directly — obtained from TokenBucket.reserveN(n,
        max_wait), which fixes the reserved count n and the ready time.
        wait(), cancel(), and delay() all take no arguments.
    Output:
        wait()   -> list[int] | None — the n reserved tokens once due, or
            None if the reservation was already consumed or cancelled.
        cancel() -> None — drops the claim, returning the tokens.
        delay()  -> float — seconds still to wait before the tokens are due.

    Example:
        r = await bucket.reserveN(8, max_wait=2.0)   # 8 tokens claimed
        r.delay()             -> ~0.3          # not due yet
        await r.wait()        -> [t1, ..., t8]   # sleeps the ETA, consumes
        await r.cancel()      -> None          # no-op: already consumed
        Explanation: wait() and cancel() are one-shot and mutually
        exclusive — whichever runs first wins, and the later call is a
        no-op. Here wait() consumed the claim, so the trailing cancel()
        does nothing.

    reserveN sets tokens aside by adding them to the bucket's _promised
    count — tokens that exist (or soon will, once refilled) but are spoken
    for, so no other caller is handed them. The Reservation is the handle
    that later collects or drops that claim.

      - wait()   sleeps until the tokens are due, then consumes them.
      - cancel() abandons the claim, releasing the tokens.
      - delay()  reports the seconds still to wait.

    wait() and cancel() are one-shot and mutually exclusive: the first to
    run sets a flag (_consumed or _cancelled) and every later call is a
    no-op, so a reservation is never both spent and refunded.

    Standard library:
        asyncio.sleep(seconds) — suspends just this coroutine for the
            delay without blocking the event loop; wait() uses it to sleep
            out the reservation's ETA so other coroutines keep running.
        asyncio.Condition — reached through the bucket: if the tokens are
            not yet in the bucket, wait() awaits the bucket's _allocated
            condition until a fill() supplies them. (See TokenBucket for
            the shared Lock and the two Conditions.)

    Pseudocode:
        wait():
            if consumed or cancelled: return None
            await sleep(delay())                   # wait out the ETA
            if consumed or cancelled: return None  # cancelled mid-sleep?
            async with bucket.lock:
                bucket.lazy_refill()
                while bucket has fewer than `reserved` tokens:
                    await bucket._allocated.wait()  # wait for a fill()
                tokens = pop `reserved` tokens from the bucket
                bucket._promised -= reserved        # claim now settled
                notify bucket._do_allocate          # space freed for fill()
                mark consumed
            return tokens

        cancel():
            if consumed or cancelled: return None
            async with bucket.lock:
                bucket._promised -= reserved        # un-promise the tokens
                notify bucket._allocated            # they are free again
            mark cancelled

    Why _promised: reserved tokens are counted but not yet removed, so
    every "what is actually free?" test subtracts _promised — that is what
    stops a reserved token from being handed to another caller.
    """

    def __init__(
        self, tb: TokenBucket, ready_at: float | int, delay: float | int, reserved: int
    ) -> None:
        self._tb = tb
        self._ready_at = ready_at
        self._delay = delay
        self._reserved = reserved
        self._cancelled: bool = False
        self._consumed: bool = False

    def delay(self) -> float:
        return max(0, self._ready_at - time.monotonic())

    async def wait(self) -> list[int] | None:
        if self._cancelled or self._consumed:
            return None

        await asyncio.sleep(self.delay())

        if self._cancelled or self._consumed:
            return None

        async with self._tb._lock:
            await self._tb._lazy_refill()
            res = []

            while len(self._tb._bucket) < self._reserved:
                await self._tb._allocated.wait()

            for _ in range(self._reserved):
                res.append(self._tb._bucket.popleft())

            self._tb._promised -= self._reserved
            self._tb._do_allocate.notify_all()
            self._consumed = True

        return res

    async def cancel(self) -> None:
        if self._cancelled or self._consumed:
            return None
        async with self._tb._lock:
            self._tb._promised -= self._reserved
            self._tb._allocated.notify_all()
        self._cancelled = True


class TokenBucket:
    """A token-bucket rate limiter built on asyncio.

    Input:
        max_capacity : int — constructor arg; max tokens the bucket holds.
        fill_rate    : int — constructor arg; tokens earned per second.
        try_acquire(n) / get(n) / reserveN(n, max_wait) take an int count
        n; reserveN also takes max_wait seconds. fill() takes nothing.
    Output:
        try_acquire(n) -> list[int] — n tokens, or [] if n aren't free now.
        reserveN(n, max_wait) -> Reservation | None — a future claim, or
            None if the wait would exceed max_wait.
        get(n) -> list[int] — n tokens, blocking until available.
        fill() -> None — adds the tokens earned since the last refill.

    Example 1:  # try_acquire — fail fast, then succeed after a refill
        bucket = TokenBucket(max_capacity=10, fill_rate=10)
        await bucket.try_acquire(3)   -> []          # empty, fail fast
        # ... ~1 second passes ...
        await bucket.try_acquire(3)   -> [t1, t2, t3]
        Explanation: try_acquire never waits. On a fresh, empty bucket it
        returns []; after a second the lazy refill has earned ~10 tokens,
        so the retry succeeds. (Tokens are random ints in [1, 100].)

    Example 2:  # get / fill — blocking consumer and producer
        bucket = TokenBucket(max_capacity=5, fill_rate=10)
        task   = create_task(bucket.get(2))   # consumer parks: empty
        await bucket.fill()                   # earns tokens, wakes it
        await task                    -> [t1, t2]
        Explanation: get(2) on an empty bucket awaits the _allocated
        condition; fill() adds earned tokens and notifies it, so get()
        wakes, takes its 2 tokens, and returns.

    Example 3:  # reserveN — claim future tokens
        bucket = TokenBucket(max_capacity=5, fill_rate=10)
        r = await bucket.reserveN(8, max_wait=2.0)   # 8 > 5 -> a wait
        r.delay()                     -> ~0.3        # seconds to wait
        await r.wait()                -> [t1, ..., t8]   # sleeps, consumes
        Explanation: 8 tokens are not on hand, so reserveN sets them
        aside (in _promised) and returns a Reservation whose wait()
        sleeps out the ETA before consuming. Had the ETA exceeded
        max_wait, reserveN would have returned None.

    The bucket holds up to max_capacity tokens and earns fill_rate tokens
    per second. There is no background filler task: tokens are computed
    lazily from elapsed * fill_rate off a monotonic clock and capped at
    max_capacity, so the bucket is immune to wall-clock jumps.

    It offers two coordination styles over the same bucket:

      Self-refilling, non-blocking — try_acquire / reserveN:
        each refills lazily itself, then either takes tokens now or
        reports how long the caller would have to wait.

      Producer / consumer, blocking — fill / get:
        coordinated by two Conditions over one lock — _do_allocate (a
        producer sleeps here while the bucket is full) and _allocated (a
        consumer sleeps here while it is empty).

    Reserved tokens live in _promised: counted, but not yet removed. Every
    "what is free?" test subtracts _promised, so a token a Reservation has
    claimed is never handed out twice — no double-spending.

    Standard library:
        time.monotonic() — a clock that only moves forward, immune to
            wall-clock adjustments; token earnings are deltas of it, so an
            NTP step can neither grant nor revoke tokens.
        collections.deque — holds the token objects; append() adds one,
            popleft() removes the oldest, both O(1).
        asyncio.Lock — an async mutex; `async with lock` gives one
            coroutine exclusive access to the bucket, and awaiting a busy
            lock yields the event loop rather than blocking a thread.
        asyncio.Condition — a Lock plus a wait/notify queue: a coroutine
            calls await cond.wait() to drop the lock and sleep until
            another calls cond.notify_all(). Two are used so a fill()
            wakes only consumers and a get() wakes only producers.
        asyncio.sleep — used by a Reservation to wait out its ETA.

    Pseudocode:
        lazy_refill():                       # earn tokens from the clock
            elapsed   = now - last_refill
            add_count = min(free_space, elapsed * fill_rate)
            append add_count tokens to the bucket
            last_refill += add_count / fill_rate   # credit only what landed

        try_acquire(n):                      # fail-fast, non-blocking
            async with lock:
                lazy_refill()
                if (len(bucket) - promised) < n:
                    return []                # not enough free -> take none
                return pop n tokens

        reserveN(n, max_wait):               # claim future tokens
            async with lock:
                lazy_refill()
                shortfall = n - (len(bucket) - promised)
                delay = shortfall / fill_rate if shortfall > 0 else 0
                if delay > max_wait:
                    return None              # would wait too long
                promised += n                # set the claim aside
                return Reservation(ready_at = now + delay, reserved = n)

        fill():                              # producer
            async with do_allocate:
                while bucket is full:
                    await do_allocate.wait()
                add the tokens earned since last_refill
                notify _allocated            # wake a waiting consumer

        get(n):                              # consumer, blocks until n taken
            async with allocated:
                while fewer than n collected:
                    while (len(bucket) - promised) == 0:
                        await allocated.wait()      # wait for a fill()
                    take as many as are free, up to the remaining need
                    notify _do_allocate      # wake a waiting producer

    Why two Conditions, not one: a producer blocked on "bucket full" and a
    consumer blocked on "bucket empty" wait for opposite events. Separate
    Conditions mean fill() wakes only consumers and get() wakes only
    producers — no waking the wrong waiters. Both share the one lock, so
    the bucket is never inspected by two coroutines at once.
    """

    def __init__(self, max_capacity: int, fill_rate: int) -> None:
        self._max_capacity = max_capacity
        self._fill_rate = fill_rate

        self._last_refill = time.monotonic()
        self._bucket: collections.deque[int] = collections.deque()

        # reserved tokens
        self._promised = 0

        self._lock = asyncio.Lock()
        self._do_allocate = asyncio.Condition(self._lock)
        self._allocated = asyncio.Condition(self._lock)

    async def _lazy_refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        total = elapsed * self._fill_rate
        free_space = self._max_capacity - len(self._bucket)
        add_count = int(min(free_space, total))

        for _ in range(add_count):
            self._bucket.append(random.randint(1, 100))

        if add_count > 0:
            self._last_refill = self._last_refill + (add_count / self._fill_rate)

    async def try_acquire(self, n: int) -> list[int]:
        async with self._lock:
            await self._lazy_refill()
            if len(self._bucket) - self._promised < n:
                return []

            res = []
            for _ in range(n):
                res.append(self._bucket.popleft())

        self._do_allocate.notify_all()
        return res

    async def reserveN(self, n: int, max_wait: float) -> Reservation | None:
        async with self._lock:
            # may be negative
            await self._lazy_refill()
            effective = len(self._bucket) - self._promised
            new_eff = effective - n

            if new_eff >= 0:
                delay = 0
                ready_at = time.monotonic()
            else:
                delay = abs(new_eff) / self._fill_rate
                ready_at = time.monotonic() + delay

            if delay > max_wait:
                return None

            self._promised += n

        return Reservation(tb=self, ready_at=ready_at, delay=delay, reserved=n)

    async def fill(self) -> None:
        async with self._do_allocate:
            while len(self._bucket) == self._max_capacity:
                await self._do_allocate.wait()

            elapsed = time.monotonic() - self._last_refill
            total_tok = elapsed * self._fill_rate
            tokens = int(min(total_tok, self._max_capacity - len(self._bucket)))

            for _ in range(tokens):
                self._bucket.append(random.randint(1, 100))

            self._last_refill += tokens / self._fill_rate
            self._allocated.notify_all()
        return None

    async def get(self, n: int) -> list[int]:
        if n <= 0:
            raise ValueError("n should be > 0")
        if n > self._max_capacity:
            raise ValueError("should not exceed max_capacity")

        async with self._allocated:
            res = []
            while len(res) < n:
                while len(self._bucket) - self._promised == 0:
                    await self._allocated.wait()

                to_take = min(n - len(res), len(self._bucket) - self._promised)
                for _ in range(to_take):
                    res.append(self._bucket.popleft())

                self._do_allocate.notify_all()
        return res
