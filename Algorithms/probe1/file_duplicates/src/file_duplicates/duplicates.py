"""Find Duplicate Files — a dedup problem (LeetCode #609).

Given a list of directory strings, return every group of files whose
contents are byte-for-byte identical. It is a bucket-by-key problem:
files that share a content key land in one bucket, and every bucket of
size >= 2 is a duplicate group.

Input:
    paths : list[str] — each string describes one directory in the form
        "dir name1(content1) name2(content2) ...": the first token is the
        directory path, every later token pairs a file name with its
        content in parentheses. A file's path is "dir/name".
Output:
    list[list[str]] — every group of >= 2 file paths sharing identical
    content. Files with unique content are omitted; group order and
    within-group order do not matter.

Example 1:
    Input:  paths = ["root/a 1.txt(abcd) 2.txt(efgh)",
                     "root/c 3.txt(abcd)",
                     "root/c/d 4.txt(efgh)",
                     "root 4.txt(efgh)"]
    Output: [["root/a/1.txt", "root/c/3.txt"],
             ["root/a/2.txt", "root/c/d/4.txt", "root/4.txt"]]
    Explanation: content "abcd" is shared by root/a/1.txt and
        root/c/3.txt; content "efgh" by the other three files.

Example 2:
    Input:  paths = ["root/a 1.txt(alpha) 2.txt(beta)",
                     "root/b 3.txt(gamma)"]
    Output: []
    Explanation: every file has unique content, so no group is returned.

Example 3:
    Input:  paths = ["d1 report.txt(version_a)",
                     "d2 report.txt(version_b)"]
    Output: []
    Explanation: the key is content, not file name — two files both named
        report.txt but holding different content are not duplicates.

Constraints:
    - A file's content is the text between its parentheses; names and
      contents contain no spaces or parentheses.
    - The crawl is over the given strings only; nothing is read from a
      real filesystem at this tier.

Four tiers keep the bucket-by-key shape; what escalates is the key —
and the cost of computing it. Tiers 1-2 take the directory-string input
above; Tiers 3-4 instead take a filesystem path and walk a real tree.

Tier 1 — DuplicateFinder:   the raw file content is the key.
Tier 2 — HashFinder:        a SHA-256 digest of the content is the key.
Tier 3 — FunnelFinder:      walks a real tree; size -> prefix -> full hash.
Tier 4 — DistributedFinder: recursive scan jobs + a shared content index;
                            a single-process simulation of a distributed
                            service. See README.md for the architecture.
"""

import hashlib
import os
import threading
from collections import defaultdict
from queue import Queue


class DuplicateFinder:
    """Tier 1: group LeetCode #609 directory strings by raw content.

    Input:
        paths : list[str] — directory strings in #609's format,
            "dir name1(content1) name2(content2) ...". A file's path is
            "dir/name".
    Output:
        list[list[str]] — every group of >= 2 file paths with identical
        content; files with unique content are omitted, order irrelevant.

    Example 1:
        Input:  paths = ["root/a 1.txt(abcd) 2.txt(efgh)",
                         "root/c 3.txt(abcd)",
                         "root/c/d 4.txt(efgh)",
                         "root 4.txt(efgh)"]
        Output: [["root/a/1.txt", "root/c/3.txt"],
                 ["root/a/2.txt", "root/c/d/4.txt", "root/4.txt"]]
        Explanation: content "abcd" is shared by root/a/1.txt and
            root/c/3.txt; content "efgh" by the other three files.

    Example 2:
        Input:  paths = ["root/a 1.txt(alpha) 2.txt(beta)",
                         "root/b 3.txt(gamma)"]
        Output: []
        Explanation: every file has unique content — no group is returned.

    Example 3:
        Input:  paths = ["d1 report.txt(version_a)",
                         "d2 report.txt(version_b)"]
        Output: []
        Explanation: the key is content, not file name — two files both
            named report.txt but holding different content are not
            duplicates.

    The input is #609's format — a list of directory strings, each

        "dir name1(content1) name2(content2) ..."

    where the first token is a directory path and every later token pairs
    a file name with its content. A file's path is "dir/name".

    Bucket every path in a dict keyed by raw content; each bucket holding
    two or more paths is a duplicate group. This is bucket-by-key — the
    shape of LeetCode #49 (Group Anagrams) — and the whole tier ladder is
    variations on what that key is.

    Standard library:
        collections.defaultdict(list) — a dict that creates a missing
            key's value on first access, so buckets[content].append(path)
            works without a separate "is this key present?" check.
        str.split() — with no argument, splits on runs of whitespace and
            discards empties, cleanly separating the directory token from
            the file tokens.

    Pseudocode:
        find(paths):
            buckets = defaultdict(list)          # content -> list of paths
            for entry in paths:
                tokens    = entry.split()        # ["dir", "n1(c1)", ...]
                directory = tokens[0]
                for token in tokens[1:]:         # token = "name(content)"
                    name, content = token[:-1].split("(")  # drop ")", split
                    buckets[content].append(directory + "/" + name)
            return [g for g in buckets.values() if len(g) >= 2]

    Complexity: O(total input size) — one parse pass, one dict insert per
    file. Space O(total input size) — every path and content is retained.

    Key point: the grouping key is content, never the file name —
    identically-named files with different content are not duplicates —
    and the >= 2 filter is "duplicate" made literal (a bucket of one is a
    unique file).
    """

    # content -> paths with root
    def _parse(self, files: list[str], root: str) -> dict[str, list[str]]:

        # root -> root/a
        d: dict[str, list[str]] = defaultdict(list)
        for file in files:
            idx1 = file.find("(")
            idx2 = file.find(")")
            cont = file[idx1 + 1 : idx2]
            d[cont].append(f"{root}/{file[:idx1]}")

        return d

    def find(self, paths: list[str]) -> list[list[str]]:
        dup: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            parts = p.split()
            root = parts[0]
            # content -> files with root
            dc: dict[str, list[str]] = self._parse(files=parts[1:], root=root)

            for k, v in dc.items():
                dup[k].extend(v)

        ans = []
        for v in dup.values():
            if len(v) == 1:
                continue
            ans.append(v)
        return ans


