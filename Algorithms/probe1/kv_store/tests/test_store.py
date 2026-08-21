"""Tests for KV Store.

Every tier exposes the same put / get / delete surface; correctness
tests are parametrized over KV_STORES so adding a new tier just
appends the new class to the list.

Tier-specific tests live in their own sections at the bottom:
  - TTL behavior (Tier 2) uses a FakeClock so time advances
    deterministically without time.sleep().
  - Transaction behavior (Tier 3a) exercises begin/commit/rollback.
"""

import pytest

from kv_store.store import SimpleKV, TransactionalKV, TransactionError, TTLKV

# Every tier with the same put/get/delete surface goes here.
# TTLKV's TTL params default to None; TransactionalKV is in autocommit
# mode (no open transaction), so both satisfy the base surface.
KV_STORES = [SimpleKV, TTLKV, TransactionalKV]


class FakeClock:
    """A monotonic clock you can advance manually.

    Substitutes for ``time.monotonic`` in tests so we can fast-forward
    time without ``time.sleep()``. ``advance(dt)`` moves the clock
    forward by ``dt`` seconds; calling the instance returns the
    current time (callable interface matches what TTLKV expects).
    """

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_get_unknown_returns_none(kv_cls: type) -> None:
    """A fresh store returns None for any key — the missing-key sentinel."""
    kv = kv_cls()
    assert kv.get("never_set") is None


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_put_then_get_returns_value(kv_cls: type) -> None:
    """The most basic round-trip: put a value, get it back."""
    kv = kv_cls()
    kv.put("user:42", "Alice")
    assert kv.get("user:42") == "Alice"


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_put_overwrites_existing_value(kv_cls: type) -> None:
    """A second put on the same key replaces the first value entirely."""
    kv = kv_cls()
    kv.put("k", "v1")
    kv.put("k", "v2")
    assert kv.get("k") == "v2"


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_delete_removes_key(kv_cls: type) -> None:
    """After delete, a subsequent get returns None."""
    kv = kv_cls()
    kv.put("k", "v")
    kv.delete("k")
    assert kv.get("k") is None


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_delete_unknown_is_noop(kv_cls: type) -> None:
    """Deleting a key that was never set must NOT raise; it's a no-op.
    Tests the dict.pop(key, None) idiom over plain del.
    """
    kv = kv_cls()
    kv.delete("never_existed")  # must not raise
    assert kv.get("never_existed") is None


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_repeated_delete_is_noop(kv_cls: type) -> None:
    """Two deletes in a row on the same key — second is a no-op."""
    kv = kv_cls()
    kv.put("k", "v")
    kv.delete("k")
    kv.delete("k")
    assert kv.get("k") is None


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_multiple_keys_are_independent(kv_cls: type) -> None:
    """Operations on one key do not affect other keys."""
    kv = kv_cls()
    kv.put("a", 1)
    kv.put("b", 2)
    kv.put("c", 3)
    kv.delete("b")
    assert kv.get("a") == 1
    assert kv.get("b") is None
    assert kv.get("c") == 3


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_falsy_values_are_preserved(kv_cls: type) -> None:
    """Storing 0, False, "", or [] must NOT be confused with "missing".
    A naive ``return store.get(key) or None`` would turn 0 / False / ""
    into None — that's wrong. The contract is "None means absent."
    """
    kv = kv_cls()
    kv.put("zero", 0)
    kv.put("false", False)
    kv.put("empty_str", "")
    kv.put("empty_list", [])
    assert kv.get("zero") == 0
    assert kv.get("false") is False
    assert kv.get("empty_str") == ""
    assert kv.get("empty_list") == []


@pytest.mark.parametrize("kv_cls", KV_STORES)
def test_complex_value_types_are_supported(kv_cls: type) -> None:
    """Values can be any Python object — dicts, lists, custom objects.
    The store is a value-passing container, not a serialization layer.
    """
    kv = kv_cls()
    kv.put("nested", {"x": [1, 2, 3], "y": {"z": 7}})
    assert kv.get("nested") == {"x": [1, 2, 3], "y": {"z": 7}}


# ----------------------------------------------------------------------
# Tier 2: TTLKV-specific tests (expiry behavior).
# ----------------------------------------------------------------------


