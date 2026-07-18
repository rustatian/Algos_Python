"""Byte-pattern search — tiered learning port.

Public classes:
    NaiveSearch            — Tier 1: O(n·m) brute force over a buffer.
    RabinKarpSearch        — Tier 2: O(n+m) rolling hash over a buffer.
    ChunkedRabinKarpSearch — Tier 3: bounded-memory stream search with
                             overlap reads (the "too large to load" case).

Tier 4 (DistributedSearch) is an architecture discussion, not code — see
README.md.
"""

from byte_pattern.byte_pattern import (
    ChunkedRabinKarpSearch,
    NaiveSearch,
    RabinKarpSearch,
)

__all__ = ["NaiveSearch", "RabinKarpSearch", "ChunkedRabinKarpSearch"]
