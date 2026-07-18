# Find Duplicate Files

Given a set of files, return every group of files whose contents are
**byte-for-byte identical**. A group is two or more files that share the
same content; a file whose content is unique belongs to no group.

Modeled on LeetCode #609 (Find Duplicate File in System) and the classic
file-dedup interview question. A learning exercise: four tiers, the same
result, an escalating answer to "how do you find duplicates *at scale*?"

## Problem

A **duplicate group** is a set of file paths that all hold identical
content. Return every group of size ≥ 2; group order and within-group
order do not matter, and files with unique content are omitted.

Tiers 1–2 take LeetCode #609's input — a list of directory strings:

```
"root/d1/d2 f1.txt(content_a) f2.txt(content_b) ..."
```

Each string describes one directory: the first token is its path, every
later token is `name(content)`. A file's path is `directory/name`. Tiers
3–4 drop the toy format and walk a real directory tree on disk.

It is a **bucket-by-key** problem: files with the same content-key fall
into the same bucket, and buckets of size ≥ 2 are the duplicate groups.
Every tier keeps that shape — what changes is *what the key is* and *how
much work it takes to compute*.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `DuplicateFinder` | the raw content is the key | bucket-by-key — group paths in a `dict[content, paths]` |
| 2 | `HashFinder` | a SHA-256 digest is the key | the key need not *be* the data — a 32-byte digest collapses any-size content |
| 3 | `FunnelFinder` | size → prefix-hash → full-hash funnel | cheapest discriminator first — most files are never fully read |
| 4 | `DistributedFinder` | recursive async scan jobs + a database index | the directory walk becomes self-spawning jobs over a worker fleet |

All four expose the same entry point:

```python
finder.find(...)  # -> list[list[str]]
```

Tiers 1–2 take the `list[str]` of #609 directory strings; tiers 3–4 take
a filesystem path. Every tier returns the same duplicate groups.

Each tier answers the previous one's weak spot. Tier 1 holds every file's
full content in memory as a dict key — fine for #609 strings, ruinous for
real files. Tier 2 keys on a fixed-size hash instead, but still reads
every byte of every file. Tier 3 adds a funnel: bucket by `size` first (a
metadata read — no file I/O at all), then by a hash of the first few KB,
and full-hash only the survivors — so unique files and near-misses are
culled cheaply. Tier 4 leaves the single machine: an API enqueues a scan
job, each job subdivides its directory into child scan jobs, and a
database — not an in-process dict — accumulates the content index. It
ships as both a runnable single-process simulation and an architecture
write-up.

The hard part of Tiers 3–4 is not the hashing — it is **not reading what
you do not have to**. A size bucket of one is a proven-unique file, at a
cost of zero bytes read.

## Tier 4 architecture — the system-design follow-up

`DistributedFinder` ships as a single-process **simulation** of a
distributed file-dedup service. The class docstring covers the in-process
plumbing; this section is the high-level design write-up — what you'd
whiteboard if the interviewer says "now scale this across servers."

### Opener — the clarifying questions (before drawing anything)

Asking these scores points: it shows you scope before you architect.

- **Sync vs async response?** Scans run minutes-to-hours → async, return
  a `scan_id` immediately.
- **Result delivery?** Inline JSON, paginated, or signed object-store
  URL? Millions of paths require pagination at minimum.
- **Idempotency?** Same root submitted twice — start a new scan or reuse
  the in-flight one? Client-supplied `request_id` is the standard way.
- **Multi-tenant?** Tenant isolation in the DB and the queue?
- **Retention?** How long do `scan_id → groups` results live?
- **Definition of "duplicate"?** Byte-identical (SHA-256), or
  near-duplicate (perceptual hash, document shingles)?
- **Symlinks?** Track by inode, not path — otherwise you cycle.

The design below assumes async API, paginated results, idempotent on
`request_id`, single-tenant, byte-identical, ~30-day retention.

### Block diagram

```
                                ┌──────────┐
   POST /scan                   │  Client  │
   GET  /scan/{id}              └────┬─────┘
                                     │
                          ┌──────────▼──────────┐
                          │      API tier       │  stateless HTTP
                          └──────┬───────┬──────┘
                                 │       │
                  ┌──────────────┘       └────────┐
                  │ create scan row,              │ read scan status,
                  │ enqueue seed job              │ paginate groups
                  ▼                               ▼
        ┌────────────────┐                ┌──────────────┐
        │   job queue    │                │   Postgres   │
        │ (Kafka/SQS)    │                │  (sharded)   │
        └───────┬────────┘                └──────▲───────┘
                │                                │
          workers pull                   write index rows,
                ▼                        atomically inc/dec
        ┌──────────────────┐             pending_jobs
        │  worker fleet    ├─────────────────────┘
        │  (stateless,     │
        │   autoscaled)    ◄─────── enqueue child job per subdir
        └──────┬───────────┘                   ▲
               │ subdir found                  │
               └───────────────────────────────┘
```

