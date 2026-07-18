# Filesystem Crawler

Walk a directory tree from a root path and return every **file** beneath
it, descending recursively into subdirectories. The local-filesystem
sibling of `web_crawler`: the same frontier-based traversal and the same
termination problem under concurrency, but over a *tree* instead of a
*graph*.

Modeled on the classic "crawl a filesystem" interview question (the
front half of "find duplicate files") and the Unix `find(1)` / Python
`os.walk` problem. Related LeetCode references: #1233 (Remove
Sub-Folders from the Filesystem), #588 (Design In-Memory File System),
#1166 (Design File System). Closely related problems in this repo:
`web_crawler` (the graph analogue) and `file_duplicates` (whose Tier 1
is a filesystem crawl).

## Problem

You are given:

- a `root` path, e.g. `/home/alice`;
- a `FileSystem` exposing two methods that stand in for real syscalls:
  - `list_dir(path) -> list[str]` — the immediate child *names* under a
    directory (raises `OSError` if `path` is unreadable);
  - `is_dir(path) -> bool` — whether a path is a directory or a file.

Return every file path reachable beneath `root` by recursively
descending into subdirectories. Each file appears once; order does not
matter.

It is a **tree traversal**: directories are internal nodes, files are
leaves, and `list_dir` expands a node's children. Because a filesystem
tree gives every entry exactly one parent, no two paths collide — so,
unlike `web_crawler`, **no global visited set is needed**.

The one exception that breaks the tree property is **symlinks**: a
symlink can point back at an ancestor, turning the tree into a graph
with cycles. The default policy here (matching `os.walk(followlinks=
False)`) is to **not follow symlinks** — they are treated as leaves and
recorded as files, never descended into. Following them is documented as
a variant below; it reintroduces `web_crawler`'s dedup problem (a
visited-set of resolved inodes to break cycles).

Directories the crawler cannot read (`list_dir` raises `OSError` /
`PermissionError`) are skipped, not fatal — a single unreadable folder
must not abort the whole crawl.

## Tiers

| Tier | Class                 | Concurrency model              | The lesson                                                                       |
|------|-----------------------|--------------------------------|----------------------------------------------------------------------------------|
| 1    | `SimpleCrawler`       | single-threaded                | the algorithm — frontier of dirs, dir/file dispatch, symlinks-as-leaves, skip unreadable |
| 2    | `ThreadPoolCrawler`   | `ThreadPoolExecutor`           | FS syscalls release the GIL → threads genuinely parallelize disk I/O; dynamic tasks make termination the trap |
| 3    | `AsyncCrawler`        | `asyncio` + `to_thread`        | there is **no** portable async FS API — asyncio orchestrates, threads run the blocking `list_dir` |
| 4    | `DistributedCrawler`  | worker fleet + walk queue      | recursive self-spawning directory walk — the system-design follow-up                   |

Every tier exposes the same synchronous entry point:

```python
crawler.crawl(root, fs) -> list[str]
```

`fs` is the injected `FileSystem` (the `list_dir` / `is_dir` pair). Both
Tier 3's event loop and Tier 4's queue are driven *inside* `crawl()`, so
the entry point stays synchronous across every tier — exactly as in
`web_crawler`.

Each tier answers the previous one's weak spot. Tier 1 is a clean
single-threaded walk — correct, but it issues one blocking `list_dir`
syscall at a time, so on a network filesystem (NFS, a FUSE-mounted
object store) where each call is a round-trip, it spends almost all its
wall-clock time waiting. Tier 2 exploits the fact that filesystem
syscalls **release the GIL**: a `ThreadPoolExecutor` can have many
`list_dir` calls in flight at once, hiding per-call latency — the catch
is that each directory listed reveals more directories, so the task
count is unknown up front and termination detection becomes the hard
part. Tier 3 reaches for `asyncio` and discovers the defining
filesystem fact: there is no truly-async FS API to await, so the event
loop must offload every blocking `list_dir` to a worker thread via
`asyncio.to_thread` — asyncio drives concurrency, threads do the
syscalls. Tier 4 leaves the single machine: each directory becomes a
queue job, subdirectories enqueue child jobs, and a `pending_jobs`
counter detects completion across a worker fleet.

### Why there is no "pure-async" tier (the contrast with web_crawler)