class HashFinder:
    """Tier 2: group LeetCode #609 directory strings by a SHA-256 digest.

    Input:
        paths : list[str] — directory strings in #609's format,
            "dir name1(content1) name2(content2) ...". A file's path is
            "dir/name".
    Output:
        list[list[str]] — every group of >= 2 file paths with identical
        content; files with unique content are omitted, order irrelevant.

    Example 1:
        Input:  paths = ["root/a 1.txt(abcd) 2.txt(efgh)",
                         "root/c 3.txt(abcd)",
                         "root/c/d 4.txt(efgh)",
                         "root 4.txt(efgh)"]
        Output: [["root/a/1.txt", "root/c/3.txt"],
                 ["root/a/2.txt", "root/c/d/4.txt", "root/4.txt"]]
        Explanation: "abcd" hashes to one digest, "efgh" to another; the
            two digests bucket the four paths into two groups.

    Example 2:
        Input:  paths = ["root/a 1.txt(alpha) 2.txt(beta)",
                         "root/b 3.txt(gamma)"]
        Output: []
        Explanation: three distinct contents hash to three distinct
            digests — every bucket holds one path, so no group is returned.

    Example 3:
        Input:  paths = ["d1 report.txt(version_a)",
                         "d2 report.txt(version_b)"]
        Output: []
        Explanation: different content hashes to different digests, so two
            files both named report.txt are not grouped — the key is the
            content's hash, never the file name.

    The same bucket-by-key algorithm as Tier 1 — but the key is no longer
    the file content itself; it is a SHA-256 digest of that content. That
    is the whole lesson of Tier 2: the key need not *be* the data. Tier 1
    used the entire content as a dict key — fine for short #609 strings,
    ruinous for real files, where a 4 GB file would mean a 4 GB key.
    SHA-256 collapses content of any size to a fixed 32-byte digest, so
    the bucket dict stays small however large the files grow. Identical
    content always hashes to the identical digest, and a collision
    between *different* contents is cryptographically infeasible, so
    grouping by digest is exactly as correct as grouping by content.

    Standard library:
        hashlib.sha256(data) — returns a hash object over the bytes
            `data`. .hexdigest() reads the digest out as a 64-character
            hex string; .digest() gives the same value as 32 raw bytes.
            Either is a fixed-size, hashable key — the digest does not
            grow with the content.
        str.encode() — SHA-256 hashes bytes, not str, so the content
            string is encoded (UTF-8) to bytes before being hashed.

    Pseudocode:
        find(paths):
            buckets = dict: digest -> list of file paths
            for entry in paths:
                directory, file_tokens = parse entry   # as in Tier 1
                for each (name, content) in file_tokens:
                    digest = sha256(content.encode()).hexdigest()
                    buckets[digest].append(directory + "/" + name)
            return [g for g in buckets.values() if len(g) >= 2]

    Complexity: O(total input size) — each content is hashed once, in
    time linear in its length. Space O(number of files): every bucket
    key is a fixed 32-byte digest regardless of content size — the gain
    over Tier 1, whose keys grew with the content they held.
    """

    def _parse(self, files: list[str], root: str) -> dict[str, list[str]]:
        # root -> root/a
        d: dict[str, list[str]] = defaultdict(list)
        for file in files:
            idx1 = file.find("(")
            idx2 = file.find(")")
            cont = hashlib.sha256(file[idx1 + 1 : idx2].encode()).hexdigest()
            d[cont].append(f"{root}/{file[:idx1]}")

        return d

    def find(self, paths: list[str]) -> list[list[str]]:
        dup: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            parts = p.split()
            root = parts[0]
            # content -> files with root
            dc: dict[str, list[str]] = self._parse(files=parts[1:], root=root)

            for k, v in dc.items():
                dup[k].extend(v)

        ans = []
        for v in dup.values():
            if len(v) == 1:
                continue
            ans.append(v)
        return ans


