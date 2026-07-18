"""Permissions in File System — a classic systems interview question.

A file system is a tree of folders; each folder has at most one parent
and the root has no parent. A user has explicit access to some set of
folders. ``has_access(folder)`` returns True if the folder is in the
access set OR any of its ancestors is — i.e., access inherits from
ancestors down to descendants.

Input:
    parent: dict[str, str | None]
        Maps each folder to its parent. The root folder maps to None.
        All folders referenced by has_access / add_access / remove_access
        must appear as keys in this mapping.
    add_access(folder: str) -> None
        Grant explicit access to ``folder``.
    remove_access(folder: str) -> None
        Revoke explicit access (no-op if not granted).
    has_access(folder: str) -> bool
        True iff ``folder`` itself or any ancestor is currently in the
        access set.
Output:
    has_access returns the boolean answer. The access set is
    write-through — add/remove are visible to subsequent queries.

Example 1:
    parent = {
        "/":              None,
        "/usr":           "/",
        "/usr/local":     "/usr",
        "/usr/local/bin": "/usr/local",
        "/etc":           "/",
    }
    ops:  ["add_access", "has_access", "has_access",   "has_access",       "has_access"]
    args: [["/usr"],     ["/usr"],     ["/usr/local"], ["/usr/local/bin"], ["/etc"]]
    out:  [None,         True,         True,           True,               False]
    Explanation:
        Grant access to "/usr". A direct query on "/usr" returns True;
        descendants ("/usr/local", "/usr/local/bin") inherit. "/etc" is
        a sibling of "/usr", not a descendant, so it stays False.

Example 2 (multi-grant + revoke):
    parent = {"/": None, "/a": "/", "/a/b": "/a", "/c": "/"}
    ops:  ["add_access", "add_access", "has_access", "remove_access", "has_access", "has_access"]
    args: [["/a"],       ["/c"],       ["/a/b"],     ["/a"],          ["/a/b"],     ["/c"]]
    out:  [None,         None,         True,         None,            False,        True]
    Explanation:
        Access to "/a" grants access to "/a/b". After revoking "/a",
        "/a/b" no longer has any ancestor in the access set → False.
        "/c" still has its own access entry.

Example 3 (root grants everything):
    parent = {"/": None, "/a": "/", "/a/b": "/a", "/c": "/"}
    ops:  ["add_access", "has_access", "has_access", "has_access"]
    args: [["/"],        ["/"],        ["/a/b"],     ["/c"]]
    out:  [None,         True,         True,         True]
    Explanation:
        Granting access to the root grants access to every folder in
        the tree — every folder has the root as an ancestor.

Modeled on the classic "Permissions in File System" onsite interview
question. Related LeetCode tree-traversal problems: #1376 (Time to
Inform Employees), #1466 (Reorder Routes), #797 (All Paths Source to
Target).

This package ports the problem as a tiered learning ladder. Each tier
is a class with the same has_access surface; what changes is what's
precomputed, what's cached, and what extra operations are exposed.

Tier 1: SimplePermissions       — walk parent pointers on every query; no precomputation.
Tier 2: CachedPermissions       — memoize has_access; invalidate descendants on permission change.
Tier 3: MinimalPermissions      — adds minimal_cover() via BFS-mark over the tree.
Tier 4: DistributedPermissions  — sharded ACL service across users (the system-design follow-up).

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

from collections import defaultdict


class SimplePermissions:
    """Tier 1: walk parent pointers from folder to root; check ancestors.

    The textbook algorithm. No precomputation, no cache. Every
    ``has_access`` call retraces the folder's chain of ancestors until
    it finds one in the access set or reaches the root.

    Input:
        __init__(parent: dict[str, str | None])
            Folder → parent map. Root → None.
        add_access(folder: str) -> None
            Grant explicit access.
        remove_access(folder: str) -> None
            Revoke explicit access (no-op if not granted).
        has_access(folder: str) -> bool
            True iff folder OR any ancestor is in the access set.
    Output:
        has_access returns the boolean answer.

    Example 1:
        parent = {"/": None, "/a": "/", "/a/b": "/a"}
        ops:  ["add_access", "has_access", "has_access", "has_access"]
        args: [["/a"],       ["/a"],       ["/a/b"],     ["/"]]
        out:  [None,         True,         True,         False]
        Explanation:
            From "/a/b": "/a/b" not in access; parent "/a" IS in access
            → return True. From "/": "/" not in access; parent is None
            → return False (the root has no ancestor to inherit from).

    Example 2 (deep chain — pays the O(depth) cost):
        parent = {"/": None, "/a": "/", "/a/b": "/a", "/a/b/c": "/a/b"}
        ops:  ["add_access", "has_access"]
        args: [["/"],         ["/a/b/c"]]
        out:  [None,          True]
        Explanation:
            From "/a/b/c", walk up: "/a/b/c" → "/a/b" → "/a" → "/" → None.
            Only the root is in the access set; the walk visits every
            ancestor before finding it. Depth-many comparisons per
            query — the worst case Tier 2 fixes by caching.

    Example 3 (no inheritance from siblings):
        parent = {"/": None, "/a": "/", "/b": "/"}
        ops:  ["add_access", "has_access"]
        args: [["/a"],       ["/b"]]
        out:  [None,         False]
        Explanation:
            "/b" is a sibling of "/a", not a descendant. Walk up: "/b"
            → "/" → None; neither is in the access set → False.
            Inheritance only flows from ancestors.

    Standard library:
        set — the access set; O(1) membership test.
        dict — the parent map; O(1) parent lookup.

    Pseudocode:
        data:
            parent — dict folder → parent (None for root).
            access — set of folders with explicit access.

        add_access(f):
            add f to access.

        remove_access(f):
            discard f from access (no-op if absent).

        has_access(f):
            current = f
            while current is not None:
                if current in access:
                    return True
                current = parent[current]
            return False

    Complexity:
        Storage: O(|tree|) for the parent map; O(|access|) for the set.
        add_access(), remove_access(): O(1).
        has_access(): O(depth) — the chain of ancestors walked from
            the query node up to the root. For a balanced tree of N
            folders, depth ≈ log N. For a worst-case linear chain,
            depth = N.
    """

    def __init__(self, parent: dict[str, str | None]):
        self.parent = parent
        self.access: set = set()

    def add_access(self, folder: str) -> None:
        self.access.add(folder)
        return None

    def remove_access(self, folder: str) -> None:
        self.access.discard(folder)
        return None

    def has_access(self, folder: str) -> bool:
        curr = folder
        while curr is not None:
            if curr in self.access:
                return True
            curr = self.parent[curr]
        return False


class CachedPermissions:
    """Tier 2: memoize has_access; invalidate descendants on change.

    Same algorithm as Tier 1, plus a cache. The first call to
    ``has_access(folder)`` walks the parent chain and caches the
    result; subsequent calls for the same folder return from the cache
    in O(1). When the access set changes for some folder F, every
    *descendant* of F has its cache entry invalidated — those are the
    folders whose has_access answer could have changed.

    The invalidation traversal is a BFS down from F via a ``children``
    map built once at construction (the inverse of ``parent``). The
    same children map is reused by Tier 3's ``minimal_cover()``.

    Input:
        __init__(parent: dict[str, str | None])
        add_access(folder: str) -> None
        remove_access(folder: str) -> None
        has_access(folder: str) -> bool
    Output:
        Identical to SimplePermissions — same boolean answer, just
        faster on repeated queries.

    Example 1 (same I/O as Tier 1; cache is invisible to the contract):
        parent = {"/": None, "/a": "/", "/a/b": "/a"}
        ops:  ["add_access", "has_access", "has_access", "has_access"]
        args: [["/a"],       ["/a/b"],     ["/a/b"],     ["/"]]
        out:  [None,         True,         True,         False]
        Explanation:
            First has_access("/a/b") walks "/a/b" → "/a" (in access) →
            True; the answer is cached. Second call returns from cache
            in O(1). has_access("/") walks "/" → None → False; cached.

    Example 2 (invalidation on revoke — the bug to avoid):
        parent = {"/": None, "/a": "/", "/a/b": "/a"}
        ops:  ["add_access", "has_access", "remove_access", "has_access"]
        args: [["/a"],       ["/a/b"],     ["/a"],          ["/a/b"]]
        out:  [None,         True,         None,            False]
        Explanation:
            has_access("/a/b") caches True. remove_access("/a") must
            invalidate the subtree under "/a" — both "/a" and "/a/b"
            have their cache entries cleared. The next has_access("/a/b")
            re-walks, finds no ancestor in the access set → False.
            A naive implementation that *only* updated the access set
            (no invalidation) would still return the stale True here.

    Example 3 (invalidation on add — the symmetric bug):
        parent = {"/": None, "/a": "/", "/a/b": "/a"}
        ops:  ["has_access", "add_access", "has_access"]
        args: [["/a/b"],     ["/a"],       ["/a/b"]]
        out:  [False,        None,         True]
        Explanation:
            has_access("/a/b") caches False (no grants yet).
            add_access("/a") must invalidate the "/a" subtree; without
            invalidation the cached False would persist and the second
            has_access("/a/b") would wrongly return False.

    Standard library:
        set — the access set.
        dict (parent) — the parent map (passed in).
        dict (children) — inverted parent map, built once at __init__.
        dict (cache) — folder → cached has_access bool.
        collections.deque OR list — the BFS queue for invalidation.

    Pseudocode:
        data:
            parent   — dict folder → parent.
            children — dict folder → list of children. Built once.
            access   — set.
            cache    — dict folder → bool.

        __init__(parent):
            store parent.
            build children = invert parent:
                for f, p in parent.items():
                    if p is not None:
                        children[p].append(f)
            access = empty set.
            cache  = empty dict.

        invalidate_subtree(f):
            queue = [f]
            while queue is not empty:
                node = queue.pop()
                cache.pop(node, None)              # remove if present
                for child in children.get(node, []):
                    queue.append(child)

        add_access(f):
            access.add(f)
            invalidate_subtree(f)

        remove_access(f):
            access.discard(f)
            invalidate_subtree(f)

        has_access(f):
            if f in cache:
                return cache[f]
            current = f
            while current is not None:
                if current in access:
                    cache[f] = True
                    return True
                current = parent[current]
            cache[f] = False
            return False

    Why invalidate descendants but not siblings or cousins:
        When ``/usr``'s access changes, every descendant of ``/usr``
        had its has_access answer computed using ``/usr``'s membership
        at the time. Their cached values are now stale. Folders in
        OTHER subtrees (``/etc``, ``/home/user``) have ancestors that
        are unchanged — their cached values are still correct. The
        children map defines exactly the invalidation set.

    Complexity:
        Storage: O(|tree|) for parent and children maps; O(|access|)
            for the access set; O(|cache|) ≤ O(|tree|) for the cache.
        add_access(), remove_access(): O(|subtree of f|) for
            invalidation. Small subtree → near-O(1); root → O(|tree|).
        has_access(): O(1) on cache hit; O(depth) on cache miss
            (same walk as Tier 1).

    What this tier does NOT do (left as an optimization):
        Back-propagate during the walk-up. A smarter has_access caches
        every folder visited on the way up — so a single chain of
        queries from leaves toward the root amortizes the walk: the
        first miss is O(depth), every subsequent query for a folder
        on that chain is O(1). The simple version here only caches
        the *queried* folder; ancestors are uncached until queried
        themselves.
    """

    def __init__(self, parent: dict[str, str | None]):
        self._parent = parent
        self._children = defaultdict(list)
        for f, p in self._parent.items():
            if p is not None:
                self._children[p].append(f)
        self._access = set()
        self._cache = {}

    def _invalidate_subtree(self, folder: str) -> None:
        queue = [folder]
        while queue:
            node = queue.pop()
            self._cache.pop(node, None)
            for ch in self._children.get(node, []):
                queue.append(ch)

    def add_access(self, folder: str) -> None:
        self._access.add(folder)
        self._invalidate_subtree(folder)
        return None

    def remove_access(self, folder: str) -> None:
        self._access.discard(folder)
        self._invalidate_subtree(folder)
        return None

    def has_access(self, folder: str) -> bool:
        if folder in self._cache:
            return self._cache[folder]
        curr = folder
        while curr is not None:
            if curr in self._access:
                self._cache[folder] = True
                return True
            curr = self._parent[curr]

        self._cache[folder] = False
        return False