def test_ttl_value_returned_before_expiry() -> None:
    """A value with a TTL is returned normally until its expiry passes."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("session", "Alice", ttl_seconds=5)
    assert kv.get("session") == "Alice"
    clock.advance(3)
    assert kv.get("session") == "Alice"  # 2s left


def test_ttl_value_returns_none_after_expiry() -> None:
    """After ttl_seconds elapse, get returns None."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("session", "Alice", ttl_seconds=5)
    clock.advance(6)
    assert kv.get("session") is None


def test_ttl_expiry_at_exact_boundary() -> None:
    """At exactly ttl_seconds, the value is expired (clock() >= expires_at)."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("k", "v", ttl_seconds=5)
    clock.advance(5)  # exactly at expiry
    assert kv.get("k") is None


def test_put_without_ttl_never_expires() -> None:
    """ttl_seconds=None (or omitted) → key persists indefinitely."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("forever", "stays")
    clock.advance(1_000_000)
    assert kv.get("forever") == "stays"


def test_overwrite_resets_ttl() -> None:
    """A second put without ttl_seconds clears the prior expiry.

    Matches Redis SET semantics — fresh put → fresh expiry state.
    """
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("k", "v1", ttl_seconds=5)
    kv.put("k", "v2")  # no TTL — clears expiry
    clock.advance(100)
    assert kv.get("k") == "v2"


def test_overwrite_replaces_ttl_with_new_ttl() -> None:
    """A second put WITH a new ttl_seconds replaces the old TTL."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("k", "v1", ttl_seconds=2)
    kv.put("k", "v2", ttl_seconds=10)  # new TTL of 10s from now
    clock.advance(5)  # original TTL would have expired
    assert kv.get("k") == "v2"  # but new TTL still has 5s left


def test_get_evicts_expired_key_lazily() -> None:
    """get() on an expired key removes it from internal storage.

    Probe: after a get-that-returns-None, sweep_expired() should
    report 0 evicted — proving the key was already gone.
    """
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("k", "v", ttl_seconds=1)
    clock.advance(5)
    assert kv.get("k") is None  # lazy eviction here
    assert kv.sweep_expired() == 0  # nothing left to sweep


def test_sweep_expired_returns_count_of_evicted() -> None:
    """sweep_expired returns the number of keys it removed."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("a", 1, ttl_seconds=1)
    kv.put("b", 2, ttl_seconds=1)
    kv.put("c", 3, ttl_seconds=1)
    clock.advance(2)
    assert kv.sweep_expired() == 3


def test_sweep_expired_leaves_non_expired_keys() -> None:
    """sweep_expired removes ONLY expired keys; fresh ones stay."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("expired", 1, ttl_seconds=1)
    kv.put("fresh", 2, ttl_seconds=10)
    clock.advance(2)
    assert kv.sweep_expired() == 1
    assert kv.get("fresh") == 2
    assert kv.get("expired") is None


def test_sweep_expired_leaves_no_ttl_keys() -> None:
    """Keys put without ttl_seconds are never swept (no expiry)."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("permanent", 1)
    kv.put("ephemeral", 2, ttl_seconds=1)
    clock.advance(2)
    assert kv.sweep_expired() == 1
    assert kv.get("permanent") == 1


