"""LRU Cache — the state-management foundation, tiered learning port.

Public classes:
    SimpleLRU      — Tier 1: single-threaded DLL + dict; O(1) get/put.
    ThreadSafeLRU  — Tier 2: Tier 1 under one lock.
    TTLLRU         — Tier 3: per-entry TTL on top of LRU eviction.

Tier 4 (DistributedCache) is an architecture discussion, not code — see
README.md.
"""

from lru.lru import SimpleLRU, ThreadSafeLRU, TTLLRU

__all__ = ["SimpleLRU", "ThreadSafeLRU", "TTLLRU"]