`web_crawler` has a `PureAsyncCrawler` (Tier 3b) because network I/O has
real async APIs — `aiohttp` lets the event loop await a socket directly,
no threads. **The filesystem has no equivalent.** Local file operations
are "always ready" to the OS readiness model behind `epoll`/`kqueue`, so
they never yield to the event loop the way a socket does. The popular
`aiofiles` library is a thread-pool wrapper, not true async I/O. So
fs_crawler's async tier is *necessarily* asyncio-over-threads — the
absence of a pure-async variant is itself the lesson.

### Following symlinks (the documented variant)

To follow symlinks instead of treating them as leaves, the crawler must
detect cycles, which means tracking which real directories it has
already entered:

- Resolve each candidate directory to a stable identity — its
  `(st_dev, st_ino)` inode pair (the device + inode number uniquely
  identifies a file across the whole machine, even through symlinks and
  hard links).
- Keep a `visited_inodes` set. Before descending into a directory,
  check-and-add; if it was already present, a symlink has led back to
  somewhere already walked — skip it to break the cycle.

This is exactly `web_crawler`'s `visited` set, just keyed by inode
instead of URL. It is the moment the filesystem stops being a tree and
becomes a graph.

### BFS vs DFS

Tier 1 can use either a queue (BFS, `deque.popleft`) or a stack
(DFS, `deque.pop` or recursion). They return the same set of files; the
difference is memory profile. BFS holds the whole current *level* of the
tree in the frontier — costly for wide, shallow trees (a directory with
a million subdirectories). DFS holds one root-to-leaf *path* — costly
for deep, narrow trees, and risks a `RecursionError` if done with native
recursion on a very deep tree. `os.walk` is iterative DFS for this
reason. The reference uses BFS with an explicit `deque`; either is
acceptable as long as it is iterative, not naive recursion.

## Distributed extension — the system-design follow-up

Every tier above is **single-machine**. The system-design follow-up
takes the next step: *how do you crawl a filesystem with billions of
files — a multi-petabyte storage tier, a fleet of user home
directories — across a worker fleet, without re-walking, without losing
progress when a worker dies, and without one giant directory or one
unreadable subtree wedging the whole job?*

This is the **same recursive-self-spawning pattern** as
`file_duplicates`'s directory-walk Tier 4 and `web_crawler`'s Tier 4:
each unit of work (listing one directory) discovers child units (its
subdirectories) and enqueues them. What's *different* from web_crawler
is the dedup problem — there isn't one. A filesystem tree gives each
path a single parent, so each directory is enqueued exactly once by its
unique parent. **No global visited set** (unless symlinks are followed,
in which case an inode-dedup set returns — see the variant).

### Opener — clarifying questions

- **Scope?** One root walked to completion (a backup, an index build),
  or continuous watching for changes (an inotify/FSEvents pipeline)?
  Drives bounded-job vs. forever-running.
- **Follow symlinks?** Default no (tree, no dedup). If yes, an inode
  visited-set is required and the storage/coordination cost rises.
- **Cross mount points?** Should the walk cross filesystem boundaries
  (different `st_dev`) or stay within one volume? `find -xdev` /
  `os.walk` both default to *not* crossing; matters when `/home` and
  `/mnt` are different volumes.
- **Output?** Just paths, or paths + metadata (size, mtime, owner)?
  Metadata means a `stat` per entry — a second syscall per file,
  doubling the I/O.
- **Storage backend?** Local disk, NFS, or an object store behind a
  FUSE layer? Object stores have API rate limits and high per-call
  latency — drives request batching and a rate-limit gate.
- **Consistency?** Is the tree allowed to change *during* the walk
  (files created/deleted underneath us)? Almost always yes — drives
  TOCTOU-tolerant handling (a path that vanishes mid-walk is skipped,
  not fatal).
- **Incremental?** Full walk every time, or only re-walk subtrees whose
  `mtime` changed since the last crawl? Drives a delta-crawl optimization.

Assumed for the design below: bounded scope (one root, walk to
completion), symlinks **not** followed, do **not** cross mount points,
output paths + size + mtime, object-store backend with API rate limits,
TOCTOU-tolerant, full (non-incremental) walk.

### Block diagram

