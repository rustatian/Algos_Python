# Token Bucket

A token-bucket rate limiter. The bucket holds up to `max_capacity` tokens
and earns `fill_rate` tokens per second; callers acquire tokens to pace
rate-limited work, and either fail fast or wait when it runs dry. There
is no background filler thread — tokens are computed from `elapsed ×
fill_rate`, capped at `max_capacity`, off a `time.monotonic()` clock, so
the bucket is immune to wall-clock jumps. This is the canonical pattern
used in Guava's `RateLimiter`, Bucket4j, and Resilience4j.

Modeled on the classic "distributed rate limiter" interview question — the
token bucket is the canonical rate-limiting algorithm. Related concurrency
drills: LeetCode #1188 (Bounded Blocking Queue), #1117 (Building H2O), and
#362 (Hit Counter).

## Tiers

| Tier | Class | Concurrency | Tokens | API |
|------|-------|-------------|--------|-----|
| 1 | `SimpleTokenBucket` | none — single-threaded | `int` count | `try_acquire(n) -> bool` |
| 2 | `ConcurrentTokenBucket` | `threading.Lock` + `Condition` | `int` count | `try_acquire(n) -> bool`, `acquire(n) -> None` (blocking) |
| 4 | `TokenBucket` | `asyncio` + reservations | `deque[int]` values | `try_acquire(n)`, `reserveN(...)`, `get(n)`, `fill()` |

(No Tier 3 — the natural "blocking-via-Condition" tier is folded into
Tier 2, and coroutine-based concurrency is what Tier 4 introduces.)

Tier 1 is the Java-interview baseline: Guava `RateLimiter` / Bucket4j
*without* the synchronized block — single-threaded, fail-fast, lazy
refill. Tier 2 adds the synchronized block back: a `threading.Lock`
serializes `try_acquire` so concurrent callers can't overspend, and a
`threading.Condition` lets `acquire(n)` block until the bucket has
earned enough. Tier 4 takes the full systems-interview shape: token
*payloads* (a `deque` of random ints in [1, 100]), cooperative
async/await blocking via two `asyncio.Condition`s, and the
`reserveN`/`Reservation` future-claim primitive for rate-limiter clients
that need a deterministic "this is how long you'll wait" answer up front.

## API (Tier 4 — full async)

`try_acquire` and `reserveN` refill the bucket lazily themselves; `fill`
and `get` are an explicit producer / consumer pair.

| Method | Blocks? | Behavior |
|--------|---------|----------|
| `try_acquire(n)` | no | fail-fast: take `n` tokens now, or return `[]` |
| `reserveN(n, max_wait)` | no | reserve `n` future tokens (a `Reservation`); `None` if the wait would exceed `max_wait` |
| `fill()` | when the bucket is full | producer: add the tokens earned since the last refill |
| `get(n)` | until `n` are available | consumer: wait for `fill()`, then take `n` |

`get` rejects `n ≤ 0` or `n > max_capacity` up front — either request could
never be satisfied and would otherwise block forever.

`reserveN` returns a `Reservation`: `await wait()` sleeps until the tokens
are ready and then consumes them, `await cancel()` drops the claim, and
`delay()` reports the seconds still to wait. Reserved-but-unconsumed tokens
are counted separately, so no caller is handed a token another has already
reserved — no double-spending.

## Distributed extension — the system-design follow-up

The in-process `TokenBucket` is a single-machine primitive. The system-design
follow-up is what `learn.html` calls out explicitly:
*"distributed via Redis + Lua script (atomic check-refill-deduct)."* The
architectural question shifts from "how do I coordinate cooperative
coroutines around one bucket?" to "how do I rate-limit a million
requests per second per key, across a fleet of stateless app servers,
without any one of them desyncing or double-spending?"

### Opener — clarifying questions

- **Sync or async rate-limit decision?** Sync (the limiter blocks the
  caller until tokens are available) or async (the limiter returns
  immediately with 429-or-allowed, no waiting)? Async is the standard
  for HTTP APIs; sync only makes sense for back-pressure on internal
  queues.
- **One bucket per key?** Per user? Per (user, endpoint)? Per API key?
  Drives the cardinality of the bucket store.
- **Strict or eventual consistency?** Strict (every server sees the
  same bucket state instantly) requires a central atomic store like
  Redis. Eventual (per-server local buckets + reconciliation) trades
  accuracy for latency.
- **Failure semantics?** When the bucket store is unreachable, fail
  *open* (allow all traffic — system stays available, attackers get a
  free ride) or *closed* (deny all traffic — system protects itself,
  legitimate users get errored)?
- **Multi-region?** Per-region buckets or globally synchronized?

Assumed for the design below: async (return 429 or allow, no wait),
per-key bucket, strict consistency (Redis + Lua), fail-open on Redis
outage with a circuit breaker, single-region.

