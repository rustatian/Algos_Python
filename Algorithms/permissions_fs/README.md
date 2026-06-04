# Permissions in File System

Decide whether a user has access to a given folder in a file system tree.
A user has *explicit access* to some set of folders; a folder is
**accessible** if it is in that set OR any of its ancestors is. Access
inherits from ancestors down to descendants.

Modeled on the classic "Permissions in File System" onsite interview
question. Related LeetCode tree-traversal references: #1376 (Time to
Inform Employees), #1466 (Reorder Routes), #797 (All Paths Source to
Target).

## Problem

A file system is a tree of folders. Each folder has at most one parent;
the root maps to `None`. Two write operations and one read:

- `add_access(folder)` — grant explicit access.
- `remove_access(folder)` — revoke explicit access (no-op if not granted).
- `has_access(folder) -> bool` — True iff `folder` itself or any
  ancestor is currently in the access set.

The follow-up the interview commonly asks: **find redundant access
entries**. A grant on folder `X` is redundant if some ancestor of `X` is
also granted (`X` inherits anyway). Compute the **minimal cover** — the
smallest set that gives the same `has_access` result everywhere.

## Tiers

| Tier | Class                    | Strategy                                                    | The lesson                                                                                 |
|------|--------------------------|-------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| 1    | `SimplePermissions`      | walk parent pointers from folder to root                    | the algorithm — O(depth) per query, no precomputation                                      |
| 2    | `CachedPermissions`      | memoize has_access; invalidate descendants on change        | cache + selective invalidation — knowing *what* to invalidate is the trap                  |
| 3    | `MinimalPermissions`     | adds `minimal_cover()` via BFS-mark                         | tree DP — duality between "walk up to find an ancestor" and "BFS down to mark descendants" |
| 4    | `DistributedPermissions` | sharded ACL service across users + invalidation propagation | the system-design follow-up — billions of files, ACL on every read                               |

All four tiers expose the same write/read surface:

```python
p = SimplePermissions(parent_map)
p.add_access("/usr")
p.remove_access("/usr")
p.has_access("/usr/local")  # -> bool
```

Tier 3 adds `minimal_cover() -> set[str]` returning the redundancy-free
access set.

Each tier answers the previous one's weak spot. Tier 1 is O(depth) per
query — fine for shallow trees, expensive at production scale where the
same folder is queried many times. Tier 2 caches results, but
permission changes invalidate every descendant of the changed folder —
the BFS-down-to-invalidate is itself a tree-walk lesson. Tier 3 turns
the question inside out: instead of asking "do I have access?" on each
query, precompute the minimal cover and ask "is this in or below a
cover element?" Tier 4 leaves the single machine: ACL state shards by
user, hot paths get a per-user cache, and invalidation rides a pub-sub
topic per changed folder.

## Tier 4 architecture — the system-design follow-up

The first three tiers are single-process. The system-design
follow-up takes the next step: *how do you serve has_access at billions
of files, on the hot path of every file read, in under a millisecond?*
The architectural question shifts from "what's the cheapest in-process
algorithm?" to "where does the answer live, how does it stay correct
when permissions change, and what happens when the ACL service is
slow or unreachable?"

This shape is steady-state — not a job-shaped problem like
`file_duplicates`, not a scatter-gather problem like `hit_counter`. The
defining characteristic is the **read-to-write ratio**: permissions
change rarely (a manual grant from a user, an admin policy push) but
are checked on *every* file read. That asymmetry — millions of reads
per write — is what drives the design.

### Opener — clarifying questions

- **Read vs write ratio?** Confirm the asymmetry. If reads dominate
  writes 1000:1 or more, optimize the read path aggressively (edge
  cache); if reads and writes are comparable, you can't sustain edge
  caches.
- **Latency SLO?** Sub-millisecond for has_access? Drives whether
  the ACL service can be on the critical path of every request.
