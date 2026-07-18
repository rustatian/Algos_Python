# Web Crawler

Crawl every page reachable from a starting URL that lives on the **same
hostname**, following the links each page exposes, and visiting no page
twice.

Modeled on LeetCode #1242 (Web Crawler Multithreaded) and the classic
"Crawler" interview question. A learning exercise: a ladder of tiers, the same
result, an escalating concurrency model at each step.

## Problem

You are given:

- a `start_url`, e.g. `http://news.site.com/home`;
- an `HtmlParser` exposing one method, `get_urls(url) -> list[str]`, which
  returns every URL linked from that page. It stands in for an HTTP fetch
  plus HTML link extraction.

Return every URL reachable from `start_url` whose hostname equals
`start_url`'s hostname. Each URL appears once; order does not matter.

A **hostname** is the slice between `http://` and the next `/`. Every URL
uses the `http://` scheme. `http://a.com/x` and `http://a.com/y` share a
hostname; `http://a.com` and `http://b.com` do not.

It is a graph traversal: pages are nodes, links are directed edges, and
`get_urls` expands a node's neighbors. The crawl explores the subgraph the
hostname filter carves out — it *follows links*, it does not enumerate the
host, so a same-host page that nothing links to is never reached.

## Tiers

| Tier | Class               | Concurrency model              | The lesson                                                             |
|------|---------------------|--------------------------------|------------------------------------------------------------------------|
| 1    | `SimpleCrawler`     | single-threaded                | the algorithm — frontier + visited set, hostname filter                |
| 2    | `ThreadPoolCrawler` | `ThreadPoolExecutor`           | shared `visited` needs a lock; dynamic tasks make termination the trap |
| 2b   | `LevelCrawler`      | level-synchronous `.map()`     | lock-free by *confinement* — workers only fetch; the main thread owns `visited` |
| 3a   | `AsyncCrawler`      | `asyncio` + `to_thread`        | blocking parser pushed to worker threads — asyncio drives, threads fetch |
| 3b   | `PureAsyncCrawler`  | `asyncio`, async-native        | async-native parser awaited directly — cooperative, no threads at all  |
| 4    | `QueueCrawler`      | `queue.Queue` + worker threads | `Queue.join()` / `task_done()` — termination built in                  |

Every tier exposes the same synchronous entry point:

```python
crawler.crawl(start_url, parser) -> list[str]
```

Tiers 1, 2, 2b, 3a, and 4 take the synchronous parser. Only Tier 3b takes
an async-native parser (`async def get_urls`). Both Tier 3 crawlers still
drive the event loop inside `crawl()`, so the entry point stays synchronous
across every tier.

The hard part of a *concurrent* crawl is not parallelism — it is
**termination detection**. Work is discovered dynamically: every page
fetched reveals more pages, so the total task count is unknown up front.
Each tier past the first is a different answer to "how do I know the crawl
has finished?"

## Distributed extension — the system-design follow-up

Every tier in this repo is **single-machine**. The system-design
follow-up takes the next step: *how do you crawl the web at scale?* The
architectural question shifts from "how do I detect termination in one
process?" to "how does a fleet of workers cooperatively traverse a graph
that spans billions of pages, without crawling the same URL twice,
without overwhelming any one host, and without losing all progress when
a worker dies?"

This is structurally the **same recursive-self-spawning pattern** as
`file_duplicates`'s Tier 4: each unit of work (one page fetch) discovers
child units (the URLs linked from that page) and enqueues them. What's
different is the de-duplication problem (URLs from *any* page can point
to *any* URL — the visited set is global, not per-job).

### Opener — clarifying questions