def test_sweep_expired_on_empty_store_returns_zero() -> None:
    """sweep_expired on a store with no keys returns 0 (no-op)."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    assert kv.sweep_expired() == 0


def test_delete_works_on_expired_key() -> None:
    """delete() on an expired-but-not-yet-evicted key is a no-op (no raise)."""
    clock = FakeClock()
    kv = TTLKV(clock=clock)
    kv.put("k", "v", ttl_seconds=1)
    clock.advance(5)
    kv.delete("k")  # must not raise
    assert kv.get("k") is None


# ----------------------------------------------------------------------
# Tier 3a: TransactionalKV-specific tests (begin / commit / rollback).
# ----------------------------------------------------------------------


def test_autocommit_writes_go_straight_to_base() -> None:
    """Outside a transaction, put/get/delete behave like SimpleKV."""
    kv = TransactionalKV()
    kv.put("a", 1)
    assert kv.get("a") == 1
    kv.delete("a")
    assert kv.get("a") is None


def test_commit_persists_buffered_writes() -> None:
    """A write inside a transaction becomes durable on commit."""
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.begin()
    kv.put("a", 2)
    assert kv.get("a") == 2  # overlay hit, before commit
    kv.commit()
    assert kv.get("a") == 2  # base hit, after commit


def test_rollback_discards_buffered_writes() -> None:
    """A write inside a transaction vanishes on rollback."""
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.begin()
    kv.put("a", 99)
    assert kv.get("a") == 99  # visible inside the txn
    kv.rollback()
    assert kv.get("a") == 1  # base never changed


def test_new_key_inside_transaction_visible_then_rolled_back() -> None:
    """A brand-new key in a transaction is visible, then gone after rollback."""
    kv = TransactionalKV()
    kv.begin()
    kv.put("fresh", "v")
    assert kv.get("fresh") == "v"
    kv.rollback()
    assert kv.get("fresh") is None


def test_new_key_inside_transaction_persists_on_commit() -> None:
    """A brand-new key in a transaction survives commit."""
    kv = TransactionalKV()
    kv.begin()
    kv.put("fresh", "v")
    kv.commit()
    assert kv.get("fresh") == "v"


def test_delete_inside_transaction_hidden_then_rolled_back() -> None:
    """Delete-in-txn hides the base value (tombstone) but rollback restores it.

    This is the tombstone test: get() must NOT fall through to the base
    value while the delete is buffered.
    """
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.begin()
    kv.delete("a")
    assert kv.get("a") is None  # tombstone — does NOT see base's 1
    kv.rollback()
    assert kv.get("a") == 1  # delete rolled back


def test_delete_inside_transaction_persists_on_commit() -> None:
    """Delete-in-txn removes the key from the base on commit."""
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.put("b", 2)
    kv.begin()
    kv.delete("a")
    kv.commit()
    assert kv.get("a") is None
    assert kv.get("b") == 2  # untouched key survives


def test_delete_then_reput_inside_transaction() -> None:
    """A tombstone can be overwritten by a later put in the same txn."""
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.begin()
    kv.delete("a")  # overlay: a -> TOMBSTONE
    kv.put("a", 5)  # overlay: a -> 5  (overwrites tombstone)
    assert kv.get("a") == 5
    kv.commit()
    assert kv.get("a") == 5


def test_keys_untouched_by_transaction_fall_through_to_base() -> None:
    """Reads for keys not in the overlay see the committed base value."""
    kv = TransactionalKV()
    kv.put("a", 1)
    kv.put("b", 2)
    kv.begin()
    kv.put("a", 100)  # only 'a' is in the overlay
    assert kv.get("a") == 100  # overlay
    assert kv.get("b") == 2  # falls through to base
    kv.rollback()


def test_commit_without_begin_raises() -> None:
    """commit() with no open transaction raises TransactionError."""
    kv = TransactionalKV()
    with pytest.raises(TransactionError):
        kv.commit()


def test_rollback_without_begin_raises() -> None:
    """rollback() with no open transaction raises TransactionError."""
    kv = TransactionalKV()
    with pytest.raises(TransactionError):
        kv.rollback()


def test_double_begin_raises() -> None:
    """begin() while a transaction is already open raises (flat: no nesting)."""
    kv = TransactionalKV()
    kv.begin()
    with pytest.raises(TransactionError):
        kv.begin()


def test_can_begin_again_after_commit() -> None:
    """After commit closes a transaction, a fresh begin is allowed."""
    kv = TransactionalKV()
    kv.begin()
    kv.put("a", 1)
    kv.commit()
    kv.begin()  # must not raise — txn was closed
    kv.put("a", 2)
    kv.commit()
    assert kv.get("a") == 2


def test_can_begin_again_after_rollback() -> None:
    """After rollback closes a transaction, a fresh begin is allowed."""
    kv = TransactionalKV()
    kv.begin()
    kv.put("a", 1)
    kv.rollback()
    kv.begin()  # must not raise
    kv.put("a", 2)
    kv.commit()
    assert kv.get("a") == 2


def test_falsy_value_survives_commit() -> None:
    """Buffering a falsy value (0, "", False) and committing keeps it intact.

    Guards against an implementation that treats falsy overlay values as
    'absent' and skips them during commit.
    """
    kv = TransactionalKV()
    kv.begin()
    kv.put("zero", 0)
    kv.put("empty", "")
    kv.commit()
    assert kv.get("zero") == 0
    assert kv.get("empty") == ""
