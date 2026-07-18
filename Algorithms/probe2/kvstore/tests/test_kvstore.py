"""Tests for the KV Store ladder.

The shared put / get / delete surface is parametrized over the tiers that
expose it, so a new tier only needs to be appended to ``BASIC_KVS``.
Aggregate behavior (average / max) gets its own section. Tests for the
windowed and retention tiers are added as we reach them.

No input-validation tests on purpose — these exercises are about the
algorithm, not argument guarding.
"""

import pytest

from kvstore.store import SimpleKV, StatsKV

# Every tier with the same put / get / delete surface goes here.
BASIC_KVS = [SimpleKV, StatsKV]


# --- Tiers 1 & 2: shared put / get / delete surface ---------------------


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_get_missing_returns_none(kv_cls):
    kv = kv_cls()
    assert kv.get("absent") is None


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_put_then_get(kv_cls):
    kv = kv_cls()
    kv.put("a", 1.0)
    assert kv.get("a") == 1.0


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_put_overwrites(kv_cls):
    kv = kv_cls()
    kv.put("a", 1.0)
    kv.put("a", 2.0)
    assert kv.get("a") == 2.0


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_delete_removes(kv_cls):
    kv = kv_cls()
    kv.put("a", 1.0)
    kv.delete("a")
    assert kv.get("a") is None


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_delete_absent_is_noop(kv_cls):
    kv = kv_cls()
    kv.delete("ghost")  # must not raise
    assert kv.get("ghost") is None


@pytest.mark.parametrize("kv_cls", BASIC_KVS)
def test_many_keys_independent(kv_cls):
    kv = kv_cls()
    for i in range(100):
        kv.put(str(i), float(i))
    for i in range(100):
        assert kv.get(str(i)) == float(i)


# --- Tier 2: average ----------------------------------------------------


def test_average_empty_is_zero():
    kv = StatsKV()
    assert kv.get_average() == 0.0


def test_average_basic():
    kv = StatsKV()
    kv.put("a", 1.0)
    kv.put("b", 2.0)
    kv.put("c", 3.0)
    assert kv.get_average() == 2.0


def test_average_reflects_overwrite():
    kv = StatsKV()
    kv.put("a", 1.0)
    kv.put("b", 3.0)  # avg 2.0
    kv.put("a", 5.0)  # a:5, b:3 -> avg 4.0
    assert kv.get_average() == 4.0


def test_average_reflects_delete():
    kv = StatsKV()
    kv.put("a", 1.0)
    kv.put("b", 3.0)
    kv.delete("a")  # only b:3 remains
    assert kv.get_average() == 3.0


# --- Tier 2: max (lazy-deletion heap) -----------------------------------


def test_max_empty_is_none():
    kv = StatsKV()
    assert kv.get_max() is None


def test_max_basic():
    kv = StatsKV()
    kv.put("a", 1.0)
    kv.put("b", 5.0)
    kv.put("c", 3.0)
    assert kv.get_max() == 5.0


def test_max_after_overwrite_lowers():
    kv = StatsKV()
    kv.put("a", 10.0)
    kv.put("b", 2.0)
    kv.put("a", 1.0)  # the 10 is gone; max now 2
    assert kv.get_max() == 2.0


def test_max_after_delete_of_current_max():
    kv = StatsKV()
    kv.put("a", 10.0)
    kv.put("b", 2.0)
    kv.delete("a")  # drop the current max
    assert kv.get_max() == 2.0


def test_max_then_all_deleted_is_none():
    kv = StatsKV()
    kv.put("a", 10.0)
    kv.put("b", 2.0)
    kv.delete("a")
    kv.delete("b")
    assert kv.get_max() is None


def test_max_stable_under_repeated_overwrite():
    # Stale heap nodes must not resurrect an old maximum.
    kv = StatsKV()
    kv.put("a", 5.0)
    kv.put("a", 1.0)
    kv.put("a", 9.0)
    kv.put("a", 2.0)
    assert kv.get_max() == 2.0
    assert kv.get_average() == 2.0
