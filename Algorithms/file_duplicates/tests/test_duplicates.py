"""Tests for Find Duplicate Files.

Every tier returns the same duplicate groups, so the correctness tests are
parametrized over the finder classes. Neither the order of the groups nor
the order of paths within a group is fixed by the contract, so results are
compared through norm() below.

Tiers 1-2 share LeetCode #609's string-input contract and the
STRING_FINDERS list. Tiers 3-4 walk a real directory tree, share
TREE_FINDERS, and use pytest's tmp_path fixture; each tier also has a
small block of tier-specific tests.
"""

from pathlib import Path

import pytest

from file_duplicates.duplicates import (
    DistributedFinder,
    DuplicateFinder,
    FunnelFinder,
    HashFinder,
)

# Finders that take LeetCode #609 directory strings (Tiers 1, 2).
STRING_FINDERS = [DuplicateFinder, HashFinder]

# Finders that take a filesystem path and walk a real tree (Tiers 3, 4).
TREE_FINDERS = [FunnelFinder, DistributedFinder]


def norm(groups: list[list[str]]) -> list[list[str]]:
    """Sort a finder's output so it can be compared order-insensitively.

    The contract fixes neither group order nor path order within a group;
    sorting both levels makes the comparison canonical. Sorting (rather
    than set-ifying) preserves multiplicity, so a path wrongly emitted
    twice in one group would survive the sort and fail the assertion.
    """
    return sorted(sorted(group) for group in groups)


# ---------------------------------------------------------------------------
# Shared correctness — every string-input finder (Tiers 1, 2) must satisfy these.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_leetcode_609_example(finder_cls: type) -> None:
    """LeetCode #609 example 1: two duplicate groups spread across nested
    directories — the same content reached by different paths.
    """
    paths = [
        "root/a 1.txt(abcd) 2.txt(efgh)",
        "root/c 3.txt(abcd)",
        "root/c/d 4.txt(efgh)",
        "root 4.txt(efgh)",
    ]
    got = finder_cls().find(paths)
    assert norm(got) == norm(
        [
            ["root/a/1.txt", "root/c/3.txt"],
            ["root/a/2.txt", "root/c/d/4.txt", "root/4.txt"],
        ]
    )


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_no_duplicates_returns_empty(finder_cls: type) -> None:
    """Every file has unique content → no group is returned."""
    paths = [
        "root/a 1.txt(alpha) 2.txt(beta)",
        "root/b 3.txt(gamma)",
    ]
    assert finder_cls().find(paths) == []


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_all_files_share_content(finder_cls: type) -> None:
    """When every file has the same content, the result is a single group
    holding all of them.
    """
    paths = [
        "root/a 1.txt(same) 2.txt(same)",
        "root/b 3.txt(same)",
    ]
    got = finder_cls().find(paths)
    assert norm(got) == norm([["root/a/1.txt", "root/a/2.txt", "root/b/3.txt"]])


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_two_independent_groups(finder_cls: type) -> None:
    """Distinct contents form distinct groups; a file whose content is
    unique joins neither.
    """
    paths = [
        "d1 a.txt(xx) b.txt(yy)",
        "d2 c.txt(xx) d.txt(yy)",
        "d3 e.txt(unique)",
    ]
    got = finder_cls().find(paths)
    assert norm(got) == norm(
        [
            ["d1/a.txt", "d2/c.txt"],
            ["d1/b.txt", "d2/d.txt"],
        ]
    )


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_same_name_different_content_not_grouped(finder_cls: type) -> None:
    """The key is content, not file name: identically-named files holding
    different content are not duplicates.
    """
    paths = [
        "d1 report.txt(version_a)",
        "d2 report.txt(version_b)",
    ]
    assert finder_cls().find(paths) == []


@pytest.mark.parametrize("finder_cls", STRING_FINDERS)
def test_duplicates_within_one_directory(finder_cls: type) -> None:
    """Two equal-content files in the same directory still form a group —
    a path is "directory/name", so the two paths differ by file name.
    """
    paths = ["root/sub copy1.txt(data) copy2.txt(data)"]
    got = finder_cls().find(paths)
    assert norm(got) == norm([["root/sub/copy1.txt", "root/sub/copy2.txt"]])


# ---------------------------------------------------------------------------
# Shared correctness — every tree-walking finder (Tiers 3, 4) must satisfy
# these. The inputs exercise the lessons both finders share: walking a real
# tree, recursing into subdirectories, and producing the same duplicate
# groups via whatever discriminator each tier uses internally.
# ---------------------------------------------------------------------------


