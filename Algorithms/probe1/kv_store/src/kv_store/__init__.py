"""KV Store — progressive design, tiered learning port.

Public classes:
    SimpleKV         — Tier 1: in-memory dict with put / get / delete.
    TTLKV            — Tier 2: per-key TTL with lazy expiry + sweep_expired().
    TransactionalKV  — Tier 3a: flat transactions (begin / commit / rollback).
    TransactionError — raised on an illegal transaction state transition.

Additional tiers (SnapshotKV [3b], DistributedKV [4]) will land in this
namespace as they are added.
"""

from kv_store.store import SimpleKV, TransactionalKV, TransactionError, TTLKV

__all__ = ["SimpleKV", "TTLKV", "TransactionalKV", "TransactionError"]
