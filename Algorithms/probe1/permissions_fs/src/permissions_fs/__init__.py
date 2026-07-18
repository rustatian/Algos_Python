"""Permissions in File System — tiered learning port.

Public classes:
    SimplePermissions  — Tier 1: walk parent pointers on every query.
    CachedPermissions  — Tier 2: memoize has_access; invalidate descendants on change.

Additional tiers (MinimalPermissions, DistributedPermissions) will land
in this namespace as they are added.
"""

from permissions_fs.fsperm import CachedPermissions, SimplePermissions

__all__ = ["CachedPermissions", "SimplePermissions"]
