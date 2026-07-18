"""Tests for the in-memory filesystem ladder.

The ls/mkdir/add/read contract is shared across all three tiers, so those
tests are parametrized over FILE_SYSTEMS. Path normalization is Tier 2+
only; concurrency is Tier 3 only.
"""

import threading

import pytest

from build_fs import (
    PathNormalizingFileSystem,
    SimpleFileSystem,
    ThreadSafeFileSystem,
)

# All tiers share the core path-addressed surface.
FILE_SYSTEMS = [SimpleFileSystem, PathNormalizingFileSystem, ThreadSafeFileSystem]

# Tiers that understand "." / ".." in paths.
NORMALIZING = [PathNormalizingFileSystem, ThreadSafeFileSystem]


# ----------------------------------------------------------------------
# Shared core contract (LeetCode #588).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_empty_root_lists_nothing(cls: type) -> None:
    fs = cls()
    assert fs.ls("/") == []


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_mkdir_then_ls(cls: type) -> None:
    fs = cls()
    fs.mkdir("/a/b/c")
    assert fs.ls("/") == ["a"]
    assert fs.ls("/a") == ["b"]
    assert fs.ls("/a/b") == ["c"]


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_add_and_read_file(cls: type) -> None:
    fs = cls()
    fs.add_content_to_file("/a/b/c/d", "hello")
    assert fs.ls("/") == ["a"]
    assert fs.read_content_from_file("/a/b/c/d") == "hello"


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_add_appends(cls: type) -> None:
    fs = cls()
    fs.add_content_to_file("/f", "ab")
    fs.add_content_to_file("/f", "cd")
    assert fs.read_content_from_file("/f") == "abcd"


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_ls_on_file_returns_its_name(cls: type) -> None:
    fs = cls()
    fs.add_content_to_file("/a/f", "x")
    assert fs.ls("/a/f") == ["f"]


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_ls_is_sorted(cls: type) -> None:
    fs = cls()
    for name in ("c", "a", "b"):
        fs.mkdir(f"/{name}")
    assert fs.ls("/") == ["a", "b", "c"]


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_mkdir_is_idempotent(cls: type) -> None:
    fs = cls()
    fs.mkdir("/a/b")
    fs.mkdir("/a/b")  # must not clobber or raise
    fs.add_content_to_file("/a/b/f", "x")
    fs.mkdir("/a/b")  # still must not wipe the file's parent
    assert fs.read_content_from_file("/a/b/f") == "x"


@pytest.mark.parametrize("cls", FILE_SYSTEMS)
def test_directories_and_files_coexist(cls: type) -> None:
    fs = cls()
    fs.mkdir("/a/dir")
    fs.add_content_to_file("/a/file", "data")
    assert fs.ls("/a") == ["dir", "file"]


# ----------------------------------------------------------------------
# Tier 2+ — path normalization (LeetCode #71).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", NORMALIZING)
def test_dot_dot_resolves_to_parent(cls: type) -> None:
    fs = cls()
    fs.mkdir("/a/b")
    fs.add_content_to_file("/a/b/../b/f", "x")  # == /a/b/f
    assert fs.read_content_from_file("/a/b/f") == "x"


@pytest.mark.parametrize("cls", NORMALIZING)
def test_single_dot_is_ignored(cls: type) -> None:
    fs = cls()
    fs.add_content_to_file("/a/./b", "y")  # == /a/b
    assert fs.read_content_from_file("/a/b") == "y"


@pytest.mark.parametrize("cls", NORMALIZING)
def test_dot_dot_above_root_is_noop(cls: type) -> None:
    fs = cls()
    fs.add_content_to_file("/../f", "z")  # == /f
    assert fs.read_content_from_file("/f") == "z"


# ----------------------------------------------------------------------
# Tier 3 — concurrency.
# ----------------------------------------------------------------------


def test_threadsafe_concurrent_mkdir_under_shared_parent() -> None:
    """Many threads create distinct children of the same parent at once;
    all must survive (none lost to a race on creating the parent).
    """
    fs = ThreadSafeFileSystem()
    fs.mkdir("/shared")

    def worker(base: int) -> None:
        for i in range(50):
            fs.mkdir(f"/shared/{base}_{i}")

    threads = [threading.Thread(target=worker, args=(b,)) for b in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 8 workers * 50 dirs each, all under /shared, none lost.
    assert len(fs.ls("/shared")) == 8 * 50
