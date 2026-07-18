"""Find a byte pattern in a file too large to load (Rabin-Karp).

Find every offset at which a ``needle`` byte-pattern occurs in a
``haystack`` — including a haystack so large it cannot be held in memory
at once. The headline technique is the **Rabin-Karp rolling hash**: hash a
sliding window in O(1) per step by removing the leading byte and adding
the trailing one, and only fall back to a full byte compare when the hash
matches (to rule out the rare hash collision).

The "too large to load" twist is solved by reading the stream in fixed
chunks with an **overlap of (pattern_length − 1) bytes** between
consecutive chunks, so a match straddling a chunk boundary is never
missed.

This package ports the problem as a tiered learning ladder:

Tier 1: NaiveSearch            — O(n·m) brute force over an in-memory buffer.
Tier 2: RabinKarpSearch        — O(n+m) rolling hash over an in-memory buffer.
Tier 3: ChunkedRabinKarpSearch — stream search: bounded memory, overlap reads.
Tier 4: DistributedSearch      — HLD only (see README); shard the file.

Input:
    NaiveSearch / RabinKarpSearch:
        search(haystack: bytes, needle: bytes) -> list[int]
    ChunkedRabinKarpSearch:
        search_stream(reader, needle: bytes, chunk_size: int = 65536) -> list[int]
        reader — any object with ``read(n) -> bytes`` (an open file, BytesIO).
Output:
    A sorted list of every START offset where ``needle`` occurs (overlapping
    occurrences included). Empty list if needle is empty or longer than the
    data.

Example 1 (all occurrences, including overlapping):
    RabinKarpSearch().search(b"aaaa", b"aa")  -> [0, 1, 2]

Example 2 (no match):
    RabinKarpSearch().search(b"abcdef", b"xyz")  -> []

Example 3 (boundary-spanning match across chunks):
    data   = b"....NEEDLE...."
    reader = io.BytesIO(data)
    ChunkedRabinKarpSearch().search_stream(reader, b"NEEDLE", chunk_size=4)
    -> [4]   # found even though "NEEDLE" is split across 4-byte chunks

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

from typing import Protocol

# Rolling-hash parameters. BASE is the alphabet size (a byte is 0..255);
# MOD is a large prime so hash values stay bounded and collisions are rare.
_BASE = 256
_MOD = (1 << 61) - 1  # a Mersenne prime; big enough to make collisions rare


class _ByteReader(Protocol):
    """Anything with a ``read(n)`` returning up to n bytes (b"" at EOF).

    Matches ``io.BytesIO``, an open binary file, a socket wrapper, etc. The
    stream tier depends only on this tiny surface so it never needs the
    whole haystack in memory.
    """

    def read(self, n: int) -> bytes: ...


class NaiveSearch:
    """Tier 1: brute-force substring search over an in-memory buffer.

    Check every start position; at each, compare the needle byte by byte.
    Correct and obvious — the baseline the rolling hash improves on.

    Input / Output:
        search(haystack: bytes, needle: bytes) -> list[int] of start offsets.

    Example:
        NaiveSearch().search(b"abab", b"ab")  -> [0, 2]

    Standard library:
        Slicing on ``bytes`` — ``haystack[i:i+m] == needle`` is a C-level
        compare, so this is the clean expression of "compare the window".

    Pseudocode:
        for start in 0 .. n-m:
            if haystack[start : start+m] == needle:
                emit start

    Complexity:
        Time O(n·m) worst case (every window fully compared); space O(1).
        The worst case (e.g. haystack b"aaaa...a", needle b"aa...ab") is
        exactly what Rabin-Karp's O(1)-per-window hashing avoids.
    """

    def search(self, haystack: bytes, needle: bytes) -> list[int]:
        n, m = len(haystack), len(needle)
        if m == 0 or m > n:
            return []
        return [start for start in range(n - m + 1) if haystack[start : start + m] == needle]


class RabinKarpSearch:
    """Tier 2: Rabin-Karp rolling-hash search over an in-memory buffer.

    Hash the needle and the first window once; then slide the window one
    byte at a time, updating the hash in O(1) (remove the leading byte's
    contribution, shift, add the new trailing byte). Compare full bytes
    ONLY when the hashes match, so the rare collision can never produce a
    false positive.

    Input / Output:
        search(haystack: bytes, needle: bytes) -> list[int] of start offsets.

    Example:
        RabinKarpSearch().search(b"aaaa", b"aa")  -> [0, 1, 2]

    Standard library:
        pow(base, exp, mod) — modular exponentiation, used once to
            precompute BASE^(m-1) mod MOD (the weight of the leading byte
            that the roll removes). Python's big ints make the modular
            arithmetic exact.

    Pseudocode:
        high = BASE^(m-1) mod MOD          # weight of the window's first byte
        needle_hash, window_hash = hash(needle), hash(haystack[0:m])
        for start in 0 .. n-m:
            if window_hash == needle_hash and haystack[start:start+m] == needle:
                emit start                  # verify bytes — guard collisions
            if start < n-m:                 # roll to the next window
                window_hash = ((window_hash - haystack[start]*high) * BASE
                               + haystack[start+m]) mod MOD

    Why verify the bytes on a hash match:
        Two different windows can share a hash (a collision). Without the
        byte compare, a collision would be reported as a false match. The
        hash is a fast FILTER; the compare is the SOURCE OF TRUTH. Because
        collisions are rare, the expensive compare runs ~once per real
        match, keeping the average cost O(n+m).

    Why subtract ``haystack[start] * high`` before multiplying by BASE:
        The leading byte contributed ``byte * BASE^(m-1)`` to the hash.
        Removing that, then multiplying the rest by BASE and adding the new
        byte, shifts the whole window left one position — the "rolling"
        step. Python's ``%`` returns a non-negative result even after the
        subtraction goes negative, so no manual fix-up is needed.

    Complexity:
        Time O(n+m) expected; O(n·m) only in the pathological all-collisions
        case. Space O(1).
    """

    def search(self, haystack: bytes, needle: bytes) -> list[int]:
        n, m = len(haystack), len(needle)
        if m == 0 or m > n:
            return []

        high = pow(_BASE, m - 1, _MOD)  # weight of the leading byte
        needle_hash = 0
        window_hash = 0
        for i in range(m):
            needle_hash = (needle_hash * _BASE + needle[i]) % _MOD
            window_hash = (window_hash * _BASE + haystack[i]) % _MOD

        result: list[int] = []
        for start in range(n - m + 1):
            # Hash is a filter; the slice compare is the source of truth.
            if window_hash == needle_hash and haystack[start : start + m] == needle:
                result.append(start)
            if start < n - m:
                # Roll the window one byte to the right in O(1).
                window_hash = (
                    (window_hash - haystack[start] * high) * _BASE
                    + haystack[start + m]
                ) % _MOD
        return result


class ChunkedRabinKarpSearch:
    """Tier 3: search a stream too large to load, in bounded memory.

    Read the haystack in fixed-size chunks and search each. The trick is to
    prepend the last ``len(needle) - 1`` bytes of the previous chunk to the
    next one (the *overlap*), so a match that straddles a chunk boundary is
    found exactly once. Memory stays O(chunk_size + needle), independent of
    the total file size.

    Input:
        search_stream(reader, needle, chunk_size=65536) -> list[int]
            reader — any object with ``read(n) -> bytes`` (file / BytesIO).
    Output:
        Absolute start offsets of every occurrence, in order.

    Example:
        reader = io.BytesIO(b"xxxxNEEDLExxxx")
        ChunkedRabinKarpSearch().search_stream(reader, b"NEEDLE", chunk_size=4)
        -> [4]    # matched despite "NEEDLE" spanning several 4-byte chunks

    Standard library:
        (Reuses Tier 2's RabinKarpSearch on each overlapped buffer.)

    Pseudocode:
        overlap = b""; base = 0            # base = absolute offset of overlap[0]
        loop:
            chunk = reader.read(chunk_size)
            if not chunk: stop
            buf = overlap + chunk
            for rel in RabinKarp(buf, needle): emit base + rel
            keep = min(m-1, len(buf))      # tail to carry into the next buffer
            base += len(buf) - keep
            overlap = buf[len(buf)-keep:]

    Why the overlap is exactly (needle_len − 1) bytes:
        A match is ``m`` bytes long. The most it can straddle a boundary
        while still beginning in the previous chunk is by starting at the
        very last position whose match would need the next chunk — i.e. up
        to ``m-1`` bytes before the boundary. Carrying ``m-1`` trailing
        bytes forward guarantees every boundary-spanning match has all its
        bytes in some single buffer. One fewer byte and a match split right
        at the seam would be lost.

    Why there is no double counting:
        Within a buffer, a match can only START at positions ``0 .. len-m``;
        the carried overlap occupies the last ``m-1`` bytes
        (``len-m+1 .. len-1``). So no match found in a buffer begins inside
        the region that gets carried forward — each match is found in
        exactly one buffer.

    Complexity:
        Time O(total_bytes + matches·m); memory O(chunk_size + m) regardless
        of file size — the property that makes "too large to load" tractable.
    """

    def search_stream(
        self, reader: _ByteReader, needle: bytes, chunk_size: int = 65536
    ) -> list[int]:
        m = len(needle)
        if m == 0:
            return []

        inner = RabinKarpSearch()
        result: list[int] = []
        overlap = b""
        base = 0  # absolute offset in the stream of overlap[0]

        while True:
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            buf = overlap + chunk
            for rel in inner.search(buf, needle):
                result.append(base + rel)
            # Carry the last (m-1) bytes forward to catch boundary matches.
            keep = min(m - 1, len(buf))
            base += len(buf) - keep
            overlap = buf[len(buf) - keep :]

        return result
