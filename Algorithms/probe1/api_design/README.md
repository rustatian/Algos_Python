# API Design for Long-Running Requests

A synchronous endpoint that does slow work inline holds the connection open
and times out. The standard fix is **submit → poll**: `POST /jobs` commits
the request to a durable queue and returns `{job_id, PENDING}` immediately;
a worker processes it asynchronously; `GET /jobs/{id}` returns status and,
eventually, the result. A client-supplied **idempotency key** makes retries
safe.

Modeled on the classic "API design for long-running requests" question.
Related LeetCode references: #1396 (Underground System — async state per
entity), #1797 (Authentication Manager — TTL/expiry), #1656 (Design an
Ordered Stream), #2102 (Sequentially Ordinal Rank Tracker).

## Problem

Some requests are slow (transcode a video, generate a report). Doing the
work inside the request means the caller waits the whole time — and
browsers, load balancers, and proxies cut connections after tens of
seconds, while a crash mid-work loses everything. The async contract
decouples acceptance from completion:

```python
api = AsyncJobAPI(handler=transcode)
rec = api.submit(video)        # POST /jobs -> {job_id, PENDING}, returns NOW
...                            # a worker processes it off the request path
api.get(rec.id).status         # GET /jobs/{id} -> PENDING | RUNNING | SUCCESS | FAILED
```

Here the work is an injected `handler` and the worker is an explicit
`process_*` step, so the lifecycle is observable and deterministic — no
threads, no sleeping.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SyncAPI` | run the work inline | the baseline/problem — the caller blocks; long work times out |
| 2 | `AsyncJobAPI` | submit → poll over a durable queue | decoupling — accept now, work later, poll for status/result |
| 3 | `IdempotentJobAPI` | Tier 2 + `request_id` dedup | safe retries — a re-sent POST must not create a second job |
| 4 | `DistributedJobAPI` | API tier + queue + worker fleet + DB | the system-design follow-up — the async-jobs-at-scale architecture |

Each tier answers the previous one's weak spot. Tier 1 returns the result
inline — simple, but the request latency *is* the job duration, so anything
slow breaks. Tier 2 splits accept from execute: `submit` commits the job and
returns `PENDING` instantly, a worker runs the handler and records
`SUCCESS`+result or `FAILED`+error, and the client polls `get`. A failure is
*recorded on the job*, not raised, because the work runs detached from the
original request — there is no caller to raise to. Tier 3 closes the retry
hole: networks make clients resend, so a client-chosen `request_id` is
remembered (`request_id -> job_id`) and a repeat returns the *existing* job,
running the handler at most once. Tier 4 distributes it.

### Why the idempotency key is chosen by the client

The server's `job_id` is only known *after* the first response — which a
retry may never have received (that is *why* the client is retrying). So the
client must pick the key up front (a UUID per logical request) for the
server to recognize a retry of a request whose response was lost. This is
exactly Stripe's `Idempotency-Key` header and AWS's client-request tokens.

### Long-poll — the same contract, a different wait

A pure poll loop wastes round-trips. The **long-poll** variant has
`GET /jobs/{id}` *hold the connection* for up to N seconds, returning early
the moment the job finishes (or at the timeout, to be retried). It is a
transport optimization over the identical submit/poll semantics — the job
model does not change, only how the client waits. (Webhooks/Server-Sent
Events are the push alternative; see Tier 4.)

## Tier 4 — the system-design follow-up (async jobs at scale)

The follow-up: *run this as a service handling many slow jobs across an
autoscaled worker fleet.* This is the canonical async-jobs architecture —
the same recursive-self-spawning, queue-plus-database pattern as this
repo's `producer_consumer_service`, `web_crawler`, and `file_duplicates`
Tier 4s, viewed from the API contract.

```
   client ─► API tier (stateless) ─► jobs table (PENDING) ─► durable queue
                  │                        ▲                      │
            GET /jobs/{id}  ◄── status/result                workers claim,
            (poll or long-poll)                              run, mark SUCCESS/FAILED
```

**Opener questions.** Job duration and volume? Result size — inline in the
status, or a URL to an object store? Delivery — at-least-once (idempotent
workers) or exactly-once? Push (webhook/SSE) or poll? Result retention/TTL?
Priorities and fairness across tenants?

**Design sketch.**

- **Stateless API tier.** `POST /jobs` writes a job row (`PENDING`) and
  enqueues it, then returns `{job_id}`. `GET /jobs/{id}` reads the row.
  Horizontally scalable because all state is in the DB/queue.
- **Idempotency at the database.** The client `request_id` is a UNIQUE
  column on the jobs table, so the dedupe holds even across many API
  servers handling concurrent retries — a duplicate insert is rejected and
  the existing row is returned. This is Tier 3's map, made durable and
  distributed.
- **Durable queue + worker fleet.** Workers claim jobs (`SELECT ... FOR
  UPDATE SKIP LOCKED` or a managed queue), run them, and write the result.
  This is `producer_consumer_service` Tier 3 — leases requeue dead workers,
  retries are bounded, poison jobs go to a dead-letter queue.
- **Results.** Small results live on the job row; large ones (a rendered
  file) go to an object store and the row carries a URL. Results have a TTL.
- **Notification.** Poll, long-poll (hold the GET), or push via a webhook /
  SSE when the job finishes — all over the same job model.

**Failures.** Lost POST response → client retries with the same
`request_id` → same job (no duplicate). Worker dies mid-job → lease expires →
requeued; idempotent workers absorb the partial run. Client polls a job that
expired → `404`/`GONE` after the result TTL. Queue overload → backpressure +
autoscale workers on queue depth.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `AsyncJobAPI.submit` | `POST /jobs` on a stateless API tier |
| `_jobs` dict | a jobs table (status, result, request_id UNIQUE) |
| `_queue` deque | a durable queue (SQS / Redis Streams / a DB-backed queue) |
| `process_*` worker step | an autoscaled, idempotent worker fleet |
| `request_id` map | a UNIQUE idempotency-key column enforced across API servers |
| `get` polling | poll / long-poll / webhook notification |

## Running the tests

```sh
uv run pytest Algorithms/api_design/tests/ -q
```
