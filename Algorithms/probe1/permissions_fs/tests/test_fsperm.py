"""Tests for Permissions in File System.

Every tier exposes the same has_access / add_access / remove_access
surface; the correctness tests are parametrized over PERMS so adding a
new tier just appends the new class to the list.
"""

import pytest

from permissions_fs.fsperm import CachedPermissions, SimplePermissions

# Every tier with the same access surface goes here.
PERMS = [SimplePermissions, CachedPermissions]


# A tiny fixture tree used by most tests:
#
#     /
#     ├── usr
#     │   └── local
#     │       └── bin
#     ├── etc
#     └── home
#         └── user
#
TINY_TREE = {
    "/": None,
    "/usr": "/",
    "/usr/local": "/usr",
    "/usr/local/bin": "/usr/local",
    "/etc": "/",
    "/home": "/",
    "/home/user": "/home",
}


@pytest.mark.parametrize("perms_cls", PERMS)
def test_empty_access_returns_false_everywhere(perms_cls: type) -> None:
    """A fresh Permissions object has no grants; every query is False,
    including the root.
    """
    p = perms_cls(TINY_TREE)
    for folder in TINY_TREE:
        assert p.has_access(folder) is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_direct_access(perms_cls: type) -> None:
    """add_access(folder) → has_access(folder) is True."""
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    assert p.has_access("/usr") is True


@pytest.mark.parametrize("perms_cls", PERMS)
def test_inherited_access_one_level(perms_cls: type) -> None:
    """Immediate children of a granted folder inherit access."""
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    assert p.has_access("/usr/local") is True


@pytest.mark.parametrize("perms_cls", PERMS)
def test_inherited_access_deep(perms_cls: type) -> None:
    """Inheritance propagates through every level of descendants."""
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    assert p.has_access("/usr/local/bin") is True


@pytest.mark.parametrize("perms_cls", PERMS)
def test_siblings_do_not_inherit(perms_cls: type) -> None:
    """A grant on /usr does not propagate to /etc or /home/user — they
    are not descendants.
    """
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    assert p.has_access("/etc") is False
    assert p.has_access("/home/user") is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_root_grants_everything(perms_cls: type) -> None:
    """Granting the root grants access to every folder in the tree."""
    p = perms_cls(TINY_TREE)
    p.add_access("/")
    for folder in TINY_TREE:
        assert p.has_access(folder) is True


@pytest.mark.parametrize("perms_cls", PERMS)
def test_remove_revokes_access(perms_cls: type) -> None:
    """After remove_access, descendants lose inherited access (unless
    another ancestor still grants it).
    """
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    assert p.has_access("/usr/local") is True
    p.remove_access("/usr")
    assert p.has_access("/usr") is False
    assert p.has_access("/usr/local") is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_remove_preserves_other_ancestors(perms_cls: type) -> None:
    """If two ancestors grant access, removing one still leaves the
    descendant accessible via the other.
    """
    p = perms_cls(TINY_TREE)
    p.add_access("/")
    p.add_access("/usr")
    p.remove_access("/usr")
    # / still grants → /usr/local still accessible.
    assert p.has_access("/usr/local") is True
    assert p.has_access("/usr") is True  # via root


@pytest.mark.parametrize("perms_cls", PERMS)
def test_multiple_independent_grants(perms_cls: type) -> None:
    """Two unrelated grants — each subtree is independent."""
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    p.add_access("/home")
    assert p.has_access("/usr/local/bin") is True
    assert p.has_access("/home/user") is True
    assert p.has_access("/etc") is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_remove_unknown_is_noop(perms_cls: type) -> None:
    """Removing a folder that was never granted is a no-op (no raise)."""
    p = perms_cls(TINY_TREE)
    p.remove_access("/usr")
    assert p.has_access("/usr") is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_repeated_grant_is_idempotent(perms_cls: type) -> None:
    """add_access on an already-granted folder is a no-op; one remove
    revokes regardless of how many adds were issued.
    """
    p = perms_cls(TINY_TREE)
    p.add_access("/usr")
    p.add_access("/usr")
    p.add_access("/usr")
    assert p.has_access("/usr") is True
    p.remove_access("/usr")
    assert p.has_access("/usr") is False


@pytest.mark.parametrize("perms_cls", PERMS)
def test_query_folder_with_direct_access_returns_true(perms_cls: type) -> None:
    """A folder in the access set returns True without walking — the
    folder itself counts as its own zeroth ancestor.
    """
    p = perms_cls(TINY_TREE)
    p.add_access("/usr/local/bin")
    assert p.has_access("/usr/local/bin") is True
    # And inheritance doesn't run upward — siblings/parents are not granted.
    assert p.has_access("/usr/local") is False
    assert p.has_access("/usr") is False


# ---------------------------------------------------------------------------
# Tier 2 only — cache-invalidation correctness for CachedPermissions.
#
# These exercise the bug a naive cache misses: when the access set
# changes, the SAME folder that was previously queried must return the
# NEW answer, not the cached old one. A Tier 2 implementation that
# forgot invalidation on add/remove would still pass the shared tests
# IF every test only queries the folder after the final state — but
# would fail these.
# ---------------------------------------------------------------------------


def test_cached_invalidation_on_grant_after_query() -> None:
    """Querying BEFORE granting caches False; the subsequent grant must
    invalidate that cache so the next query returns True. A Tier 2
    implementation that skipped invalidation on add_access would
    persist the cached False here.
    """
    p = CachedPermissions(TINY_TREE)
    # Prime the cache with the "no access yet" answer.
    assert p.has_access("/usr/local") is False
    # Grant access to an ancestor.
    p.add_access("/usr")
    # Invalidation must drop the stale False; new walk finds /usr in
    # access and returns True.
    assert p.has_access("/usr/local") is True


def test_cached_invalidation_on_revoke_after_query() -> None:
    """Mirror of the above: querying after grant caches True; revoking
    the grant must invalidate the cache so the next query returns False.
    """
    p = CachedPermissions(TINY_TREE)
    p.add_access("/usr")
    # Prime the cache with True.
    assert p.has_access("/usr/local") is True
    # Revoke; cache must be invalidated for the /usr subtree.
    p.remove_access("/usr")
    assert p.has_access("/usr/local") is False


def test_cached_unrelated_subtrees_remain_correct_across_changes() -> None:
    """A change in one subtree must not corrupt the answers for an
    unrelated subtree. We can't easily inspect the cache directly, but
    we can verify correctness is preserved across many changes — which
    is what matters.
    """
    p = CachedPermissions(TINY_TREE)
    p.add_access("/home")
    assert p.has_access("/home/user") is True
    # Lots of churn in /usr; /home subtree must still report correctly.
    p.add_access("/usr")
    p.remove_access("/usr")
    p.add_access("/usr/local")
    p.remove_access("/usr/local")
    assert p.has_access("/home/user") is True
    assert p.has_access("/home") is True
    assert p.has_access("/usr") is False
    assert p.has_access("/usr/local/bin") is False


def test_cached_deep_invalidation_reaches_all_descendants() -> None:
    """Adding access at the root must invalidate (or otherwise
    correctly update) every cached entry in the tree. After priming
    the cache with False for every leaf, granting the root must flip
    every subsequent query to True.
    """
    p = CachedPermissions(TINY_TREE)
    # Prime: every folder caches False.
    for folder in TINY_TREE:
        assert p.has_access(folder) is False
    # Grant the root → every cached False is now stale.
    p.add_access("/")
    for folder in TINY_TREE:
        assert p.has_access(folder) is True