- **Group/role inheritance?** Real ACLs are not just per-user-per-folder
  — a user inherits access via groups they belong to. Trees of groups,
  not just trees of folders. Drastically expands the invalidation
  graph.
- **Sharing model?** Does a folder have one owner (Unix-style) or
  many independent grants (cloud-storage-style sharing)? Drives the storage
  shape of the access set.
- **Multi-tenant?** Tenant isolation in storage; per-tenant rate limits.
- **Fail mode under outage?** Fail-closed (deny on uncertainty —
  secure but unavailable) or fail-open (allow on uncertainty — available
  but risky)? Permissions almost always fail-closed.
- **Auditing?** Every has_access call logged? Drives the volume of
  the audit pipeline.

Assumed for the design below: 1000:1 read/write ratio, sub-ms has_access,
group-based inheritance, multi-grant sharing, single-tenant, fail-closed,
audit every grant/revoke (not every read).

### Block diagram

```
   client requests ─► app server  ──ACL check──►  edge cache  (per app srv,
                          │                            │       per-user materialized
                          │ on miss / on invalidation  │       minimal cover)
                          │                            │
                          ▼                            ▼
                  invalidation             ┌──────────────────┐
                   pub-sub topic ─────────►│   ACL service    │
                  (per affected user)      │   (sharded by    │
                          ▲                │     user_id)     │
                          │                └────────┬─────────┘
                          │                         │
                  ┌───────┴────────┐                ▼
                  │ admin / user UI│        ┌──────────────────┐
                  │ for grants     │        │   Postgres /     │
                  └───────┬────────┘        │   sharded ACL    │
                          │                 │   store          │
                          └─POST /grant────►└──────────────────┘
```

Five components:

- **App servers** — the hot-path callers of has_access. Hold an
  in-memory **edge cache**: per-user, the materialized minimal cover
  (the result of Tier 3's `minimal_cover()`).
- **ACL service** — sharded by `user_id`. Each shard owns a slice of
  the user space and serves grant/revoke writes plus
  "give-me-this-user's-minimal-cover" reads.
- **Sharded ACL store** — Postgres or a key-value store. Per-user
  rows: `(user_id, granted_folder)`. The minimal cover is computed
  on read (or precomputed and cached).
- **Pub-sub topic** — a topic per *affected* user. App servers
  subscribe; whenever a user's permissions change, the ACL service
  publishes an invalidation message; subscribed edges drop their
  cached entry for that user.
- **Admin UI / grant API** — entry point for grants/revokes.

### API surface

```http
# Hot path — called per file read.
GET /access/{user_id}/{folder_path}
  response: 200 { "allowed": true|false }
  Sub-millisecond from the edge cache; on miss, fans out to the ACL
  service (one hop) and then re-caches.

# Bulk shape — used to warm the edge cache.
GET /access/{user_id}/cover
  response: 200 {
    "cover":  ["/usr/share", "/home/alice", ...],   # minimal cover
    "version": 47                                    # monotonic counter
  }
  App servers fetch once per user-session-on-this-server, then answer
  every subsequent has_access locally.

# Write side — rare, but the system's correctness hinges on it.
POST /access/{user_id}/grant
  body:     { "folder": "/usr/share" }
  response: 201 { "version": 48 }
  Synchronously updates the ACL store; publishes invalidation; new
  version returned.

POST /access/{user_id}/revoke
  body:     { "folder": "/usr/share" }
  response: 200 { "version": 49 }

GET /access/{user_id}/audit?since=<ts>&limit=N
  response: 200 { "events": [ {ts, op, folder, actor}, ... ] }
  Audit log of grants/revokes. Used by compliance, NOT on the hot path.
```

Two access patterns explicitly separated:

- **Point query** `GET /access/{user}/{folder}` — for callers that
  don't want to hold the whole cover; one RPC per check. Slow path.
- **Bulk fetch** `GET /access/{user}/cover` — pull the minimal cover
  once, answer locally. Fast path that the edge cache uses.

### Data model

```sql
-- One row per (user, granted folder). The raw access set.
CREATE TABLE access_grants (
    user_id     uuid NOT NULL,
    folder      text NOT NULL,
    granted_by  uuid NOT NULL,           -- who issued the grant
    granted_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, folder)
) PARTITION BY HASH (user_id);

-- The per-user cover and version. Cached materialization of the
-- minimal cover; populated by the ACL service from access_grants on
-- write, served to the edge on read.
CREATE TABLE access_cover (
    user_id   uuid PRIMARY KEY,
    cover     text[] NOT NULL,           -- the minimal-cover folder set
    version   bigint NOT NULL,           -- bumps on every change
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- The folder tree — typically lives elsewhere (the file metadata
-- service), exposed to the ACL service via RPC or replicated.
-- Needed to compute the minimal cover (parent → children walk).
CREATE TABLE folder_tree (
    folder   text PRIMARY KEY,
    parent   text                         -- NULL for root
);

-- Audit log — append-only.
CREATE TABLE access_audit (
    event_id    uuid PRIMARY KEY,
    user_id     uuid NOT NULL,
    op          text NOT NULL,            -- 'grant'|'revoke'
    folder      text NOT NULL,
    actor       uuid NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON access_audit (user_id, ts);
```

Four tables, each with a distinct job:

- **`access_grants`** — the durable source of truth. Partition by
  `user_id` so a user's grants live together physically.
- **`access_cover`** — the read-optimized cache of the minimal cover.
  Computed from `access_grants` on write; served to the edge on read.
  Without it, every cover fetch walks `access_grants` and re-computes.
- **`folder_tree`** — the structural tree needed by the cover
  computation. Often a *projection* of the file metadata service, not
  authoritative here.
- **`access_audit`** — append-only event log for compliance. NEVER
  on the hot read path.

### ★ Invalidation propagation — the critical insight ★

The whole system is correct only if **invalidation reaches every edge
cache faster than the next read** after a permission change. This is
where the design earns or loses its security guarantees.

```
grant /usr/share to user 42:

  1. POST /access/42/grant → ACL service shard for user 42
  2. ACL service:
       BEGIN TRANSACTION
         INSERT access_grants(42, "/usr/share", ...)
         recompute minimal cover for user 42 from access_grants
         UPDATE access_cover SET cover=..., version=version+1
         INSERT access_audit(...)
       COMMIT
  3. PUBLISH "user/42/permissions_changed" {version: 48} to pub-sub
  4. App servers subscribed to user/42 receive the message:
       drop cache entry for user 42
       (next has_access call for user 42 will fetch fresh cover)
  5. Return 201 to admin caller.
```

Three properties of this flow that are non-obvious:

- **The COMMIT happens before the publish.** If publish fired first,
  a subscriber's race could fetch the cover from the cache (which is
  refreshed from `access_cover`), see the *new* cover, but the COMMIT
  hadn't landed yet, so a concurrent reader on a different shard
  could see the old grant set. Publishing after commit guarantees
  the new state is durably visible when subscribers refresh.
- **The publish is to a per-user topic.** A single global "permissions
  changed" topic would force every app server to refresh every user's
  cover on any change — quadratic in user count. Per-user topics
  scale linearly with the number of *changes*, which is rare.
- **The version number is monotonic.** App servers compare `version`
  in the message to the cached version; only refresh if the cached
  version is older. Solves the "out-of-order delivery" problem —
  a delayed older message can't roll forward state.

### Idempotency

- **Client → API:** request IDs on POST /grant — duplicate calls
  return the existing version. `INSERT ... ON CONFLICT DO NOTHING` on
  `(user_id, folder)`.
- **Invalidation → edge:** versioned messages. Edge caches keep the
  latest seen version; a re-delivered older message is dropped.