def write(path: Path, content: bytes) -> None:
    """Create path's parent directories, then write content to path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_groups_identical_files(finder_cls: type, tmp_path: Path) -> None:
    """Two byte-identical files in different subdirectories form one group."""
    write(tmp_path / "a" / "f1.txt", b"hello world")
    write(tmp_path / "b" / "f2.txt", b"hello world")
    got = finder_cls().find(str(tmp_path))
    assert norm(got) == norm(
        [[str(tmp_path / "a" / "f1.txt"), str(tmp_path / "b" / "f2.txt")]]
    )


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_unique_files_not_grouped(finder_cls: type, tmp_path: Path) -> None:
    """Files that all differ in content yield no group."""
    write(tmp_path / "a.txt", b"alpha")
    write(tmp_path / "b.txt", b"beta is longer")
    write(tmp_path / "c.txt", b"gamma is longer still")
    assert finder_cls().find(str(tmp_path)) == []


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_different_size_files_never_match(finder_cls: type, tmp_path: Path) -> None:
    """Files of different sizes cannot be duplicates."""
    write(tmp_path / "short.txt", b"abc")
    write(tmp_path / "long.txt", b"abcdefghij")
    assert finder_cls().find(str(tmp_path)) == []


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_same_size_different_content_not_grouped(
    finder_cls: type, tmp_path: Path
) -> None:
    """Same size, different content — the hash separates them."""
    write(tmp_path / "x.txt", b"AAAA")
    write(tmp_path / "y.txt", b"BBBB")
    assert finder_cls().find(str(tmp_path)) == []


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_walks_nested_directories(finder_cls: type, tmp_path: Path) -> None:
    """Duplicates buried several directory levels deep must still be found.
    For FunnelFinder this proves os.walk recurses; for DistributedFinder it
    proves the scan-job-spawns-scan-job pattern reaches the bottom.
    """
    write(tmp_path / "top.txt", b"deep content")
    write(tmp_path / "a" / "b" / "c" / "buried.txt", b"deep content")
    got = finder_cls().find(str(tmp_path))
    assert norm(got) == norm(
        [[str(tmp_path / "top.txt"), str(tmp_path / "a" / "b" / "c" / "buried.txt")]]
    )


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_empty_directory(finder_cls: type, tmp_path: Path) -> None:
    """An empty tree yields no groups."""
    assert finder_cls().find(str(tmp_path)) == []


@pytest.mark.parametrize("finder_cls", TREE_FINDERS)
def test_single_file(finder_cls: type, tmp_path: Path) -> None:
    """A lone file has nothing to pair with — no group."""
    write(tmp_path / "only.txt", b"lonely")
    assert finder_cls().find(str(tmp_path)) == []


# ---------------------------------------------------------------------------
# Tier 3 only: FunnelFinder's prefix_bytes knob.
#
# These exercise the funnel's stage 3 specifically: pairs of files that
# pass stage 1 (size) and stage 2 (prefix hash) together, so only the full
# hash can decide. FunnelFinder's prefix_bytes parameter lets the test pin
# the prefix length tight enough to force this case on tiny files.
# ---------------------------------------------------------------------------


def test_funnel_shared_prefix_different_tail_not_grouped(tmp_path: Path) -> None:
    """Two files of the same size whose first prefix_bytes are identical but
    whose tails differ pass stages 1 and 2 together — only the full hash
    separates them. A finder that trusted the prefix hash as final would
    wrongly group them.
    """
    write(tmp_path / "p.txt", b"SAMExxxx")
    write(tmp_path / "q.txt", b"SAMEyyyy")
    assert FunnelFinder(prefix_bytes=4).find(str(tmp_path)) == []


def test_funnel_shared_prefix_identical_files_grouped(tmp_path: Path) -> None:
    """Mirror of the above: identical content survives all three stages —
    same size, same prefix, same full hash — and is correctly grouped.
    """
    write(tmp_path / "p.txt", b"SAMExxxx")
    write(tmp_path / "q.txt", b"SAMExxxx")
    got = FunnelFinder(prefix_bytes=4).find(str(tmp_path))
    assert norm(got) == norm([[str(tmp_path / "p.txt"), str(tmp_path / "q.txt")]])


# ---------------------------------------------------------------------------
# Tier 4 only: DistributedFinder.
#
# DistributedFinder simulates a distributed scan service: workers pull
# directory-scan jobs from a Queue and recursively spawn child jobs for
# the subdirectories they find. These tests stress the two new lessons —
# transitive job spawn (a chain only the recursive spawn can reach) and
# concurrent writes to the shared content index (the db lock under load).
# ---------------------------------------------------------------------------


def test_distributed_terminates_on_deep_recursive_spawn(tmp_path: Path) -> None:
    """A 10-level-deep directory chain plus a shallow sibling, both holding
    the same content. Only the seed scan job is enqueued externally; the
    buried file is reached purely by transitively spawned scan jobs.
    The test returning at all is the proof that q.join() correctly waits
    for the whole transitive tree before the API thread proceeds.
    """
    deep = tmp_path
    for _ in range(10):
        deep = deep / "d"
    write(deep / "buried.txt", b"deep duplicate")
    write(tmp_path / "shallow.txt", b"deep duplicate")
    got = DistributedFinder().find(str(tmp_path))
    expected = {str(deep / "buried.txt"), str(tmp_path / "shallow.txt")}
    assert len(got) == 1
    assert set(got[0]) == expected


def test_distributed_concurrent_writes_to_db_are_atomic(tmp_path: Path) -> None:
    """20 subdirectories, each holding 5 files with identical content — 100
    files that all share one digest. With many workers racing, every write
    to db[that digest] must land; a missing lock would lose entries. The
    test is deterministic for a correctly-locked finder and probabilistic
    for an unlocked one (the GIL makes the race unlikely but not impossible).
    """
    for i in range(20):
        for j in range(5):
            write(tmp_path / f"d{i}" / f"f{j}.txt", b"shared content")
    got = DistributedFinder(max_workers=16).find(str(tmp_path))
    assert len(got) == 1
    expected = {
        str(tmp_path / f"d{i}" / f"f{j}.txt")
        for i in range(20)
        for j in range(5)
    }
    assert set(got[0]) == expected