```
                              ┌──────────┐
   POST /crawl                │  Client  │
   GET  /crawl/{id}           └────┬─────┘
                                   │
                        ┌──────────▼──────────┐
                        │      API tier       │  stateless HTTP
                        └──────┬───────┬──────┘
                               │       │
                  ┌────────────┘       └────────┐
                  │ create crawl row,           │ read status,
                  │ enqueue walk(root)          │ paginate files
                  ▼                             ▼
        ┌────────────────────┐          ┌──────────────┐
        │   walk queue       │          │   Postgres   │
        │  (sharded by       │          │  (crawl rows │
        │   subtree-hash,    │          │   + files)   │
        │   Kafka/SQS)       │          └──────▲───────┘
        └────────┬───────────┘                 │
                 │                       INSERT file rows
            workers pull                        │
                 ▼                              │
        ┌──────────────────┐                    │
        │  walker fleet    ├────────────────────┘
        │  (stateless,     │
        │   autoscaled)    ◄─── enqueue child walk(subdir) per subdirectory
        └──────┬───────────┘             ▲
               │ list_dir(path),         │
               │ stat(entry)             │
               ▼                         │
        ┌──────────────────┐             │
        │ storage backend  │─────────────┘
        │ (NFS / object    │
        │  store / FUSE)   │
        └──────────────────┘
```

Five components:

- **API tier** — stateless HTTP. `POST /crawl`, `GET /crawl/{id}`,
  `GET /crawl/{id}/files` (paginated).
- **Walk queue** — Kafka / SQS / Redis Streams. One job = "list this
  one directory." Sharded by a hash of the directory path so load
  spreads evenly across partitions (there is no politeness constraint
  like web_crawler's per-host rule — the only reason to shard is
  throughput).
- **Walker fleet** — stateless workers. Pull one directory job, list
  it, `stat` each entry, record files, enqueue a child job per
  subdirectory.
- **Crawl results** — Postgres: a `crawls` row per request (with the
  termination counter) and a `crawl_files` partition listing every file
  found.
- **Storage backend** — the actual filesystem under the walk (local
  disk, NFS, or an object store behind FUSE). The source of the
  `list_dir` / `stat` calls and their latency.

### API surface

```http
POST /crawl
  body:     { "root": "/home/alice", "request_id": "uuid" }
  response: 201 { "crawl_id": "abc..." }
  Idempotent on request_id — same request returns the existing crawl_id.

GET /crawl/{crawl_id}
  response: 200 {
    "crawl_id":     "...",
    "status":       "pending"|"running"|"complete"|"failed"|"cancelled",
    "dirs_walked":  84211,
    "files_found":  1937402,
    "errors":       12,                # dirs that raised on list_dir
    "pending_jobs": 67,                # ★ termination counter
    "started_at":   "...", "completed_at": "..."
  }

GET /crawl/{crawl_id}/files?cursor=<opaque>&limit=1000
  response: 200 {
    "files": [ {"path": "/home/alice/.bashrc", "size": 3771, "mtime": "..."}, ... ],
    "next_cursor": "..."     # null when exhausted
  }

DELETE /crawl/{crawl_id}
  response: 204
  Marks the crawl cancelled; workers check status before listing.
```

### Database / store schemas

```sql
-- One row per crawl request.
CREATE TABLE crawls (
    crawl_id      uuid PRIMARY KEY,
    request_id    uuid UNIQUE NOT NULL,
    root          text NOT NULL,
    root_dev      bigint,                    -- st_dev of root, for -xdev check
    status        text NOT NULL,
    pending_jobs  int  NOT NULL DEFAULT 0,    -- ★ termination counter
    dirs_walked   bigint NOT NULL DEFAULT 0,
    files_found   bigint NOT NULL DEFAULT 0,
    errors        bigint NOT NULL DEFAULT 0,
    started_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,
    error         text
);

-- One row per file found (the result set).
CREATE TABLE crawl_files (
    crawl_id  uuid NOT NULL,
    path      text NOT NULL,
    size      bigint,
    mtime     timestamptz,
    PRIMARY KEY (crawl_id, path)
) PARTITION BY HASH (crawl_id);
```

```redis
# ONLY if following symlinks: the inode visited-set (the dedup oracle).
# Keyed by crawl, member is "dev:ino". SADD returns 1 if new, 0 if seen.
SADD crawl:{crawl_id}:inodes  "{st_dev}:{st_ino}"
```