Five components, all stateless except the DB:

- **API tier** — stateless HTTP servers; two endpoints + cancel.
- **Job queue** — Kafka / SQS / Redis Streams. At-least-once delivery,
  visibility timeout, durable.
- **Worker fleet** — stateless processes pulling jobs from the queue.
  Auto-scaled on queue depth.
- **Postgres** — the source of truth: `scans` table, `content_index`,
  `dispatched_jobs` ledger.
- **File system** — the thing being scanned. NFS, S3, a cloud blob
  store. Read-only from this service.

### API surface

```http
POST /scan
  body:     { "root_path": "/u/123/photos", "request_id": "uuid-v4" }
  response: 201 { "scan_id": "abc..." }
  Idempotent on request_id — repeated POST with the same id returns the
  existing scan_id without starting a new scan.

GET /scan/{scan_id}
  response: 200 {
    "scan_id":              "...",
    "status":               "pending" | "running" | "complete" | "failed" | "cancelled",
    "files_scanned":        12345,
    "directories_scanned":  67,
    "duplicate_group_count": 42,
    "started_at": "...", "completed_at": "..."
  }

GET /scan/{scan_id}/groups?cursor=<opaque>&limit=100
  response: 200 {
    "groups": [
      { "content_hash": "...", "size": 1024,
        "paths": [".../a.jpg", ".../b.jpg"] },
      ...
    ],
    "next_cursor": "..."     // null when exhausted
  }
  Cursor-based pagination — OFFSET-based pagination degrades at the tail.

DELETE /scan/{scan_id}
  response: 204
  Best-effort cancel. Marks the scan; workers check status before picking
  up a job and skip cancelled scans.
```

Three things worth calling out:

- **POST returns immediately.** The recursion makes the work transitively
  huge; you cannot block the HTTP call.
- **GET status uses fast counters** (`files_scanned`, `pending_jobs`)
  maintained on the scan row — the progress endpoint is essentially free
  because termination already needs the counter.
- **Groups use cursor pagination, not OFFSET.** OFFSET is `O(n)` in
  Postgres and scans degrade catastrophically at the tail of a large
  result set.

### Database schema

```sql
-- One row per scan request — the lifecycle record.
CREATE TABLE scans (
    scan_id          uuid PRIMARY KEY,
    request_id       uuid UNIQUE NOT NULL,      -- client idempotency key
    root_path        text NOT NULL,
    status           text NOT NULL,             -- pending|running|complete|failed|cancelled
    pending_jobs     int  NOT NULL DEFAULT 0,   -- ★ termination counter
    files_scanned    bigint NOT NULL DEFAULT 0,
    dirs_scanned     bigint NOT NULL DEFAULT 0,
    started_at       timestamptz NOT NULL DEFAULT now(),
    completed_at     timestamptz,
    error            text
);

-- One row per (scan, file). The dedup index.
CREATE TABLE content_index (
    scan_id       uuid   NOT NULL,
    content_hash  bytea  NOT NULL,              -- SHA-256
    path          text   NOT NULL,
    file_size     bigint NOT NULL,
    PRIMARY KEY (scan_id, content_hash, path)   -- ON CONFLICT DO NOTHING target
) PARTITION BY HASH (scan_id);                  -- per-scan partitions = trivial retention drop

CREATE INDEX ON content_index (scan_id, content_hash);

-- One row per dispatched directory — at-least-once de-dupe ledger.
CREATE TABLE dispatched_jobs (
    scan_id   uuid NOT NULL,
    dir_path  text NOT NULL,
    PRIMARY KEY (scan_id, dir_path)
);
```

Three tables, and what skipping each costs:

- **`scans`** — without it, no `scan_id → results` lookup, no
  cancellation, no status endpoint.
- **`content_index`** — without `path` in the PK, a re-delivered file
  double-inserts and the group counts inflate.
- **`dispatched_jobs`** — without it, a re-delivered *directory* job
  re-enqueues its children → counter inflates indefinitely →
  termination never fires.

The PK order `(scan_id, content_hash, path)` matters: almost every query
filters on `scan_id` first, so leading with it makes the lookup a
contiguous index range-scan. The same logic drives the `PARTITION BY
HASH(scan_id)` — a scan's data lives together physically, retention is
one `DROP PARTITION`.

The final groups query — powering `GET /scan/{id}/groups`:

```sql
SELECT content_hash, file_size, array_agg(path ORDER BY path) AS paths
FROM content_index
WHERE scan_id = $1 AND content_hash > $cursor
GROUP BY content_hash, file_size
HAVING count(*) >= 2
ORDER BY content_hash
LIMIT $limit;
```

`HAVING count(*) >= 2` is the same bucket-by-key filter as Tier 1's
`if len(group) >= 2` — the SQL form of the same idea.

### ★ The recursive self-spawning async service ★

The single insight the interviewer is looking for: **the async service
recursively invokes itself**. The API enqueues exactly one job; every
other job is spawned by a job.

The naïve design — one job per scan, a single worker walking the whole
tree — fails two ways:

1. A single sequential walk has zero fleet parallelism; the whole load
   lives on one worker.
2. The job times out (queues have visibility timeouts ~1–10 minutes)
   → re-delivered → restarts from scratch → quadratic write
   amplification under retry.

The right design is **one job per directory.** The seed job is
`scan(scan_id, root_path)`. Each job:

```
worker.process(job{scan_id, dir_path}):

    if scans.status(scan_id) == 'cancelled':
        ack; return.

    BEGIN TRANSACTION
        # Idempotency: have we already dispatched this dir's children?
        INSERT dispatched_jobs(scan_id, dir_path) ON CONFLICT DO NOTHING
        if conflict:
            COMMIT; ack; return.   # at-least-once re-delivery; nothing to do.

        entries        = list(dir_path)        # one level only — no recursion here
        subdirs, files = partition(entries)

        # 1. Enqueue child jobs BEFORE decrementing self.
        for sd in subdirs:
            enqueue job{scan_id, sd}
        UPDATE scans
           SET pending_jobs = pending_jobs + len(subdirs)
         WHERE scan_id = ?

        # 2. Hash & index files (this dir's actual content).
        for f in files:
            h = streaming_sha256(f)
            INSERT content_index(scan_id, h, f.path, f.size)
                ON CONFLICT DO NOTHING

        UPDATE scans
           SET files_scanned = files_scanned + len(files),
               dirs_scanned  = dirs_scanned  + 1,
               pending_jobs  = pending_jobs  - 1,
               status        = CASE WHEN pending_jobs - 1 = 0
                                    THEN 'complete' ELSE status END,
               completed_at  = CASE WHEN pending_jobs - 1 = 0
                                    THEN now() ELSE completed_at END
         WHERE scan_id = ?
    COMMIT

    ack the job.
```

Three properties that fall out of this shape:

- **The recursion is queue subdivision, not function recursion.** Each
  level of the directory tree is one wave of jobs in the queue. `W`
  workers parallelize across the wave. A tree of depth `D` and branching
  `B` has up to `B^D` leaf jobs, processed in `O(B^D / W)` wall time.
- **Each job is bounded.** A directory's children list is one
  bounded-size response (mitigations for very large dirs below). The
  whole job fits a queue visibility timeout.
- **Self-feeding job stream.** The API enqueues exactly one job — the
  seed. Every other job is spawned by a job. The API does no
  per-directory work; the worker fleet's throughput scales with the
  tree, not the API.

**Ordering inside the transaction is load-bearing.** A worker MUST
enqueue its children and increment `pending_jobs` BEFORE decrementing
its own count. Reverse the order and the counter can transiently hit
zero with children still pending — the scan flips to `complete`
prematurely. The transaction protects the invariant atomically: either
both the increment and the decrement land, or neither does.

This is also the distinction the feedback hints at: this is a
*distributed systems* answer, not a *multi-threading* answer. The signal
is durable queue + stateless workers + idempotent writes + counter-based
termination. All four work across machines, across crashes, across
redeploys. Threads do not.

### Termination — "scan complete" detection

Three options the interviewer will probe:

| Option | How it works | Trade-off |
|--------|--------------|-----------|
| **A. `pending_jobs` counter on the `scans` row** | atomically `+= children_count` before decrementing self; when counter hits zero, scan is complete | one DB write per job-transition; simple, correct, mirrors `queue.Queue.join()` in the prototype |
| **B. Workflow engine (Temporal / Cadence)** | seed workflow `await`s its child workflows transitively; engine tracks the tree | no DB counter, but adds an external dependency; the engine owns retry / state semantics |
| **C. Per-directory bookkeeping** | each job appends its dir to a "completed" set; API thread polls "is the full tree covered?" | requires knowing the tree in advance — but the tree is exactly what the scan is *learning*; circular |

Pick **A** for an interview answer. It's the same shape as
`queue.Queue.join()` in the prototype: every `put` increments an
unfinished-tasks counter, every `task_done` decrements it, `join()`
blocks until zero. The `scans.pending_jobs` row is the durable,
distributed version of that counter.