- **Scope?** One starting URL crawled in full (LeetCode #1242), or
  continuous crawling of the whole web (Googlebot)? Drives the entire
  shape — bounded job vs. forever-running pipeline.
- **Politeness?** Per-host rate limit (Googlebot's "1 req/sec/host"
  rule)? Drives a per-host queue / lease system.
- **Robots.txt?** Honored? Cached how often?
- **Freshness?** Re-crawl after how long? Drives a re-queue scheduler.
- **Multi-tenant?** One global crawl or per-customer scoped crawls?
- **Output?** Just the URL list, or full page content? Content changes
  the storage shape from a URL set to an object store.

Assumed for the design below: bounded scope (one starting URL, crawl to
completion), per-host rate limit, robots.txt honored, no re-crawl,
single-tenant, URL list only.

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
                  │ enqueue seed fetch(root)    │ paginate URLs
                  ▼                             ▼
        ┌────────────────────┐          ┌──────────────┐
        │   fetch queue      │          │   Postgres   │
        │  (per-host shard,  │          │   + Redis    │
        │   Kafka/SQS)       │          │  (visited)   │
        └────────┬───────────┘          └──────▲───────┘
                 │                             │
           workers pull                 SADD url to visited
                 ▼                      INSERT url to crawl
        ┌──────────────────┐                   │
        │  fetcher fleet   ├───────────────────┘
        │  (stateless,     │
        │   autoscaled)    ◄─── enqueue child fetches per discovered URL
        └──────┬───────────┘             ▲
               │ get_urls(page)          │
               └─────────────────────────┘
```

Six components:

- **API tier** — stateless HTTP. `POST /crawl`, `GET /crawl/{id}`,
  `GET /crawl/{id}/urls` (paginated).
- **Fetch queue** — Kafka / SQS / Redis Streams. **Sharded by hostname**
  (critical for politeness) — every URL on `news.site.com` goes to one
  queue partition processed by one worker at a time per host.
- **Fetcher fleet** — stateless workers. Pull one URL job at a time,
  fetch the page, parse links, enqueue child fetches.
- **Visited set** — Redis set keyed by `crawl_id`. The de-dupe
  oracle: workers `SADD` before enqueueing; `SADD` returns whether the
  URL was new, so duplicate enqueues are short-circuited.
- **Crawl results** — Postgres table per `crawl_id` listing every
  successfully fetched URL.
- **Robots.txt cache** — Redis keyed by host. Refreshed every N hours.

### API surface

```http
POST /crawl
  body:     { "start_url": "http://news.site.com/home", "request_id": "uuid" }
  response: 201 { "crawl_id": "abc..." }
  Idempotent on request_id — same request returns existing crawl_id.

GET /crawl/{crawl_id}
  response: 200 {
    "crawl_id":   "...",
    "status":     "pending"|"running"|"complete"|"failed"|"cancelled",
    "urls_visited": 12345,
    "pages_fetched": 12340,
    "errors":     5,                  # fetches that 4xx/5xx'd
    "pending_jobs": 67,               # ★ termination counter
    "started_at": "...", "completed_at": "..."
  }

GET /crawl/{crawl_id}/urls?cursor=<opaque>&limit=100
  response: 200 {
    "urls": ["http://news.site.com/home", ".../article-1", ...],
    "next_cursor": "..."     # null when done
  }

DELETE /crawl/{crawl_id}
  response: 204
  Marks the crawl cancelled; workers check status before fetching.
```

### Database / store schemas

```sql
-- One row per crawl request.
CREATE TABLE crawls (
    crawl_id      uuid PRIMARY KEY,
    request_id    uuid UNIQUE NOT NULL,
    start_url     text NOT NULL,
    target_host   text NOT NULL,
    status        text NOT NULL,
    pending_jobs  int  NOT NULL DEFAULT 0,    -- ★ termination counter
    urls_visited  bigint NOT NULL DEFAULT 0,
    pages_fetched bigint NOT NULL DEFAULT 0,
    errors        bigint NOT NULL DEFAULT 0,
    started_at    timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,
    error         text
);

-- One row per crawled URL (the result set).
CREATE TABLE crawl_urls (
    crawl_id  uuid NOT NULL,
    url       text NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    http_status int,
    PRIMARY KEY (crawl_id, url)
) PARTITION BY HASH (crawl_id);
```

```redis
# Visited set — atomic de-dupe oracle.
SADD crawl:{crawl_id}:visited <url>           # returns 1 if new, 0 if dup

# Robots.txt cache — refreshed every 6h.
SET  robots:{hostname}  "<robots.txt content>"  EX 21600

# Per-host rate limit token bucket (see token_bucket).
HSET ratelimit:{hostname}  tokens <n>  last_fill <ts>
```

Three storage targets, each with a distinct job:

- **`crawls` / `crawl_urls`** in Postgres — the durable result set;
  paginates millions of URLs cleanly.
- **`crawl:{id}:visited`** in Redis SET — the in-memory de-dupe oracle;
  `SADD` returns whether the URL was new in O(1). Postgres-backed
  uniqueness check would be possible but ~100× slower in the hot path.
- **`robots:` / `ratelimit:`** in Redis — short-lived caches.

### ★ The recursive self-spawning fetcher ★

The single critical insight: each fetch is one job; child URLs become
child jobs. Same pattern as `file_duplicates`'s directory-walk Tier 4.

```
worker.process(job{crawl_id, url}):

    if crawls.status(crawl_id) in ('cancelled', 'failed'):
        ack; return.

    BEGIN TRANSACTION
        # Per-host rate-limit gate (token bucket — see token_bucket).
        if not rate_limit.try_acquire(hostname(url)):
            requeue job with delay; ack; return.

        # Politeness: robots.txt
        if not robots_cache.allowed(url):
            COMMIT; ack; return.

        # Fetch + parse.
        try:
            page = http_fetch(url, timeout=10s)
            links = parse_links(page)
        except HTTPError as e:
            UPDATE crawls SET errors = errors + 1,
                              pending_jobs = pending_jobs - 1
                          WHERE crawl_id = ?
            COMMIT; ack; return.

        # Record this URL as fetched.
        INSERT crawl_urls(crawl_id, url, http_status) ON CONFLICT DO NOTHING
        UPDATE crawls SET pages_fetched = pages_fetched + 1 WHERE crawl_id = ?

        # Hostname filter + de-dupe + enqueue children.
        children = 0
        for link in links:
            if hostname(link) != target_host: continue
            if not redis.SADD(f"crawl:{crawl_id}:visited", link): continue
            enqueue job{crawl_id, link}
            children += 1

        UPDATE crawls
           SET urls_visited = urls_visited + children,
               pending_jobs = pending_jobs + children - 1,
               status = CASE WHEN pending_jobs + children - 1 = 0
                             THEN 'complete' ELSE status END,
               completed_at = CASE WHEN pending_jobs + children - 1 = 0
                                   THEN now() ELSE completed_at END
         WHERE crawl_id = ?
    COMMIT

    ack the job.
```

Two properties:

- **The recursion is queue subdivision.** Each level of the link graph
  is a wave of jobs. The fleet processes them in parallel; `pending_jobs`
  tracks total outstanding work; termination is the counter hitting zero.
- **`SADD` *before* enqueue is the de-dupe.** Redis' SADD is atomic
  and returns whether the value was new. If two workers discover the
  same URL simultaneously, only one's SADD returns "new" → only one
  enqueue → no duplicate fetch.

The ordering `SADD → enqueue` (not the reverse) matters: if you enqueue
first and SADD later, a partial failure can lose URLs. SADD first
guarantees that an URL "claimed" in the visited set is durably
recorded before any worker can start its fetch.

### Termination — same counter as file_duplicates

`crawls.pending_jobs` increments by the count of newly-enqueued children
*before* decrementing self. The transaction protects the invariant. When
the counter hits zero, the crawl flips to `complete`. The whole
mechanism is the durable analogue of `queue.Queue.join()`.

Watchdog: if `pending_jobs > 0` and the row hasn't been updated in N
minutes, the crawl is wedged (lost job, dead worker) → retry or fail.

### Per-host politeness — the politeness gate

Web crawling has a unique constraint absent from `file_duplicates`:
**you must not hammer any one host**. A polite crawler fetches at most
~1 page/sec/host. The fetch queue is **sharded by host** so each host
maps to one queue partition; the partition's consumer (one worker at a
time per host) gates fetches through a token bucket. A high-fanout host
(news.site.com has 100K linked pages) processes its queue serially; a
sparse host processes immediately.

This is the `token_bucket` algorithm dropped into the fetcher's hot
path — at scale, with one bucket per hostname in Redis.

### Idempotency

- **Client → API:** `request_id` unique key on `crawls`. Repeated POST
  returns existing `crawl_id`.
- **SADD on `visited`:** atomic; duplicates short-circuit at the
  Redis call.
- **`crawl_urls` PK `(crawl_id, url)`** with `ON CONFLICT DO NOTHING` —
  re-delivered fetches don't double-insert.
- **`pending_jobs` arithmetic** lives in one transaction — partial
  failures don't corrupt the counter.

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Worker crash mid-fetch | visibility timeout → re-delivered → idempotent writes absorb it |
| HTTP 4xx/5xx on a page | counted as `errors`; URL still marked visited; children not enqueued (no parsed body) |
| HTTP timeout | worker NACKs after the queue's visibility-timeout-aligned local timeout; re-tries up to N times then marks the URL `errored` |
| `robots.txt` says disallow | URL skipped (visited but not fetched); children not enqueued |
| Spider trap (infinite same-host URL space, e.g. calendar URLs going to year 9999) | per-crawl URL count cap; pattern detection (URLs with identical structure but varying integer); manual blocklist |
| Hot host (one host's queue backs up) | autoscale per-host partition workers; bump the host's bucket capacity if the host welcomes it |
| Visited-set OOM in Redis | use a probabilistic set (Bloom filter) instead — accepts a small false-positive rate (some URLs falsely "already visited" → unfetched) for a 10× memory reduction |
| Lost job in queue | `pending_jobs` never reaches zero → watchdog after N min → retry or fail |
| Client cancels | DELETE → `crawls.status = 'cancelled'` → workers skip jobs for cancelled crawls |

### Scaling levers

- **Per-host queue partitions.** Hostname → partition; one consumer
  per partition guarantees the politeness rate.
- **Bloom filter for visited.** Trades ~1% false-positive rate (some
  URLs erroneously marked visited → never fetched) for ~10× memory
  reduction. Used at Google scale.
- **Streaming compression of crawl_urls.** Per-crawl partition; old
  crawls archived to cheap object storage via `pg_dump`.
- **Multi-region.** Pin a crawl to the region near its target host;
  reduces cross-ocean fetch latency.
- **Adaptive bucket sizing.** Hosts that 429 us back get their bucket
  capacity halved; hosts that respond fast get bumped.

### What this design defers

- **Continuous crawling.** This is a one-shot crawl that terminates.
  Production crawlers (Googlebot) re-queue URLs on a freshness
  schedule, never terminate.
- **JavaScript rendering.** Modern pages need a headless browser
  (Puppeteer / Playwright) to discover links; the fetch worker becomes
  a fleet of browser processes with much higher per-page cost.
- **Content extraction.** This design returns URLs only. Real
  crawlers also extract title / body / metadata into a content store.
- **PageRank / link graph.** This design just lists URLs. Building
  the directed link graph for ranking is a separate index pipeline.
- **Cross-crawl dedup.** Two crawls of the same host re-fetch
  everything; an "if this URL was crawled in the last N days, skip"
  optimization is a content layer above this design.

### Simulation → production mapping

The single-process `QueueCrawler` mirrors this architecture piece for
piece — what changes is what holds the state and how parts communicate:

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `queue.Queue` | Kafka / SQS / Redis Streams, partitioned by hostname |
| `threading.Thread` worker pool | a fleet of stateless fetcher processes |
| `set()` for visited | Redis SET keyed by crawl_id (or Bloom filter at scale) |
| `set.add()` returning `False` on dup | `SADD` returning `0` |
| `queue.join()` | `crawls.pending_jobs` counter, atomically inc/dec |
| in-process `crawl()` call | `POST /crawl` + `GET /crawl/{id}/urls` |
| (none) | per-host token bucket for politeness |
| (none) | robots.txt cache |

## Running the tests

```sh
uv run pytest Algorithms/web_crawler/tests/ -q
```
