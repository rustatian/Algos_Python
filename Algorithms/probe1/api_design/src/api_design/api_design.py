"""API Design for Long-Running Requests (submit-then-poll).

A synchronous endpoint that does slow work inline ties up a connection and
times out. The standard fix is the **submit → poll** pattern: ``POST /jobs``
commits the request to a durable queue and returns ``{job_id, PENDING}``
immediately; a worker processes it asynchronously; ``GET /jobs/{id}``
returns status and, eventually, the result. Re-submitting is made safe with
an **idempotency key** (a client ``request_id``) so a retried POST does not
create a second job.

This package ports the problem as a tiered learning ladder:

Tier 1: SyncAPI            — do the work inline and return it (the baseline/problem).
Tier 2: AsyncJobAPI        — submit→poll: enqueue, return PENDING, worker, GET status.
Tier 3: IdempotentJobAPI   — Tier 2 + request_id dedup so retried submits are safe.
Tier 4: DistributedJobAPI  — HLD only (see README); the async-jobs-at-scale design.

The actual work is an injected ``handler(payload) -> result``, and the
"worker" is an explicit ``process_*`` step, so tests are deterministic — no
background threads, no sleeping. (In production the worker is a separate
process pulling from a durable queue; here it is a method you call.)

Input:
    Tier 1: submit(payload) -> result            (blocking, inline)
    Tier 2/3: submit(payload[, request_id]) -> JobRecord   (returns PENDING now)
              get(job_id) -> JobRecord | None
              process_next() / process_all()     (the async worker step)
Output:
    A JobRecord carries {id, status, result, error}; status walks
    PENDING -> RUNNING -> SUCCESS|FAILED.

Example (submit → poll):
    api = AsyncJobAPI(handler=lambda p: p.upper())
    rec = api.submit("hello")        # rec.status == PENDING, returns immediately
    api.get(rec.id).status           # -> PENDING   (worker hasn't run yet)
    api.process_all()                # the worker processes the queue
    api.get(rec.id).result           # -> "HELLO"

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import collections
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable
from uuid import uuid4


class JobStatus(StrEnum):
    """Lifecycle of an async job, as reported by GET /jobs/{id}."""

    PENDING = "pending"  # accepted, queued, not yet started
    RUNNING = "running"  # a worker is processing it
    SUCCESS = "success"  # finished; result is available
    FAILED = "failed"  # finished with an error


@dataclass
class JobRecord:
    """The poll-able state of a job — the GET /jobs/{id} response body."""

    id: str
    status: JobStatus
    result: Any = None
    error: str | None = None


class SyncAPI:
    """Tier 1: the synchronous baseline — do the work inline and return it.

    The naive design: the request handler runs the work and returns the
    result in the same call. Simple, but it holds the connection open for
    the whole duration — a slow job blows past client/proxy timeouts and a
    crash mid-work loses everything. This is the problem the async tiers
    solve.

    Input / Output:
        submit(payload) -> result     — blocks until the work is done.

    Example:
        api = SyncAPI(handler=lambda p: p * 2)
        api.submit(21)   -> 42

    Why this does not scale to long-running work:
        The caller waits for the full job duration; load balancers and
        browsers cut connections after tens of seconds; an in-flight job is
        lost if the server restarts. Long work must be decoupled from the
        request — which is Tier 2.

    Complexity:
        submit: the cost of the handler, paid synchronously by the caller.
    """

    def __init__(self, handler: Callable[[Any], Any]) -> None:
        self._handler = handler

    def submit(self, payload: Any) -> Any:
        return self._handler(payload)  # inline — caller blocks for the result


class AsyncJobAPI:
    """Tier 2: the submit → poll pattern.

    ``submit`` accepts the request, commits it to a durable queue, and
    returns ``{job_id, PENDING}`` immediately — the connection is freed at
    once. A worker (here, an explicit ``process_*`` step) later runs the
    handler and records SUCCESS+result or FAILED+error. The client polls
    ``get(job_id)`` until the status is terminal.

    Input:
        submit(payload) -> JobRecord      — returns PENDING immediately.
        get(job_id) -> JobRecord | None   — current status/result (None if unknown).
        process_next() -> bool            — run one queued job; False if idle.
        process_all() -> None             — drain the queue.
    Output:
        JobRecord with status PENDING -> RUNNING -> SUCCESS|FAILED.

    Example:
        api = AsyncJobAPI(handler=lambda p: p.upper())
        rec = api.submit("hi")        # PENDING, instant
        api.process_all()             # worker runs
        api.get(rec.id).result        # -> "HI"

    Standard library:
        collections.deque — the durable job queue (FIFO of job ids).
        uuid.uuid4 — opaque job ids handed back to the client.
        dataclasses — the JobRecord response shape.

    Pseudocode:
        submit(payload):
            id = uuid(); jobs[id] = Record(id, PENDING); payloads[id] = payload
            queue.append(id); return jobs[id]            # ack now, work later
        process_next():
            id = queue.popleft() or return False
            rec.status = RUNNING
            try: rec.result = handler(payloads[id]); rec.status = SUCCESS
            except e: rec.error = str(e); rec.status = FAILED
        get(id): return jobs.get(id)

    Why submit returns BEFORE the work:
        Decoupling. The request latency becomes the enqueue cost (a queue
        write), not the job duration. The job survives a crash because it is
        committed to the queue, and the worker pool scales independently of
        the request tier.

    Why a job carries its error, not raises:
        The work runs in the worker, detached from the original request, so
        there is no caller to raise to. A failure is recorded as FAILED +
        error on the record; the client learns it by polling — the same way
        it would learn success.

    Complexity:
        submit/get: O(1). process_next: O(1) + the handler cost (paid by the
        worker, off the request path).
    """

    def __init__(self, handler: Callable[[Any], Any]) -> None:
        self._handler = handler
        self._jobs: dict[str, JobRecord] = {}
        self._payloads: dict[str, Any] = {}
        self._queue: collections.deque[str] = collections.deque()

    def submit(self, payload: Any) -> JobRecord:
        job_id = uuid4().hex
        self._jobs[job_id] = JobRecord(id=job_id, status=JobStatus.PENDING)
        self._payloads[job_id] = payload
        self._queue.append(job_id)  # durable queue; worker picks it up later
        return self._jobs[job_id]

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)

    def process_next(self) -> bool:
        """The worker: run one queued job. Returns False if the queue is empty."""
        if not self._queue:
            return False
        job_id = self._queue.popleft()
        record = self._jobs[job_id]
        record.status = JobStatus.RUNNING
        try:
            record.result = self._handler(self._payloads[job_id])
            record.status = JobStatus.SUCCESS
        except Exception as exc:  # the worker records failure; nobody to raise to
            record.error = str(exc)
            record.status = JobStatus.FAILED
        return True

    def process_all(self) -> None:
        while self.process_next():
            pass


class IdempotentJobAPI(AsyncJobAPI):
    """Tier 3: Tier 2 plus idempotent submit via a client request_id.

    Networks make clients retry. Without protection, a retried ``POST
    /jobs`` would enqueue the SAME work twice. An idempotency key — a
    client-chosen ``request_id`` — fixes this: the first submit creates a
    job and remembers ``request_id -> job_id``; any later submit with the
    same key returns the EXISTING job instead of creating a new one.

    Input:
        submit(payload, request_id: str) -> JobRecord
            request_id — the client's idempotency key. Re-submitting it
            returns the original job (no duplicate work).
        (get / process_next / process_all inherited from Tier 2.)
    Output:
        A repeated request_id yields the same JobRecord; the handler runs
        at most once for that key.

    Example:
        api = IdempotentJobAPI(handler=do_work)
        a = api.submit("x", request_id="r1")
        b = api.submit("x", request_id="r1")   # retry of the same request
        a.id == b.id                            # -> True (one job, not two)

    Standard library:
        dict — the idempotency map ``request_id -> job_id``.

    Pseudocode:
        submit(payload, request_id):
            if request_id in seen: return jobs[seen[request_id]]   # dedupe
            rec = super().submit(payload)
            seen[request_id] = rec.id
            return rec

    Why the key is client-chosen (not server-generated):
        The server's job_id is only known AFTER the first response — which a
        retry may never have received. The client must pick the key up front
        (a UUID per logical request) so the server can recognize a retry of
        a request whose response was lost. This is exactly how Stripe's
        ``Idempotency-Key`` header and AWS client tokens work.

    Why dedupe at submit (not at the worker):
        Catching the duplicate at the door means the work is enqueued once,
        so there is nothing to de-duplicate downstream. (At scale the
        request_id is a UNIQUE column on the jobs table, so the dedupe is
        enforced even across many API servers — see the README.)

    Complexity:
        submit: O(1) — one extra dict lookup over Tier 2.
    """

    def __init__(self, handler: Callable[[Any], Any]) -> None:
        super().__init__(handler)
        self._by_request: dict[str, str] = {}  # request_id -> job_id

    def submit(self, payload: Any, request_id: str) -> JobRecord:  # type: ignore[override]
        existing = self._by_request.get(request_id)
        if existing is not None:
            return self._jobs[existing]  # retry of a known request -> same job
        record = super().submit(payload)
        self._by_request[request_id] = record.id
        return record
