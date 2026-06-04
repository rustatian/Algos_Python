# Producer-Consumer / Image-Processing Service

A producer submits work; consumers (workers) process it asynchronously.
Two designs on a ladder: the in-memory **bounded blocking queue** (the
concurrency primitive), and the **DB-backed queue** (the production
pattern) — the producer commits a job to durable storage and returns
immediately, a worker atomically claims it (`SELECT ... FOR UPDATE SKIP
LOCKED`), processes it, and marks it done; a monitor requeues jobs stuck
behind dead workers.

Modeled on the classic "image-processing service / DB-backed queue"
question. Related LeetCode references: #1188 (Bounded Blocking Queue), #1117
(Building H2O — producer/consumer), #635 (Design Log Storage — DB-backed),
#1114/#1115/#1116 (concurrency basics).

## Problem

A request submits a unit of work (resize this image, transcode this video).
The naive synchronous design does the work in the request and returns when
done — slow, and it loses the job if the server crashes mid-work. The
async design **commits the job, returns 200 immediately**, and lets a pool
of workers process it later. The hard parts: claiming each job exactly once
across many workers, and recovering jobs whose worker died.

```python
q = DBBackedQueue()
jid = q.submit("resize:img1")   # producer: durable + acked NOW (not after work)
job = q.claim()                 # a worker atomically takes it -> PROCESSING
q.complete(job.id, "thumb1")
q.status(jid)                   # -> SUCCESS
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `BoundedBlockingQueue` | two Conditions over one lock | the primitive — block on full/empty; FIFO (#1188) |
| 2 | `DBBackedQueue` | durable rows + atomic claim | the production pattern — commit-then-ack, claim once (SKIP LOCKED) |
| 3 | `LeasedQueue` | Tier 2 + leases + bounded retries | resilience — recover dead workers, cap poison jobs |
| 4 | `DistributedTaskQueue` | broker + worker fleet | the system-design follow-up — Celery/SQS at scale |

Each tier answers the previous one's weak spot. Tier 1 is the in-memory
queue: correct and fast, but it lives in one process — a crash loses every
queued job, and it cannot span machines. Tier 2 makes the queue **durable**:
jobs are rows committed before the work, so the producer's request latency
is just a DB write and the job survives a crash; workers claim atomically so
no job is processed twice. Its remaining gap: a worker that dies after
claiming leaves its row stuck in PROCESSING forever. Tier 3 adds a **lease** —
a claimed job past its timeout is presumed dead and requeued by
`sweep_stuck()` — plus **bounded retries** so a job that always fails is
eventually parked in FAILED instead of looping. Tier 4 distributes it.

### Why the producer commits before doing the work

Returning 200 on commit (not on completion) buys durability and decoupling:
the job is safe the moment it is written, the client is freed immediately,
and the (possibly slow) processing happens asynchronously on a worker. The
request's latency is the DB write, not the work.

### Why claiming must be atomic (`SKIP LOCKED`)

Many workers poll the same table. If two read the same PENDING row, both
process it — duplicated, possibly side-effecting work. Claiming under a row
lock that *skips* rows other workers hold gives each job to exactly one
worker without the pollers blocking each other. In this port, the in-memory
lock plays that role.

### Why a lease beats a heartbeat for liveness

A lease needs no liveness traffic — the monitor only compares `claimed_at +
timeout` against now. A crash, a hang, and a network partition all look
identical: the lease expires and the job is reclaimed. (Long jobs can
extend their lease via heartbeats; the timeout is the backstop.)

## Tier 4 — the system-design follow-up (distributed task queue)

The follow-up: *process millions of jobs/day across an autoscaled worker
fleet — the shape of Celery, Sidekiq, SQS+workers, or a media-processing
pipeline.*

**Opener questions.** Throughput and job duration (ms thumbnails vs minutes
of transcoding)? Ordering needed, or independent jobs? Delivery semantics —
at-least-once (idempotent workers) or exactly-once (much harder)? Priorities?
Result delivery — poll, callback, or push? Retry/backoff policy and a
dead-letter queue?

**Design sketch.**

```
   producer ─► API ─► jobs table (PENDING)  ◄── workers claim (SKIP LOCKED)
                          │                         │  process
                          │                         ▼
                    status/result  ◄── mark SUCCESS/FAILED; emit result
                          ▲
                   monitor requeues PROCESSING rows past their lease
```

- **Durable broker.** A jobs table (Postgres `FOR UPDATE SKIP LOCKED`) or a
  managed queue (SQS, Redis Streams, RabbitMQ). The table form is Tier 2/3
  verbatim, just at scale and indexed on `(status, claimed_at)`.
- **Worker fleet**, autoscaled on queue depth. Each worker is Tier 3's
  claim → process → complete/fail loop. **At-least-once delivery** means a
  job can run more than once (worker died after the side effect but before
  the ack), so **workers must be idempotent** — key the side effect on
  `job_id` so a re-run is a no-op.
- **The monitor** is Tier 3's `sweep_stuck`, run as a periodic job:
  requeue expired leases, move past-max-attempts jobs to a **dead-letter
  queue** for inspection.
- **Backpressure & priority.** Bound the queue / shed load when depth
  explodes; separate high/low-priority queues so a flood of cheap jobs
  cannot starve urgent ones.

**Failures.** Worker dies mid-job → lease expires → requeued (idempotency
absorbs partial work). Poison job → retry cap → dead-letter queue. Broker
overload → backpressure + autoscale workers. Duplicate submit (client
retry) → an idempotency key on `submit` dedupes to one job row.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `DBBackedQueue` dict of rows | a jobs table / managed queue (SQS, Redis Streams) |
| `threading.Lock` on claim | `SELECT ... FOR UPDATE SKIP LOCKED` row locking |
| `LeasedQueue.sweep_stuck` | a periodic monitor requeuing expired leases |
| `max_attempts` cap | retry policy + dead-letter queue |
| single process | autoscaled, idempotent worker fleet behind a durable broker |

## Running the tests

```sh
uv run pytest Algorithms/producer_consumer_service/tests/ -q
```