- **Cover recomputation:** deterministic from `access_grants`;
  re-running it yields the same result. Safe to retry under transient
  failures.

### Failures and edge cases

| Failure | Effect | Mitigation |
|---------|--------|------------|
| ACL service shard unreachable | edge cache miss → no fallback | **fail-closed** — return `access_denied` on uncertainty (security default); emit an SRE metric |
| Edge cache miss + ACL shard slow | hot-path latency spikes | per-RPC budget (~5 ms); on timeout, fail-closed |
| Pub-sub delivery lost | edge caches keep stale data | **TTL on the edge cache** (~60 s) backs up invalidation; worst case, a revoked permission lingers up to TTL |
| User's cover too large to fit one message | invalidation message can't carry it | publish only `{version: N}` — edges fetch the full cover from the ACL service on refresh |
| Permission updated thousands of times per second on one user | cover recomputation thrashes | rate-limit cover recomputation per user (~10 Hz); batch up small windows |
| Group membership changes | every member's cover potentially changes | groups are first-class: invalidate every member of the group, not just one user |
| ACL store consistency lag (read replica behind) | edge sees the new cover, store says no grant | always read `access_grants` from the primary on the write path; replicas only for reads |
| Audit log fails to write | grant/revoke fails the transaction | audit is part of the same transaction — no audit, no grant |

### Scaling levers

- **Edge cache TTL.** Set against the invalidation reliability —
  shorter TTL is more conservative, more refresh load on the ACL
  service. 60 s is typical.
- **Cover-size cap.** Users with hundreds of thousands of grants
  bloat the cover. Cap cover size; for users exceeding it, fall back
  to point-query mode (slow but correct).
- **Bloom filter at the edge.** For has_access, a Bloom filter over
  the cover gives a fast "definitely no access" check at the cost of
  false positives (which fall through to the cache lookup). Cuts
  ACL-service round trips on no-access cases.
- **Group inheritance via materialization.** Don't recompute group
  membership on every cover build — precompute "user X's group set"
  as a separate cached materialization, invalidated when groups change.
- **Per-region ACL service.** Pin a user to their home region's
  ACL service; cross-region access pays a longer round trip.
- **Sharding by hash(user_id).** Adding shards re-routes ~1/N of
  users; rebalance with consistent hashing.

### What this design defers

- **Group inheritance trees.** Real ACLs let groups contain groups.
  The cover computation becomes a graph walk, not a tree walk. Same
  shape as Tier 3's BFS, but over the group DAG.
- **Time-bounded grants** ("share this folder until next Tuesday").
  Add `expires_at` to `access_grants`; recompute cover on expiry
  events (a scheduled job).
- **Negative grants** ("deny X access even though their group has it").
  Most production ACLs support deny rules; the cover algorithm has
  to handle a separate deny list.
- **Cross-tenant sharing** (sharing with users outside your org).
  Adds a layer of trust boundary checks.
- **Per-action permissions.** Real ACLs distinguish read / write /
  share / delete; this design models a single "access" predicate.
  Generalize by keying grants on `(user, folder, action)`.

### Simulation → production mapping

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `SimplePermissions` walk-up | edge cache hit on the per-user minimal cover |
| `CachedPermissions` cache | edge cache, but per-user-per-app-server, not per-folder |
| `MinimalPermissions.minimal_cover()` | the precomputed `access_cover` row, served to the edge on cache fill |
| in-process `add_access` invalidation | pub-sub message on per-user topic + edge cache drop |
| `parent` map (in-memory) | `folder_tree` (replicated from the file metadata service) |
| (none) | versioned messages + edge cache TTL backstop |
| (none) | fail-closed semantics on ACL service outage |

The algorithm transfers identically — the minimal cover is the same
set in both worlds. What changes is what stores it and how
invalidation propagates.

## Running the tests

```sh
uv run pytest Algorithms/permissions_fs/tests/ -q
```