The Redis set exists **only in the follow-symlinks variant**. In the
default tree walk there is no dedup oracle at all — the queue is the
entire coordination mechanism.

### ★ The recursive self-spawning walker ★

The single critical insight: each directory is one job; its
subdirectories become child jobs. Same pattern as `file_duplicates` and
`web_crawler`, minus the dedup.

```
worker.process(job{crawl_id, dir_path}):

    if crawls.status(crawl_id) in ('cancelled', 'failed'):
        ack; return.

    BEGIN TRANSACTION
        # Optional storage-API rate-limit gate (object-store backends).
        if not rate_limit.try_acquire(storage_shard(dir_path)):
            requeue job with delay; ack; return.

        # List the directory. Unreadable dir → count + skip, NOT fatal.
        try:
            entries = list_dir(dir_path)
        except OSError:
            UPDATE crawls SET errors = errors + 1,
                              pending_jobs = pending_jobs - 1
                          WHERE crawl_id = ?
            COMMIT; ack; return.

        UPDATE crawls SET dirs_walked = dirs_walked + 1 WHERE crawl_id = ?

        child_dirs = 0
        files_batch = []
        for name in entries:
            path = join(dir_path, name)
            try:
                st = stat(path)              # TOCTOU: may vanish → skip
            except OSError:
                continue

            if is_dir(st):
                # --xdev: do not cross filesystem boundaries.
                if st.st_dev != crawls.root_dev(crawl_id):
                    continue
                # Default: skip symlinked dirs. (Variant: SADD inode;
                # skip on dup to break cycles, then descend.)
                if is_symlink(st):
                    continue
                enqueue job{crawl_id, path}
                child_dirs += 1
            else:
                files_batch.append((path, st.st_size, st.st_mtime))

        # Bulk-insert the files found at this level.
        INSERT INTO crawl_files VALUES (files_batch...) ON CONFLICT DO NOTHING

        UPDATE crawls
           SET files_found  = files_found + len(files_batch),
               pending_jobs = pending_jobs + child_dirs - 1,
               status = CASE WHEN pending_jobs + child_dirs - 1 = 0
                             THEN 'complete' ELSE status END,
               completed_at = CASE WHEN pending_jobs + child_dirs - 1 = 0
                                   THEN now() ELSE completed_at END
         WHERE crawl_id = ?
    COMMIT

    ack the job.
```

Two properties:

- **The recursion is queue subdivision.** Each level of the directory
  tree is a wave of jobs. The fleet processes them in parallel;
  `pending_jobs` tracks total outstanding work; termination is the
  counter hitting zero.
- **No SADD-before-enqueue, unlike web_crawler.** A tree gives each
  directory exactly one parent, so each child job is enqueued by exactly
  one worker — there is no race to claim it, no duplicate enqueue to
  prevent. The queue alone is sufficient. (The follow-symlinks variant
  re-introduces the `SADD` inode check precisely because symlinks can
  give a directory more than one path-parent.)

### Termination — the same counter as web_crawler and file_duplicates

`crawls.pending_jobs` increments by the count of newly-enqueued child
directories *before* decrementing self, all inside one transaction so
the invariant holds under concurrent workers. When the counter hits
zero, the crawl flips to `complete`. This is the durable analogue of
`queue.Queue.join()`.

Watchdog: if `pending_jobs > 0` and the row hasn't advanced in N
minutes, the crawl is wedged (lost job or dead worker) → retry or fail.

### Idempotency

- **Client → API:** `request_id` unique on `crawls`. Repeated POST
  returns the existing `crawl_id`.
- **`crawl_files` PK `(crawl_id, path)`** with `ON CONFLICT DO NOTHING` —
  a re-delivered directory job re-inserts the same file rows harmlessly.
- **`pending_jobs` arithmetic** lives in one transaction — a re-delivered
  job that already committed must be deduped by a processed-jobs marker
  (e.g. a `dir_path` row with a unique key) so the counter isn't
  double-adjusted. This is the one subtlety: unlike the file inserts,
  the counter is *not* naturally idempotent, so the worker records
  "I processed dir X" and skips on redelivery.

### Failures and edge cases