Make a **stalled-scan watchdog** explicit: if `pending_jobs > 0` and
nothing has updated the scan row in N minutes, the scan is wedged (lost
job in the queue, dead worker, retry storm) → either retry the
`dispatched_jobs` rows whose corresponding work never landed, or mark
the scan `failed`.

### Idempotency

Two independent layers, both essential:

**Client → API.** `request_id` as a unique key in `scans`.
`INSERT ... ON CONFLICT (request_id) DO NOTHING RETURNING scan_id`
returns the existing `scan_id` for a duplicate POST. Handles client
retries on network blips.

**Queue → Worker.** Every queue worth using is *at-least-once*; a worker
may process the same `scan(scan_id, dir_path)` twice. Three pieces make
this safe:

1. `dispatched_jobs (scan_id, dir_path)` unique key — a re-delivered
   directory job sees the conflict and ACKs without re-enqueueing
   children. Without this, the counter inflates indefinitely.
2. `content_index (scan_id, content_hash, path)` PK with `ON CONFLICT
   DO NOTHING` — re-inserts of the same file are no-ops.
3. The whole "list + enqueue + insert + decrement" happens in one
   transaction — partial failure means nothing committed, a retry sees
   a clean slate.

### Failures and edge cases

| Failure | Recovery |
|---------|----------|
| Worker crash mid-job | visibility timeout fires → queue re-delivers → idempotency absorbs the retry |
| DB unavailable mid-transaction | transaction rolls back → worker NACKs → re-delivery later |
| Worker enqueues children, dies before COMMIT | nothing committed; safe retry |
| Worker COMMITs the decrement that hits zero | same transaction marks scan `complete` and `completed_at` — atomic |
| Lost job (queue durability hole) | `pending_jobs` never reaches zero → stall watchdog fires after N minutes → retry the `dispatched_jobs` rows whose work didn't land, or mark `failed` |
| Hot directory (1M files in one dir) | sub-batch the listing: page through `scandir`, enqueue `scan_files(scan_id, dir, [batch of N files])` jobs that hash files only, no children |
| Hot content hash (1M paths in one group) | groups endpoint paginates *within* a hash by `path` cursor |
| Client cancels | DELETE → `scans.status = 'cancelled'` → workers check status before picking up a job and skip; in-flight jobs run to completion (cheaper than preempting) |
| Symlink cycles | track by inode, not path; insert `(scan_id, inode)` into a visited-set table before processing |

### Scaling levers

- **Auto-scale workers on queue depth.** Target depth-per-worker ≤ K;
  scale up on overshoot, down on idle.
- **Partition `content_index` by `scan_id`.** Retention is one
  `DROP PARTITION`, not a `DELETE`. Old scans cost a constant to retire.
- **Streaming SHA-256** in 64 KB chunks — large files do not OOM
  workers; this is what the prototype's `while chunk := f.read(65536)`
  loop is doing at single-process scale.
- **Two-stage hashing** (size bucket → full hash) — most files have
  unique sizes and never need a hash at all. Tier 3 in the prototype is
  the same idea.
- **Multi-tenant** — add `tenant_id` everywhere, partition by
  `(tenant_id, scan_id)`, rate-limit `POST /scan` per tenant.
- **Multi-region** — pin scans to the region holding the file system;
  central aggregator only if cross-region grouping is required.

### What this design defers

State these so the interviewer knows you considered and parked them:

- **Cross-scan dedup** — compare a new scan against a historical content
  index. Needs `content_index` to retain by *content* not *scan*, plus a
  separate global hash-to-path index.
- **Near-duplicate detection** — perceptual hashing for images,
  shingles for documents. Same architecture, different hash function.
- **Encrypted-at-rest content** — workers need decryption keys per scan;
  becomes a privileged context with its own auth story.
- **Streaming results** — server-sent events to push groups as they're
  discovered, rather than polling.

### Simulation → production mapping

The single-process `DistributedFinder` mirrors the architecture above,
piece for piece:

| Simulation primitive       | Production analogue                                 |
|----------------------------|-----------------------------------------------------|
| `queue.Queue`              | Kafka / SQS / Redis Streams                         |
| `threading.Thread` pool    | a fleet of stateless worker processes               |
| `defaultdict(list)` + lock | Postgres `content_index` + `ON CONFLICT DO NOTHING` |
| `queue.join()`             | `scans.pending_jobs` counter, atomically inc/dec    |
| in-process `find()` call   | `POST /scan` + `GET /scan/{id}/groups`              |

The shape is the same; what changes is what holds the state — process
memory and threads → durable queue, Postgres, and a stateless worker
fleet.

## Running the tests

```sh
uv run pytest Algorithms/file_duplicates/tests/ -q
```
