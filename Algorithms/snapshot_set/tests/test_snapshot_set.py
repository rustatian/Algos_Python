"""Tests for the SnapshotSet ladder.

The live-set surface and the snapshot-isolation guarantee are identical
across all three tiers, so those tests are parametrized over SETS. The
garbage-collection behavior is unique to Tier 3 (GCSnapshotSet) and has
its own section.

The central property under test is ISOLATION: a snapshot taken at one
moment must keep reporting the contents of that moment, no matter how
the live set is mutated afterwards.
"""

import pytest

from snapshot_set import (
    CoWSnapshotSet,
    GCSnapshotSet,
    SimpleSnapshotSet,
)

# Every tier shares the live-set surface and the isolation guarantee.
SETS = [SimpleSnapshotSet, CoWSnapshotSet, GCSnapshotSet]


# ----------------------------------------------------------------------
# Live-set behavior (shared across all tiers).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", SETS)
def test_add_and_contains(cls: type) -> None:
    s = cls()
    s.add("a")
    assert s.contains("a")
    assert not s.contains("b")


@pytest.mark.parametrize("cls", SETS)
def test_remove(cls: type) -> None:
    s = cls()
    s.add("a")
    s.remove("a")
    assert not s.contains("a")


@pytest.mark.parametrize("cls", SETS)
def test_add_is_idempotent(cls: type) -> None:
    s = cls()
    s.add(7)
    s.add(7)
    assert s.contains(7)
    assert s.items() == {7}


@pytest.mark.parametrize("cls", SETS)
def test_remove_absent_is_noop(cls: type) -> None:
    s = cls()
    s.remove("never")  # must not raise
    s.add("x")
    s.remove("x")
    s.remove("x")  # second remove also a no-op
    assert not s.contains("x")


@pytest.mark.parametrize("cls", SETS)
def test_items_returns_a_copy(cls: type) -> None:
    """items() hands out a copy — mutating it must not touch the live set."""
    s = cls()
    s.add("a")
    snapshot_of_items = s.items()
    snapshot_of_items.add("b")  # mutate the returned set
    assert s.contains("a")
    assert not s.contains("b")  # live set untouched


@pytest.mark.parametrize("cls", SETS)
def test_iter_yields_all_live_elements(cls: type) -> None:
    s = cls()
    for x in (1, 2, 3):
        s.add(x)
    assert set(s) == {1, 2, 3}


# ----------------------------------------------------------------------
# Snapshot isolation (shared across all tiers — the core guarantee).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", SETS)
def test_snapshot_isolated_from_later_add(cls: type) -> None:
    s = cls()
    s.add("a")
    snap = s.snapshot()
    s.add("b")  # mutate live AFTER the snapshot
    assert snap.contains("a")
    assert not snap.contains("b")
    assert snap.items() == {"a"}


@pytest.mark.parametrize("cls", SETS)
def test_snapshot_isolated_from_later_remove(cls: type) -> None:
    s = cls()
    s.add("a")
    s.add("b")
    snap = s.snapshot()
    s.remove("a")  # remove AFTER the snapshot
    assert snap.contains("a")  # snapshot still sees it
    assert not s.contains("a")  # live set does not


@pytest.mark.parametrize("cls", SETS)
def test_empty_snapshot_stays_empty(cls: type) -> None:
    s = cls()
    snap = s.snapshot()  # snapshot of {}
    s.add("late")
    assert not snap.contains("late")
    assert list(snap.iterator()) == []


@pytest.mark.parametrize("cls", SETS)
def test_two_snapshots_are_independent_points_in_time(cls: type) -> None:
    s = cls()
    s.add(1)
    snap1 = s.snapshot()  # {1}
    s.add(2)
    snap2 = s.snapshot()  # {1, 2}
    s.add(3)
    assert snap1.items() == {1}
    assert snap2.items() == {1, 2}
    assert s.items() == {1, 2, 3}


@pytest.mark.parametrize("cls", SETS)
def test_readd_after_remove_is_a_fresh_interval(cls: type) -> None:
    """add → snapshot → remove → snapshot → add → snapshot: each snapshot
    captures the membership at its own moment. Exercises the per-element
    version history in the CoW/GC tiers.
    """
    s = cls()
    s.add("x")
    snap_present = s.snapshot()
    s.remove("x")
    snap_absent = s.snapshot()
    s.add("x")
    snap_present_again = s.snapshot()
    assert snap_present.contains("x")
    assert not snap_absent.contains("x")
    assert snap_present_again.contains("x")


@pytest.mark.parametrize("cls", SETS)
def test_snapshot_items_is_a_copy(cls: type) -> None:
    s = cls()
    s.add("a")
    snap = s.snapshot()
    got = snap.items()
    got.add("mutated")  # mutating the result must not affect the snapshot
    assert snap.items() == {"a"}


@pytest.mark.parametrize("cls", SETS)
def test_many_snapshots_all_consistent(cls: type) -> None:
    s = cls()
    s.add(1)
    snaps = [s.snapshot() for _ in range(50)]  # all capture {1}
    s.add(2)
    assert all(snap.contains(1) for snap in snaps)
    assert not any(snap.contains(2) for snap in snaps)


# ----------------------------------------------------------------------
# Tier 3: GCSnapshotSet — refcounted garbage collection.
# ----------------------------------------------------------------------


def test_gc_unreleased_snapshot_keeps_its_view() -> None:
    """A live (unreleased) snapshot must keep reporting its frozen view
    even while other snapshots are taken and released around it.
    """
    s = GCSnapshotSet()
    s.add(1)
    keep = s.snapshot()  # pins this version; never released
    s.add(2)
    throwaway = s.snapshot()
    throwaway.release()
    assert keep.items() == {1}  # still correct — its version stayed pinned
    assert s.contains(2)


def test_gc_release_reclaims_old_history() -> None:
    """Releasing every snapshot lets the history shrink toward the live
    set — the whole point of Tier 3. With Tier 2's append-only history it
    would only ever grow.
    """
    s = GCSnapshotSet()
    s.add("a")
    # Churn membership behind a series of released snapshots.
    for _ in range(20):
        snap = s.snapshot()
        s.remove("a")
        s.add("a")
        snap.release()
    # No snapshot is live now; GC should have collapsed the history.
    # The live set has 1 element, so retained history should be small —
    # certainly far less than the ~40 records an ungarbage-collected
    # history would hold.
    assert s.live_history_size() <= 2
    assert s.contains("a")


def test_gc_context_manager_releases() -> None:
    s = GCSnapshotSet()
    s.add("x")
    with s.snapshot() as snap:
        assert snap.contains("x")
    # snap is auto-released at block exit; reading it now must raise.
    with pytest.raises(RuntimeError):
        snap.contains("x")


def test_gc_read_after_release_raises() -> None:
    s = GCSnapshotSet()
    s.add("x")
    snap = s.snapshot()
    snap.release()
    with pytest.raises(RuntimeError):
        snap.items()


def test_gc_release_is_idempotent() -> None:
    s = GCSnapshotSet()
    s.add("x")
    snap = s.snapshot()
    snap.release()
    snap.release()  # second release must be a harmless no-op


def test_gc_multiple_snapshots_same_version_share_one_pin() -> None:
    """Several snapshots taken with no write between them pin the same
    version; the version is only collectible once all of them release.
    """
    s = GCSnapshotSet()
    s.add(1)
    a = s.snapshot()
    b = s.snapshot()  # same version as `a` (no write between)
    s.add(2)
    a.release()
    # `b` still pins the old version — its view must remain correct.
    assert b.items() == {1}
    b.release()