### The shape — a thin service over Redis

```
                              ┌──────────┐
   client request ──────────► │  app srv │  needs to rate-limit user X
                              │ (any)    │       │
                              └──────────┘       │ EVALSHA <lua_script>
                                                 │   <key>:user:X
                                                 │   <args>: now, n, cap, rate
                                                 ▼
                                       ┌──────────────────┐
                                       │   Redis cluster  │
                                       │  bucket state    │
                                       │  HSET / HGET     │
                                       └──────────────────┘
                                                 │
                                                 ▼
                          response: { allowed: bool, retry_after: ms }
```

Three components:

- **App servers** — stateless. Don't hold bucket state. Every rate-limit
  decision is one Redis call.
- **Redis cluster** — sharded by bucket key (consistent hashing). Each
  shard owns a subset of buckets. Holds the bucket state and runs the
  atomic Lua check-refill-deduct script.
- **(Optional) Rate-limit service** — a thin wrapper around Redis,
  exposes `POST /acquire {key, n}`. Caches the Lua script SHA, handles
  retries, emits metrics. Could be skipped — apps can call Redis
  directly.

### API surface

```http
POST /acquire
  body:     { "key": "user:42:endpoint:/charge", "n": 1 }
  response: 200 { "allowed": true,  "remaining": 99 }
           OR
            429 { "allowed": false, "retry_after_ms": 1200, "remaining": 0 }

  semantics: one atomic decision. Returns immediately; no waiting.
             Client retries after `retry_after_ms` if denied.

POST /acquire
  body:     { "key": "user:42:endpoint:/charge", "n": 5, "max_wait_ms": 2000 }
  response: 200 (after up to 2s of waiting)
           OR
            408 if the wait would exceed max_wait_ms

  semantics: optional "wait up to N ms" variant; equivalent to TokenBucket's
             reserveN. Server-side wait costs a held connection; rarely worth it.
```

`POST /acquire` is one call per rate-limit decision. At 1M req/sec
aggregate, that's 1M Redis calls/sec — Redis Cluster handles this on
modest hardware.

### ★ The Redis + Lua atomic check-refill-deduct ★

The critical insight `learn.html` flags. The whole rate-limit decision —
read state, refill from elapsed time, check capacity, deduct, write back
— must be **atomic with respect to concurrent app servers**. If two
app servers issue a `HGET` then independently compute `tokens -= n` then
`HSET`, the second's write overwrites the first's — classic lost-update,
plus double-spending of tokens.

Lua scripts run **atomically** inside Redis. The whole script executes
without interleaving any other command. So the entire decision becomes
one Redis call.

```lua
-- KEYS[1] = bucket key, e.g. "rl:user:42"
-- ARGV[1] = now_ms (server clock — Redis can return its own TIME)
-- ARGV[2] = n (tokens requested)
-- ARGV[3] = max_capacity
-- ARGV[4] = fill_rate (tokens per ms)
-- returns: {allowed (0 or 1), remaining tokens, retry_after_ms}

local state    = redis.call('HMGET', KEYS[1], 'tokens', 'last_fill')
local tokens   = tonumber(state[1]) or ARGV[3]    -- bucket starts full
local last     = tonumber(state[2]) or tonumber(ARGV[1])

-- Refill from elapsed time (same math as the in-process TokenBucket).
local elapsed  = tonumber(ARGV[1]) - last
local refilled = math.min(ARGV[3], tokens + elapsed * ARGV[4])

if refilled >= tonumber(ARGV[2]) then
    -- Allow: deduct n.
    refilled = refilled - tonumber(ARGV[2])
    redis.call('HMSET', KEYS[1],
               'tokens',    refilled,
               'last_fill', ARGV[1])
    redis.call('PEXPIRE', KEYS[1], 60000)         -- TTL keeps the keyspace bounded
    return {1, refilled, 0}
else
    -- Deny: compute retry-after = (needed - have) / rate.
    local short_by = tonumber(ARGV[2]) - refilled
    local retry_ms = math.ceil(short_by / ARGV[4])
    return {0, refilled, retry_ms}
end
```

Three properties:

- **Atomicity.** The whole read-compute-write is one Lua execution,
  invisible to other commands. No lost updates, no double-spend.
- **Self-initialization.** First call to a bucket key sees `state = nil`,
  initializes to full capacity, then deducts. No separate "create
  bucket" step needed.
- **Self-cleanup via TTL.** Idle bucket keys expire automatically; the
  keyspace doesn't grow forever. A 60-second TTL is comfortably longer
  than any reasonable refill time.

The script is loaded once per app server via `SCRIPT LOAD`, then invoked
by `EVALSHA <sha>` — saves bandwidth on the hot path.

### Storage on Redis

