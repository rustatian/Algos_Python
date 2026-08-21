# KV Store — progressive design

An in-memory key-value store that grows in capability across four
tiers, each adding one new dimension of complexity:

```
put / get / delete   →   TTL   →   versioned reads   →   distributed
```

Modeled on the classic "KV Store — progressive design" interview
question. Related LeetCode references: #981 (Time-Based KV Store —
exact match for Tier 3), #146 (LRU Cache — eviction foundation),
#1797 (Authentication Manager — TTL pattern), #460 (LFU Cache),
#432 (All O'one Data Structure).

## Problem

Three operations form the baseline contract:

- `put(key, value)` — store a value at the given key (overwrite if present).
- `get(key) -> value | None` — return the stored value or `None` if missing.
- `delete(key)` — remove the key; no-op if not present.

The contract treats `None` as the sentinel for "key not in store." A
caller storing `None` as a real value would be ambiguous; production
KVs solve this by adding a separate `contains(key) -> bool`. This
learning ladder accepts the ambiguity.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleKV` | plain `dict[str, Any]` | the baseline contract — falsy-values vs. missing |
| 2 | `TTLKV` | `(value, expires_at)` tuples; lazy expiry + background sweeper | expiration is lazy-on-read with a sweeper for active eviction; monotonic clock |
| 3a | `TransactionalKV` | overlay dict + delete-tombstones; begin/commit/rollback | transaction isolation — buffered writes, atomic commit, the tombstone trick |
| 3b | `SnapshotKV` | copy-on-write versioning (LeetCode #981 shape) | snapshot isolation — versioned reads, no locking |
| 4 | `DistributedKV` | sharded by key + replicated + WAL | the system-design follow-up — DynamoDB/Cassandra patterns |

Tiers 3a and 3b are **siblings**, not a refinement — both answer the
question "what does a reader see while writes are in flight?" but from
different angles. 3a is the *operational* view (BEGIN/COMMIT/ROLLBACK,
the in-memory-DB interview classic); 3b is the *read-model* view
(read-at-version, LeetCode #981). Snapshot isolation is in fact how
many real databases *implement* transaction isolation, so the two are
deeply related under the hood.

All tiers share the put/get/delete surface:

```python
kv = SimpleKV()
kv.put("user:42", "Alice")
kv.get("user:42")  # -> "Alice"
kv.delete("user:42")
```

Tier 2 adds `put(key, value, ttl_seconds=None)` for time-bounded keys.
Tier 3a adds `begin()`, `commit()`, `rollback()` for atomic write
groups. Tier 3b adds `snapshot() -> int` and `get(key, snapshot=N)`
for versioned reads.

Each tier answers a distinct weak spot. Tier 1 holds keys forever —
fine for a learning baseline, ruinous for a cache or session store.
Tier 2 adds TTL: each value carries an expiry timestamp; reads that
find an expired value evict-and-return-None; a background sweeper
prunes expired entries that nobody reads. Tier 3a makes writes
*atomic and reversible*: a transaction buffers its writes (and
delete-tombstones) in an overlay; `commit` merges the overlay into the
base in one step, `rollback` throws it away. Tier 3b makes the store
*time-traveling*: every put bumps a global version, and reads at a
specific version return the value current at that point — the
LeetCode #981 contract verbatim. Tier 4 leaves the single machine:
keys shard across N nodes by consistent hashing; writes are replicated
to a quorum; durability comes from a Write-Ahead Log with group commit.

The progressive structure (rather than a single all-features class) is
deliberate — each tier teaches one isolated concept. Combining
transactions + TTL + snapshots in one class would compound the
mistakes (every snapshot would have to track expiry-per-version, and a
transaction would have to buffer TTL deadlines too).

### Tier 3a: TransactionalKV

Flat, single-level transactions on the put/get/delete surface:

```python
kv = TransactionalKV()
kv.put("a", 1)  # autocommit — straight to the base store
kv.begin()
kv.put("a", 2)  # buffered in the overlay
kv.get("a")  # -> 2    # overlay shadows the base
kv.rollback()
kv.get("a")  # -> 1    # base never changed
```

The design rests on two ideas:

- **An overlay dict.** While a transaction is open, writes land in a
  separate `txn` dict instead of the base. Reads check the overlay
  first, then fall through to the base. `commit` merges overlay→base;
  `rollback` discards the overlay.
- **Delete-tombstones.** A delete inside a transaction can't just drop
  the key from the overlay — an *absent* overlay key means "fall
  through to the base," which would re-expose the deleted value. So a
  buffered delete writes a distinct `TOMBSTONE` sentinel; `get` returns
  `None` on a tombstone instead of reading the base. This is the same
  mechanism LSM-tree storage engines (RocksDB, Cassandra) use to mark
  deletions in an append-only log.

"Flat single-level" means at most one transaction is open at a time —
`begin` while already in a transaction raises `TransactionError`, as do
`commit`/`rollback` with nothing open. The natural extension (a
nested-transaction variant) promotes the single overlay to a
`list[dict]` stack; `get` then walks the stack top-down, `commit`
merges the top frame into the one below, and `rollback` pops.

## Running the tests

```sh
uv run pytest Algorithms/kv_store/tests/ -q
```
