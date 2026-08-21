# In-Memory File System

Build a filesystem in memory, addressed by absolute paths like
`/a/b/file`. A **trie of path components**: each node is a directory (a
dict of named children) or a file (a string of content). Resolving a path
is walking the trie one component at a time.

Modeled on LeetCode #588 (Design In-Memory File System) and #1166 (Design
File System), plus the classic "in-memory FS" question. Related: #71
(Simplify Path — the normalization in Tier 2), #208 (Implement Trie).

## Problem

Four operations over absolute paths:

- `mkdir(path)` — create the directory and any missing parents (idempotent).
- `add_content_to_file(path, content)` — create the file (and parents) if
  absent, then **append** `content`.
- `read_content_from_file(path) -> str` — return the file's content.
- `ls(path) -> list[str]` — for a file, `["<filename>"]`; for a directory,
  the **sorted** names of its immediate children.

```python
fs = SimpleFileSystem()
fs.mkdir("/a/b/c")
fs.add_content_to_file("/a/b/c/d", "hello")
fs.ls("/")  # -> ["a"]
fs.read_content_from_file("/a/b/c/d")  # -> "hello"
```

The data structure is the insight: a tree where every directory holds a
`dict[name -> node]`. Lookups, inserts, and directory listings are all
just dict operations at each level — O(P) for a path of P components.

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `SimpleFileSystem` | trie of components; dict children | the algorithm — path walk, create-or-append, file-vs-dir node (#588) |
| 2 | `PathNormalizingFileSystem` | resolve `.` / `..` / `//` before walking | real paths — the Simplify Path stack algorithm (#71) |
| 3 | `ThreadSafeFileSystem` | Tier 2 under one lock | concurrency — racing creates of a shared parent directory |
| 4 | `DistributedFileSystem` | sharded metadata + content store | the system-design follow-up — a metadata service over an object store |

Every tier shares the four-operation surface.

Each tier answers the previous one's weak spot. Tier 1 only drops empty
components, so it cannot handle the `.` and `..` that real paths carry.
Tier 2 adds the Simplify Path stack — `..` pops a component, `.` is
ignored — so `/a/b/../c` resolves to `/a/c`. Tier 3 makes it
thread-safe: concurrent `mkdir("/a/b")` and `mkdir("/a/c")` would
otherwise race on creating the shared `/a` node, so each operation runs
under one tree-wide lock. Tier 4 leaves the single machine.

### Why a trie and not a flat `dict[fullpath -> content]`

A flat dict makes `read`/`add` O(1) but makes `ls("/a")` O(N) — you must
scan every key for the `/a/` prefix — and makes "is `/a` a directory?"
ambiguous. The trie makes `ls` O(children) and models the directory
hierarchy directly, which is what every real filesystem (and inode table)
does.

## Tier 4 — the system-design follow-up (distributed filesystem)

The follow-up: *store billions of files across many machines, far more
than one host's RAM or disk, served to many clients.* Real distributed
filesystems (GFS/HDFS, and cloud blob stores) split into two planes:

```
   client ─► metadata service (the trie, sharded)  ──► where do the bytes live?
                                                         │
              content store (object store / chunk servers) ◄─ read/write bytes
```

**Opener questions.** File count and size distribution (many tiny files
vs few huge)? Read/write ratio? Consistency on concurrent writes to one
file — last-writer-wins, append-only, or locked? Directory-listing scale
(millions of entries in one dir)? Durability target?

**Design sketch.**

- **Metadata plane** — the trie/inode table: path → `{type, children,
  size, chunk-ids, mtime, acl}`. Sharded by path-prefix hash so subtrees
  spread across metadata servers. This is exactly Tier 1's structure,
  persisted and partitioned (the GFS "master", HDFS "NameNode").
- **Content plane** — file bytes split into fixed chunks, each replicated
  across chunk servers / stored in an object store (S3). Metadata holds the
  chunk-id list; clients read/write chunks directly, keeping the metadata
  server off the data path.
- **`mkdir`/`ls`** are pure metadata operations. **`add_content_to_file`**
  writes chunks to the content plane, then updates the metadata chunk list
  (a metadata transaction). **`read`** resolves the path in metadata, then
  streams chunks from the content plane.
- **Concurrency** — Tier 3's tree-wide lock becomes per-path / per-subtree
  locks (or leases) in the metadata service, plus per-file write
  coordination (append-only logs sidestep most write conflicts).

**Failures.** Metadata server loss → replicate the metadata log (Raft/Paxos
for the namespace). Chunk server loss → replication factor R; re-replicate
under-replicated chunks. A giant directory → paginate `ls`; shard the
directory's children across metadata nodes. Orphaned chunks after a failed
write → background GC reconciles the chunk store against metadata.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `_Node` trie | the metadata/inode table (GFS master / HDFS NameNode) |
| `node.content` string | chunked bytes in an object store / chunk servers |
| `threading.Lock` (Tier 3) | per-subtree metadata locks/leases + Raft on the namespace |
| single process | sharded metadata plane + replicated content plane |

## Running the tests

```sh
uv run pytest Algorithms/build_fs/tests/ -q
```
