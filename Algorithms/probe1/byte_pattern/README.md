# Byte Pattern in a File

Find every offset where a `needle` byte-pattern occurs in a `haystack` —
including a haystack **too large to load into memory**. The headline
technique is the **Rabin-Karp rolling hash**; the "too large" twist is
solved by reading the stream in fixed chunks that **overlap by
`len(needle) − 1` bytes** so matches across a chunk boundary are not lost.

Modeled on the classic "find a byte pattern in a huge file" question.
Related LeetCode references: #28 (Find the Index of the First Occurrence),
#1044 (Longest Duplicate Substring — Rabin-Karp), #1392 (Longest Happy
Prefix — rolling hash / KMP), #187 (Repeated DNA Sequences).

## Problem

Return the start offset of every occurrence of `needle` in `haystack`
(overlapping occurrences included). The catch in the harder version: the
haystack is a multi-gigabyte file you cannot hold in RAM, so the search
must run over a **stream** in bounded memory.

```python
RabinKarpSearch().search(b"aaaa", b"aa")  # -> [0, 1, 2]

reader = io.BytesIO(b"xxxxNEEDLExxxx")
ChunkedRabinKarpSearch().search_stream(reader, b"NEEDLE", chunk_size=4)
# -> [4]   (found although "NEEDLE" is split across 4-byte chunks)
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `NaiveSearch` | compare every window byte-by-byte | the O(n·m) baseline; correct and obvious |
| 2 | `RabinKarpSearch` | rolling hash + verify-on-match | O(n+m) — slide the window's hash in O(1); the hash is a filter, the byte compare is truth |
| 3 | `ChunkedRabinKarpSearch` | chunked stream + `(m−1)`-byte overlap | bounded memory — search data that does not fit in RAM without missing boundary matches |
| 4 | `DistributedSearch` | shard the file across workers | the system-design follow-up — parallel scan with per-shard overlap |

Each tier answers the previous one's weak spot. Tier 1 re-compares the
needle at every position — O(n·m), quadratic on adversarial inputs. Tier 2
hashes the sliding window and updates that hash in O(1) per step (drop the
leading byte's weight, shift, add the trailing byte), comparing actual
bytes only when hashes match — so collisions can never cause a false
positive, and the average cost drops to O(n+m). Tier 3 removes the
"in-memory buffer" assumption: it reads the haystack in fixed chunks and
carries the last `m−1` bytes of each chunk into the next, so a match
straddling a boundary lands wholly inside one overlapped buffer. Memory is
O(chunk_size + m) no matter how large the file. Tier 4 splits the file
across machines.

### Why verify the bytes on a hash match

Two different windows can hash to the same value (a collision). The hash
is a fast **filter** that rejects almost all non-matches in O(1); the byte
compare is the **source of truth** that confirms the rare survivors.
Because real collisions are rare, the expensive compare runs about once
per genuine match, preserving the O(n+m) average.

### Why the overlap is exactly `len(needle) − 1` bytes

A match is `m` bytes. The furthest it can begin in one chunk and still
spill into the next is `m−1` bytes before the boundary. Carrying those
`m−1` trailing bytes forward guarantees every boundary-spanning match has
all `m` of its bytes inside a single overlapped buffer. One byte fewer and
a match split right at the seam would vanish. And because a buffer's
matches can only *start* in its first `len−m+1` positions (never inside the
carried tail), nothing is counted twice.

## Tier 4 — the system-design follow-up (distributed scan)

The follow-up: *search a file far too large for one machine — terabytes in
an object store — by fanning the scan across a worker fleet.*

**Opener questions.** One pattern or many (multi-pattern → Aho-Corasick
instead of a single rolling hash)? Is the file static or appended-to
live? Do we need *all* offsets, or just "exists / first occurrence"? Is the
data already chunked in the object store, and at what boundaries?

**Design sketch.**

```
   coordinator ── split file into byte ranges (with (m-1) overlap) ──► range jobs
                                                                          │
                          workers pull a range, fetch it, Rabin-Karp scan ◄┘
                                                                          │
                          emit absolute offsets ──► merged, deduped result
```

- **Range sharding with overlap.** The file's byte length is split into N
  ranges; each range is *extended by `m−1` bytes into the next* so a match
  on a shard boundary is wholly inside one shard — the single-machine chunk
  overlap, lifted to the cluster. A worker fetches only its range from the
  object store (an HTTP range request), so no machine ever holds the whole
  file.
- **Workers** run Tier 2's Rabin-Karp on their range and emit absolute
  offsets (range start + local offset).
- **Merge + dedup.** A match exactly on a boundary can be reported by the
  two adjacent shards (both see it, thanks to the overlap); the merge step
  sorts and dedupes offsets. (Alternatively, define each shard as
  responsible only for matches *starting* in its non-overlap region.)
- **Multi-pattern.** Searching for many needles at once switches the core
  from one rolling hash to **Aho-Corasick** (a trie of all patterns with
  failure links) so a single pass finds them all.

**Failures.** Worker dies mid-range → its range is idempotently re-assigned
(offsets are deterministic, so re-scanning is safe and the dedup absorbs
overlap). Object-store throttling → bounded concurrency / backoff per
worker. Skewed match density (one range with millions of hits) → stream
offsets out rather than buffer them; cap per-range result size and page.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `chunk_size` reads | HTTP range requests against an object store |
| `(m−1)`-byte chunk overlap | `(m−1)`-byte overlap between shard ranges |
| single-process loop | a coordinator fanning ranges to a worker fleet |
| `RabinKarpSearch` per buffer | per-shard scan (or Aho-Corasick for many needles) |
| in-memory result list | a merged, deduped, paged offset stream |

## Running the tests

```sh
uv run pytest Algorithms/byte_pattern/tests/ -q
```