```redis
# Per-bucket state — hash with two fields.
HSET rl:user:42:endpoint:/charge  tokens 99  last_fill 1716480000123
PEXPIRE rl:user:42:endpoint:/charge 60000

# Sharded by key across the Redis cluster. ~24 bytes per bucket.
# 100M active keys = ~2.4GB across the cluster. Fits.
```

The key includes everything that scopes the rate limit: user, endpoint,
maybe IP, maybe API key. Cardinality of the key space is the storage
sizing knob.

### Why not "client-local buckets + reconciliation"?

A common bad answer: each app server holds a local bucket; servers
gossip / reconcile via Redis periodically. Looks faster — no Redis call
per request.

Why it fails: **rate-limit accuracy is the entire point.** Local
buckets independently see ~1/N of the traffic, so each happily allows
its share — but together they allow N× the rate. The whole system
overshoots the limit by N×. The only fix is to size each local bucket
at 1/N of the global limit, which falls apart the moment one app
server takes more traffic than the others (which is always).

The reconciliation latency is the leak. Redis + Lua makes the decision
the single source of truth — one Redis call's latency (~0.5ms within a
DC) is the cost of correctness.

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| Redis shard unavailable | rate-limit calls to that shard's keys fail | **fail open** — allow requests when Redis is down (with a circuit breaker → fail closed if Redis is down for >10 sec); emit a metric for SRE alerting |
| Redis cluster partial outage | some keys fail, others succeed | per-key fallback as above |
| Clock skew between Redis and app servers | minor refill drift | use `ARGV[1] = redis.call('TIME')` inside the script — Redis's own clock is the authority |
| Hot key (one user dominates one shard) | shard saturates on that key's traffic | shard the hot key further: rewrite `key` as `{user:42}-{random 0..7}` — Redis Cluster hash-tag pins the 8 sub-keys to one shard, but the local bucket math splits eight ways |
| Bursty legitimate traffic (heavy hitter) | reservation latency spikes | use `reserveN` variant — caller waits up to `max_wait_ms`; client gets a 408 if patience runs out |
| Adversarial client (deliberately overrunning) | rate-limit denies; client retries faster | layer a second rate limit on `retry_after_ms` violations (a per-IP "you ignored the retry" counter) |
| Bucket reset on Redis restart | all buckets start full again — a brief moment of free traffic | rare; mitigated by persistence (Redis AOF / RDB) or graceful failover |

### Scaling levers

- **Shard buckets by key.** Redis Cluster's consistent hashing does
  this automatically. Adding shards re-routes ~1/N of buckets; in-flight
  buckets in the moved slot are TTL'd away within a minute.
- **Cache the script SHA per app server.** `EVALSHA` is one round-trip
  shorter than `EVAL`.
- **Multi-tier limits.** Per-user + per-endpoint + per-IP, each a
  separate Redis call. Order matters: cheapest deny first (IP) →
  expensive deny last (per-user-per-endpoint).
- **Burst capacity** — let the bucket exceed `max_capacity` briefly,
  then drain. Implemented by adding a "burst_capacity" field and
  separating "earned" from "burst" accounting in the Lua script.
- **Multi-region.** Per-region buckets if customers are region-pinned
  (most are). Global buckets require synchronous cross-region calls;
  rarely worth the latency.

### What this design defers

- **Sliding-window rate limits.** The token bucket allows uniform-rate
  traffic; sliding-window limiters cap the *number* of requests in any
  window, which is stricter but more expensive (uses the `hit_counter`
  shape, not the token-bucket shape). Pick based on whether bursts
  are okay.
- **Concurrent connection limits.** Per-key counter + decrement on
  disconnect; not modeled here.
- **Adaptive rate limits.** Limits that change with system load
  (when downstream services are unhealthy, tighten limits). A control
  loop over the bucket's `fill_rate`.
- **Per-tenant rate plans.** Each tenant has its own `(max_capacity,
  fill_rate)`. Stored alongside the tenant config, looked up on each
  request.

### Simulation → production mapping

The in-process `TokenBucket` and the distributed version share an
algorithm (token bucket math) but use very different concurrency
primitives:

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `asyncio.Lock` | Lua script atomicity inside Redis |
| `asyncio.Condition` (for `get` / `fill`) | client polls with `retry_after_ms` returned by the API |
| in-process method call | `POST /acquire` → Redis EVALSHA → response |
| in-process `_tokens` / `_last_fill` floats | Redis HSET fields per bucket key |
| `time.monotonic()` | `redis.call('TIME')` inside the Lua script |
| `try_acquire` returning `[]` | `429 Too Many Requests` |
| `reserveN` + `delay()` | client-side retry after `retry_after_ms` |

The algorithm transfers identically; the locking primitive and the
clock both move into Redis, which becomes the single source of truth
for every rate-limit decision in the fleet.

## Running the tests

```sh
uv run pytest Algorithms/token_bucket/tests/ -q
```