| Failure | Effect → Mitigation |
|---------|---------------------|
| Worker crash mid-listing | visibility timeout → job re-delivered → processed-jobs marker + `ON CONFLICT` absorb the replay |
| Permission denied on a directory | `list_dir` raises → counted as `errors`, children not enqueued; crawl continues |
| Directory deleted mid-walk (TOCTOU) | `list_dir`/`stat` raises on a now-gone path → skip the entry, no error escalation |
| Entry vanishes between `list_dir` and `stat` | `stat` raises → entry skipped (it was deleted underneath us) |
| Symlink cycle (follow-symlinks variant) | inode `SADD` returns 0 on a re-entered dir → skip → cycle broken |
| Hard-link / bind-mount aliasing | same file reachable by two paths → recorded twice (acceptable for path enumeration; dedup by inode if unique-files required) |
| Giant directory (10M+ entries) | a single `list_dir` job is huge → paginate the listing (cursor-based `scandir`) and enqueue continuation jobs |
| Crossing a mount point | `st_dev` differs from `root_dev` → skip (`-xdev` semantics) |
| Storage backend slow / 429 | rate-limit gate requeues with backoff; visibility timeout re-delivers |
| Lost job in queue | `pending_jobs` never reaches zero → watchdog after N min → retry or fail |
| Client cancels | DELETE → `status = 'cancelled'` → workers skip jobs for cancelled crawls |

### Scaling levers

- **Subtree-hash queue sharding.** Directory path → partition; spreads
  listing load evenly. No politeness constraint to honor (it's your own
  storage), so sharding is purely for throughput.
- **Giant-directory pagination.** A directory with millions of entries
  is listed in cursor-based chunks (`scandir` continuation), each chunk
  a separate job, so no single worker holds a 10M-entry list in memory.
- **Bulk file inserts.** Batch the `crawl_files` rows per directory into
  one multi-row `INSERT` rather than a round-trip per file.
- **Delta crawls (incremental).** Store each directory's `mtime` from
  the last crawl; on re-crawl, skip subtrees whose directory `mtime` is
  unchanged. Turns a full re-walk into an only-the-diff walk — the
  single biggest win for repeated crawls.
- **Bloom filter for inodes (follow-symlinks variant only).** Trades a
  small false-positive rate (a few real directories wrongly skipped as
  "already seen") for ~10× less memory than an exact inode set.
- **Multi-region.** Pin a crawl to the region co-located with its
  storage backend; avoids cross-region `stat` latency.

### What this design defers

- **Continuous / watch-based crawling.** This is a one-shot walk that
  terminates. A live index uses `inotify` (Linux) / `FSEvents` (macOS)
  to stream changes, never terminating.
- **Content reading.** This enumerates paths + metadata only. Reading
  file *contents* (to hash for dedup, as `file_duplicates` does, or to
  extract text for search) is a separate, far more I/O-heavy pipeline.
- **The filter predicate.** A `find`-style match (glob, size, mtime,
  owner) layered on the walk — easy to add as a per-entry test, omitted
  here to keep the walk pure.
- **Ordering guarantees.** Files come back in completion order across
  the fleet, not lexicographic or depth order.
- **Snapshot consistency.** The walk sees the tree as it mutates; it is
  not a point-in-time snapshot. A consistent view needs a filesystem
  snapshot (LVM, ZFS, btrfs) taken before the walk.

### Simulation → production mapping

The single-process `DistributedCrawler` simulation mirrors this
architecture piece for piece — what changes is what holds the state and
how the parts communicate:

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `queue.Queue` of directory paths | Kafka / SQS / Redis Streams, sharded by subtree-hash |
| `threading.Thread` worker pool | a fleet of stateless walker processes |
| injected `FileSystem.list_dir` | `scandir` against NFS / object-store / FUSE backend |
| in-memory `list` of results | `crawl_files` partitioned Postgres table |
| `queue.join()` | `crawls.pending_jobs` counter, atomically inc/dec |
| in-process `crawl()` call | `POST /crawl` + `GET /crawl/{id}/files` |
| (none — tree, no dedup) | (none — unless following symlinks → Redis inode SADD) |
| (none) | per-directory `mtime` store for delta crawls |

## Running the tests

```sh
uv run pytest Algorithms/fs_crawler/tests/ -q
```
