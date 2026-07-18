"""Tests for the byte-pattern search ladder.

NaiveSearch and RabinKarpSearch share the (haystack, needle) -> offsets
contract, so those tests are parametrized over BUFFER_SEARCHERS and assert
the two agree. ChunkedRabinKarpSearch operates on a stream and is tested
against a BytesIO across a range of chunk sizes, including sizes far
smaller than the needle to stress the boundary-overlap logic.
"""

import io

import pytest

from byte_pattern import ChunkedRabinKarpSearch, NaiveSearch, RabinKarpSearch

# Tier 1 and Tier 2 share the in-memory search contract.
BUFFER_SEARCHERS = [NaiveSearch, RabinKarpSearch]


# ----------------------------------------------------------------------
# Tier 1 / Tier 2 — in-memory search (shared contract).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_single_match(cls: type) -> None:
    assert cls().search(b"hello world", b"world") == [6]


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_no_match(cls: type) -> None:
    assert cls().search(b"abcdef", b"xyz") == []


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_overlapping_matches(cls: type) -> None:
    assert cls().search(b"aaaa", b"aa") == [0, 1, 2]


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_match_at_start_and_end(cls: type) -> None:
    assert cls().search(b"abXXab", b"ab") == [0, 4]


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_needle_equals_haystack(cls: type) -> None:
    assert cls().search(b"abc", b"abc") == [0]


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_needle_longer_than_haystack(cls: type) -> None:
    assert cls().search(b"ab", b"abc") == []


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_empty_needle(cls: type) -> None:
    assert cls().search(b"abc", b"") == []


@pytest.mark.parametrize("cls", BUFFER_SEARCHERS)
def test_binary_bytes_with_zeros(cls: type) -> None:
    """Works on arbitrary bytes, including NUL — it is byte search, not text."""
    assert cls().search(b"\x00\x01\x00\x01\x00", b"\x00\x01") == [0, 2]


def test_naive_and_rabin_karp_agree_on_a_larger_input() -> None:
    """Cross-check the rolling hash against brute force on a longer input."""
    haystack = b"the quick brown fox jumps over the lazy dog, the end" * 5
    for needle in (b"the", b"fox", b"zzz", b"o", b" the "):
        assert RabinKarpSearch().search(haystack, needle) == NaiveSearch().search(
            haystack, needle
        )


# ----------------------------------------------------------------------
# Tier 3 — chunked stream search (bounded memory + boundary overlap).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 4, 7, 16, 65536])
def test_stream_finds_boundary_spanning_match(chunk_size: int) -> None:
    """The match must be found regardless of where chunk boundaries fall —
    even when chunk_size is smaller than the needle.
    """
    data = b"xxxxNEEDLExxxx"
    reader = io.BytesIO(data)
    got = ChunkedRabinKarpSearch().search_stream(reader, b"NEEDLE", chunk_size=chunk_size)
    assert got == [4]


@pytest.mark.parametrize("chunk_size", [1, 3, 5, 1024])
def test_stream_matches_buffer_search(chunk_size: int) -> None:
    """The stream result must equal an in-memory search of the same data."""
    data = b"abracadabra abracadabra, abra!" * 3
    needle = b"abra"
    expected = RabinKarpSearch().search(data, needle)
    got = ChunkedRabinKarpSearch().search_stream(
        io.BytesIO(data), needle, chunk_size=chunk_size
    )
    assert got == expected


def test_stream_no_match() -> None:
    got = ChunkedRabinKarpSearch().search_stream(io.BytesIO(b"hello"), b"zzz")
    assert got == []


def test_stream_empty_needle() -> None:
    got = ChunkedRabinKarpSearch().search_stream(io.BytesIO(b"hello"), b"")
    assert got == []


def test_stream_overlapping_matches_across_chunks() -> None:
    """Overlapping matches that also span boundaries are all found once."""
    data = b"aaaaaa"  # "aa" occurs at 0,1,2,3,4
    got = ChunkedRabinKarpSearch().search_stream(io.BytesIO(data), b"aa", chunk_size=2)
    assert got == [0, 1, 2, 3, 4]


def test_stream_match_exactly_at_chunk_boundary() -> None:
    """A needle whose first byte ends one chunk and rest begins the next."""
    # chunk_size=5: chunks are "AAAAB","BBBB...". Needle "BB" spans the seam.
    data = b"AAAABBBBB"
    got = ChunkedRabinKarpSearch().search_stream(io.BytesIO(data), b"AB", chunk_size=5)
    assert got == [3]