class FunnelFinder:
    """Tier 3: find duplicates on a real directory tree, via a
    size -> prefix-hash -> full-hash funnel.

    Input:
        root : str — a filesystem path; every file in the tree beneath it
            (walked recursively) is a candidate.
        Constructor — prefix_bytes : int — how many leading bytes the
            stage-2 prefix hash covers (default 4096).
    Output:
        list[list[str]] — every group of >= 2 file paths whose contents
        are byte-for-byte identical; unique files omitted, order irrelevant.

    Example 1:
        Input:  files under `root`:
                    root/a/f1.txt  ->  b"hello"
                    root/b/f2.txt  ->  b"hello"
                    root/c/f3.txt  ->  b"a longer, different file"
        Output: [["root/a/f1.txt", "root/b/f2.txt"]]
        Explanation: f1 and f2 are byte-identical -> one group. f3 has a
            unique size, so stage 1 proves it unique without reading a
            single byte of its content.

    Example 2:
        Input:  files under `root`:
                    root/x.bin  ->  data beginning "REPORT-v1..."
                    root/y.bin  ->  same size, beginning "REPORT-v1..."
                                    too, but differing in its final bytes
        Output: []
        Explanation: x and y share a size and a prefix, so they survive
            stage 1 (size) and stage 2 (prefix hash). Stage 3 hashes them
            in full, sees the differing tails, and keeps them apart — the
            prefix hash narrows the field, it never decides.

    Example 3:
        Input:  an empty directory `root`, or one holding a single file
        Output: []
        Explanation: a duplicate group needs >= 2 files; with zero or one
            file, no group can form.

    Tiers 1-2 took #609 directory *strings*; from Tier 3 on the finder
    walks a real directory tree on disk. That surfaces a new problem —
    cost: hashing every file in full means reading every byte of every
    file, and on a real tree most files are duplicates of nothing.

    The fix is a funnel — three bucket-by-key stages, cheapest first, each
    stage only ever processing the survivors of the one before it:

      Stage 1 — size. Bucket by file size. Two files of different sizes
        cannot be identical, so a size-bucket of one is a proven-unique
        file — discarded for zero bytes of content read (size is metadata).
      Stage 2 — prefix hash. Within each surviving size-bucket, bucket by
        a hash of the first `prefix_bytes` bytes. Files that differ early
        separate here, having read only a few KB each.
      Stage 3 — full hash. Within each surviving (size, prefix)-bucket,
        bucket by a hash of the *entire* file. Only files that matched on
        both size and prefix are ever read in full; the full-hash buckets
        of size >= 2 are the duplicate groups.

    The point: the expensive discriminator — a full read — runs only for
    the few files a cheap one could not already tell apart.

    Standard library:
        os.walk(root) — yields (dirpath, dirnames, filenames) for every
            directory in the tree; joining dirpath with each filename
            gives every file path. It does not descend into directory
            symlinks by default, so symlink cycles are not a concern.
        os.path.getsize(path) — the file's size in bytes, taken from
            filesystem metadata without opening the file.
        open(path, "rb") — opens the file in binary mode; .read(n) takes
            the first n bytes (the prefix), and reading in a loop streams
            the whole file for the full hash.
        hashlib.sha256 — as in Tier 2; for the full hash, feed it the
            file chunk by chunk with .update() so a file of any size is
            never held in memory all at once.
        collections.defaultdict(list) — the bucket dict at each stage.

    Pseudocode:
        find(root):
            files = [every file path under root, via os.walk]

            by_size = defaultdict(list)              # stage 1
            for path in files:
                by_size[getsize(path)].append(path)

            by_prefix = defaultdict(list)            # stage 2
            for size, group in by_size.items():
                if len(group) < 2:
                    continue                         # unique size -> done
                for path in group:
                    by_prefix[(size, prefix_hash(path))].append(path)

            by_full = defaultdict(list)              # stage 3
            for key, group in by_prefix.items():
                if len(group) < 2:
                    continue                         # unique prefix -> done
                for path in group:
                    by_full[full_hash(path)].append(path)

            return [g for g in by_full.values() if len(g) >= 2]

        prefix_hash(path): open path "rb", read prefix_bytes, sha256 them.
        full_hash(path):   open path "rb", sha256 the whole file, reading
                           it in fixed-size chunks fed to .update().

    Complexity: I/O is O(total bytes of the files that survive to stage 3)
    — not O(total bytes on disk). A tree where every file has a unique
    size reads no file content at all. Space O(number of files).
    """

    def __init__(self, prefix_bytes: int = 4096) -> None:
        self._prefix_bytes = prefix_bytes

    def find(self, root: str) -> list[list[str]]:
        by_size: dict[int, list[str]] = defaultdict(list)
        for dirpath, dirnames, filenames in os.walk(root):
            for file in filenames:
                sz = os.path.getsize(f"{dirpath}/{file}")
                by_size[sz].append(f"{dirpath}/{file}")

        by_prefix: dict[bytes, list[str]] = defaultdict(list)
        for size, group in by_size.items():
            if len(group) < 2:
                continue
            for file in group:
                with open(file, "rb") as f:
                    head = f.read(self._prefix_bytes)
                    by_prefix[head].append(file)

        by_full: dict[str, list[str]] = defaultdict(list)
        for pref, group in by_prefix.items():
            if len(group) < 2:
                continue
            for file in group:
                h = hashlib.sha256()
                with open(file, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        h.update(chunk)
                    by_full[h.hexdigest()].append(file)

        return [group for group in by_full.values() if len(group) >= 2]


class DistributedFinder:
    """Tier 4: find duplicates via recursive scan jobs writing to a shared
    content index — a single-process simulation of a distributed scan service.

    Input:
        root : str — a filesystem path; the simulated API endpoint's argument.
            The scan walks every file beneath it via recursive job spawn.
        Constructor — max_workers : int — number of worker threads in the
            simulated worker fleet (default 16).
    Output:
        list[list[str]] — every group of >= 2 file paths whose contents
        are byte-for-byte identical; unique files omitted, order irrelevant.

    Example 1:
        Input:  files under `root`:
                    root/a/f1.txt  ->  b"hello"
                    root/b/f2.txt  ->  b"hello"
                    root/c/f3.txt  ->  b"unique"
        Output: [["root/a/f1.txt", "root/b/f2.txt"]]
        Explanation: the API enqueues the seed scan(root) job. The worker
            that takes it lists root, finds three subdirectories, and
            enqueues a child scan job per subdir. Those child scans hash
            their files and write digests to the shared db: f1 and f2's
            matching digest puts them in one group; f3 is alone.

    Example 2:
        Input:  a 10-level-deep chain plus a shallow sibling, both holding
                the same content:
                    root/d/d/.../d/buried.txt    ->  b"deep"
                    root/elsewhere.txt           ->  b"deep"
        Output: [["root/d/d/.../d/buried.txt", "root/elsewhere.txt"]]
        Explanation: only the seed job is enqueued externally; each level
            spawns the next. The queue's unfinished-task counter reaches
            zero only after every transitive child has finished — exactly
            the "scan complete" signal the API waits on.

    Example 3:
        Input:  an empty directory `root`, or one holding a single file
        Output: []
        Explanation: a group needs >= 2 files; the scan still runs (one
            job processes the root) but no bucket reaches size 2.

    The distributed follow-up to this problem. Conceptually:

        [API] enqueues scan(root) into [job queue]. A fleet of [workers]
        drain the queue: each worker, processing one directory, enqueues
        a child scan job per subdirectory and writes each file's content
        digest to a shared [content-index DB]. The API waits on the
        queue's unfinished-task counter to reach zero (every transitive
        child has finished), then reads the duplicate groups from the DB.

    The job stream is self-feeding — the API enqueues exactly ONE seed
    job; the rest of the tree's jobs are generated dynamically by jobs
    themselves. See README.md for the full architecture write-up; this
    class is the single-process simulation.

    Single-process simulation mapping:
        API endpoint   ->  the find() method
        job queue      ->  queue.Queue
        worker fleet   ->  fixed pool of threading.Thread
        content DB     ->  defaultdict(list) guarded by threading.Lock
        job spawn      ->  q.put(child_path) inside a worker
        termination    ->  q.join() — the unfinished-task counter going
                           to zero is the "scan complete" signal

    Standard library:
        queue.Queue — thread-safe FIFO; its internal unfinished-task
            counter (raised by put, lowered by task_done) is what makes
            transitive-spawn termination detectable via q.join().
        threading.Thread — the simulated worker fleet, like QueueCrawler.
        threading.Lock — the db is shared mutable state; every
            db[digest].append(path) must run inside `with db_lock:`.
        os.scandir(path) — lists ONE directory's entries as DirEntry
            objects (.path, .is_dir(), .is_file()). Unlike os.walk it
            does NOT recurse — the recursion comes from the job queue.
        hashlib.sha256 — full-content fingerprint, streamed via .update()
            (the helper from Tier 3, reusable here as-is).

    Pseudocode:
        find(root):
            db      = defaultdict(list)        # digest -> [paths]
            db_lock = Lock()
            q       = Queue();  q.put(root)    # the API's seed job

            start max_workers threads running worker()
            q.join()                            # block until scan completes

            for _ in range(max_workers):        # sentinel-shutdown
                q.put(None)
            join every thread
            return [g for g in db.values() if len(g) >= 2]

        worker():                               # each pool thread loops here
            while True:
                directory = q.get()
                if directory is None:           # sentinel — exit
                    q.task_done(); break
                for entry in os.scandir(directory):
                    if entry.is_dir(follow_symlinks=False):
                        q.put(entry.path)       # spawn a child scan job
                    elif entry.is_file(follow_symlinks=False):
                        digest = full_hash(entry.path)
                        with db_lock:
                            db[digest].append(entry.path)
                q.task_done()                   # this directory done

        full_hash(path): stream the file through a fresh sha256 hasher
                         (the helper from Tier 3 — reusable here).

    Why a lock — even with the GIL:
        db[digest].append(path) is a dict lookup (which may insert the
        default empty list) followed by a list append — multiple
        bytecodes. The GIL keeps each bytecode atomic but can switch
        threads between them, so two workers racing on the same digest
        (or on the same missing key) can clobber each other's insert.
        The lock serializes the whole update.

    Termination — why q.join() works on a self-spawning job stream:
        Every put() bumps the queue's unfinished-tasks counter; every
        task_done() lowers it. A worker's task_done for its parent
        directory runs only AFTER it has put() each child — so while
        children are still pending, the counter cannot reach zero. Only
        when the whole tree's transitive children have finished does the
        counter touch zero; that's exactly when q.join() returns to the
        API. Identical machinery to QueueCrawler (Tier 4 of web_crawler).
    """

    def __init__(self, max_workers: int = 16) -> None:
        self._max_workers = max_workers

    def full_hash(self, path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                h.update(chunk)

        return h.hexdigest()

    def find(self, root: str) -> list[list[str]]:
        q = Queue()
        db_lock = threading.Lock()
        q.put(root)
        db = defaultdict(list)

        def worker() -> None:
            while True:
                directory = q.get()
                if directory is None:
                    q.task_done()
                    break
                for entry in os.scandir(directory):
                    if entry.is_dir(follow_symlinks=False):
                        q.put(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        digest = self.full_hash(entry.path)
                        with db_lock:
                            db[digest].append(entry.path)
                q.task_done()

            return None

        threads: list[threading.Thread] = []
        for _ in range(self._max_workers):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)

        # block queue
        q.join()

        for _ in range(self._max_workers):
            q.put(None)

        for t in threads:
            t.join()

        return [val for val in db.values() if len(val) >= 2]
