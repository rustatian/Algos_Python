"""Tests for the LRU cache ladder.

The core get/put/eviction contract is identical across SimpleLRU and
ThreadSafeLRU, so those tests are parametrized over LRU_CACHES. TTLLRU
adds expiry (tested with a FakeClock) and a wider put signature, so it
has its own section. ThreadSafeLRU also gets a concurrency stress test.
"""

import threading

import pytest

from lru import SimpleLRU, ThreadSafeLRU, TTLLRU


class FakeClock:
    """A manually-advanced monotonic clock for deterministic TTL tests."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# SimpleLRU and ThreadSafeLRU share the exact get/put/eviction contract.
LRU_CACHES = [SimpleLRU, ThreadSafeLRU]


# ----------------------------------------------------------------------
# Shared get / put / eviction contract.
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_put_then_get(cls: type) -> None:
    c = cls(2)
    c.put("a", 1)
    assert c.get("a") == 1


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_get_missing_returns_none(cls: type) -> None:
    c = cls(2)
    assert c.get("nope") is None


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_evicts_least_recently_used(cls: type) -> None:
    c = cls(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")  # "a" now MRU, "b" is LRU
    c.put("c", 3)  # over capacity -> evict "b"
    assert c.get("b") is None
    assert c.get("a") == 1
    assert c.get("c") == 3


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_overwrite_refreshes_recency_and_does_not_grow(cls: type) -> None:
    c = cls(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("a", 10)  # overwrite + mark MRU; size stays 2
    c.put("c", 3)  # evict LRU, which is now "b"
    assert c.get("a") == 10
    assert c.get("b") is None
    assert c.get("c") == 3
    assert len(c) == 2


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_get_marks_most_recently_used(cls: type) -> None:
    c = cls(3)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    c.get("a")  # touch "a" so "b" becomes the LRU
    c.put("d", 4)  # evicts "b"
    assert c.get("b") is None
    assert c.get("a") == 1


@pytest.mark.parametrize("cls", LRU_CACHES)
def test_capacity_one(cls: type) -> None:
    c = cls(1)
    c.put("a", 1)
    c.put("b", 2)  # evicts "a" immediately
    assert c.get("a") is None
    assert c.get("b") == 2


# ----------------------------------------------------------------------
# Tier 2 only — concurrency.
# ----------------------------------------------------------------------


def test_threadsafe_concurrent_puts_keep_size_bounded() -> None:
    """Many threads hammering put must never exceed capacity or corrupt
    the dict/list (which would surface as a size mismatch or crash).
    """
    c = ThreadSafeLRU(capacity=50)

    def worker(base: int) -> None:
        for i in range(200):
            c.put(f"{base}:{i}", i)

    threads = [threading.Thread(target=worker, args=(b,)) for b in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Capacity invariant held under concurrency.
    assert len(c) == 50


# ----------------------------------------------------------------------
# Tier 3 only — TTL expiry.
# ----------------------------------------------------------------------


def test_ttl_value_before_expiry() -> None:
    clock = FakeClock()
    c = TTLLRU(capacity=10, clock=clock)
    c.put("k", "v", ttl_seconds=5)
    assert c.get("k") == "v"
    clock.advance(3)
    assert c.get("k") == "v"


def test_ttl_expired_is_a_miss() -> None:
    clock = FakeClock()
    c = TTLLRU(capacity=10, clock=clock)
    c.put("k", "v", ttl_seconds=5)
    clock.advance(6)
    assert c.get("k") is None


def test_ttl_expiry_at_exact_boundary() -> None:
    clock = FakeClock()
    c = TTLLRU(capacity=10, clock=clock)
    c.put("k", "v", ttl_seconds=5)
    clock.advance(5)  # clock() >= expires_at -> expired
    assert c.get("k") is None


def test_ttl_none_never_expires() -> None:
    clock = FakeClock()
    c = TTLLRU(capacity=10, clock=clock)
    c.put("forever", "v")  # no ttl
    clock.advance(1_000_000)
    assert c.get("forever") == "v"


def test_ttl_still_evicts_by_lru_capacity() -> None:
    """TTL and LRU eviction coexist: a non-expired entry can still be
    evicted by capacity pressure.
    """
    clock = FakeClock()
    c = TTLLRU(capacity=2, clock=clock)
    c.put("a", 1)  # no ttl
    c.put("b", 2)
    c.put("c", 3)  # capacity 2 -> evict LRU "a"
    assert c.get("a") is None
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_ttl_expired_entry_is_dropped_from_storage() -> None:
    """An expired entry read as a miss should free its slot, not linger."""
    clock = FakeClock()
    c = TTLLRU(capacity=2, clock=clock)
    c.put("a", 1, ttl_seconds=1)
    clock.advance(2)
    assert c.get("a") is None  # lazy expiry drops "a"
    assert len(c) == 0
