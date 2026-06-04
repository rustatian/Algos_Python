"""In-Memory File System — path-based design (LeetCode #588).

Build a filesystem in memory addressed by absolute paths like
``/a/b/file``. The structure is a **trie of path components**: each node
is a directory (a dict of named children) or a file (a string of
content). Resolving a path means walking the trie one component at a time.

This package ports the problem as a tiered learning ladder:

Tier 1: SimpleFileSystem          — ls / mkdir / add / read (#588 verbatim).
Tier 2: PathNormalizingFileSystem — resolves ``.`` / ``..`` / ``//`` (#71).
Tier 3: ThreadSafeFileSystem      — Tier 2 under one lock; safe concurrency.
Tier 4: DistributedFileSystem     — HLD only (see README); metadata service.

Input (the shared surface):
    mkdir(path: str) -> None
        Create the directory and any missing parents. Idempotent.
    add_content_to_file(path: str, content: str) -> None
        Create the file (and parents) if absent, then APPEND content.
    read_content_from_file(path: str) -> str
        Return the file's full content.
    ls(path: str) -> list[str]
        If path is a file: ["<filename>"]. If a directory: the sorted
        names of its immediate children.

Output:
    ls returns a sorted list of names; read returns the file content.

Example 1 (#588 walkthrough):
    fs = SimpleFileSystem()
    fs.ls("/")                          -> []
    fs.mkdir("/a/b/c")
    fs.add_content_to_file("/a/b/c/d", "hello")
    fs.ls("/")                          -> ["a"]
    fs.read_content_from_file("/a/b/c/d") -> "hello"

Example 2 (add appends; ls on a file returns its name):
    fs = SimpleFileSystem()
    fs.add_content_to_file("/f", "ab")
    fs.add_content_to_file("/f", "cd")   # appends
    fs.read_content_from_file("/f")      -> "abcd"
    fs.ls("/f")                          -> ["f"]

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

import threading


class _Node:
    """One entry in the filesystem trie.

    A node is a *directory* (``is_file`` False, holds named ``children``)
    or a *file* (``is_file`` True, holds string ``content``). The root is
    always a directory.
    """

    __slots__ = ("children", "is_file", "content")

    def __init__(self) -> None:
        self.children: dict[str, "_Node"] = {}
        self.is_file: bool = False
        self.content: str = ""


class SimpleFileSystem:
    """Tier 1: in-memory filesystem addressed by absolute paths (#588).

    Input / Output:
        mkdir / add_content_to_file / read_content_from_file / ls, as
        described in the module docstring.

    Example:
        fs = SimpleFileSystem()
        fs.mkdir("/a/b")
        fs.add_content_to_file("/a/b/f", "data")
        fs.ls("/a")                          -> ["b"]
        fs.read_content_from_file("/a/b/f")  -> "data"

    Standard library:
        dict — each directory's children, keyed by component name. O(1)
            descent per path component.

    Pseudocode:
        split(path):  return [c for c in path.split("/") if c]   # drop ""

        mkdir(path):       walk components, creating missing child dirs.
        add(path, text):   walk+create to the file node; is_file=True;
                           content += text  (create-or-append).
        read(path):        walk to the node; return content.
        ls(path):          walk to the node;
                           if file -> [last component];
                           else    -> sorted(children).

    Why ls returns SORTED names:
        #588 specifies lexicographic order, and a deterministic listing is
        what callers (and tests) expect from a directory walk.

    Why add_content_to_file APPENDS (not overwrites):
        #588's contract: repeated writes to the same path concatenate.
        A fresh file starts at "" so the first add is effectively a write.

    Complexity:
        Each op is O(P) to walk P path components, plus O(K log K) for ls
        sorting a directory of K children. Storage O(total content + nodes).
    """

    def __init__(self) -> None:
        self._root = _Node()

    def _split(self, path: str) -> list[str]:
        # Drop empty components so "/", "/a/", and "/a//b" all behave.
        return [c for c in path.split("/") if c]

    def _walk_to(self, parts: list[str]) -> _Node:
        """Descend to the node named by parts (assumes it exists)."""
        node = self._root
        for name in parts:
            node = node.children[name]
        return node

    def _walk_creating(self, parts: list[str]) -> _Node:
        """Descend to the node named by parts, creating missing dirs."""
        node = self._root
        for name in parts:
            if name not in node.children:
                node.children[name] = _Node()
            node = node.children[name]
        return node

    def mkdir(self, path: str) -> None:
        self._walk_creating(self._split(path))

    def add_content_to_file(self, path: str, content: str) -> None:
        node = self._walk_creating(self._split(path))
        node.is_file = True
        node.content += content  # create-or-append

    def read_content_from_file(self, path: str) -> str:
        return self._walk_to(self._split(path)).content

    def ls(self, path: str) -> list[str]:
        parts = self._split(path)
        node = self._walk_to(parts)
        if node.is_file:
            # Listing a file yields just its own name.
            return [parts[-1]]
        return sorted(node.children)


class PathNormalizingFileSystem(SimpleFileSystem):
    """Tier 2: Tier 1 plus real path normalization (LeetCode #71).

    Tier 1 only drops empty components. Real paths also contain ``.`` (this
    directory) and ``..`` (parent). This tier canonicalizes a path before
    resolving it, so ``/a/b/../c`` and ``/a/./c`` both resolve to ``/a/c``.

    Input / Output:
        Identical surface to Tier 1; paths may now contain ``.`` and ``..``.

    Example:
        fs = PathNormalizingFileSystem()
        fs.mkdir("/a/b")
        fs.add_content_to_file("/a/b/../b/f", "x")  # == /a/b/f
        fs.read_content_from_file("/a/b/f")          -> "x"

    Pseudocode (Simplify Path, #71):
        split(path):
            stack = []
            for c in path.split("/"):
                if c in ("", "."): continue
                if c == "..":
                    if stack: stack.pop()      # go up one level
                else:
                    stack.append(c)
            return stack

    Why ``..`` at the root is a no-op (not an error):
        ``/..`` resolves to ``/`` on real filesystems — the root has no
        parent, so popping an empty stack is simply ignored.

    Complexity:
        Normalization is O(P) over path components; everything else is
        inherited from Tier 1 unchanged.
    """

    def _split(self, path: str) -> list[str]:
        stack: list[str] = []
        for c in path.split("/"):
            if c == "" or c == ".":
                continue
            if c == "..":
                if stack:  # ".." above root is a no-op
                    stack.pop()
            else:
                stack.append(c)
        return stack


class ThreadSafeFileSystem(PathNormalizingFileSystem):
    """Tier 3: Tier 2 made safe for concurrent threads with one lock.

    Each operation mutates or reads the shared trie. Without a lock, two
    threads creating ``/a/b`` and ``/a/c`` at once could race on creating
    the shared ``/a`` directory, and a reader could observe a half-built
    node. One lock per operation serializes them.

    Input / Output:
        Identical to Tier 2; every call is now atomic w.r.t. other threads.

    Standard library:
        threading.Lock — held for the whole operation via ``with``.

    Why coarse-grained (one lock for the whole tree):
        Path operations touch a chain of nodes; fine-grained per-node
        locking risks deadlock (lock-ordering) for little gain at this
        scale. One tree-wide lock is simple and correct; Tier 4's sharding
        by path prefix is what restores parallelism across machines.

    Complexity:
        Same as Tier 2 plus lock acquire/release; throughput bounded by the
        single lock under contention.
    """

    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def mkdir(self, path: str) -> None:
        with self._lock:
            super().mkdir(path)

    def add_content_to_file(self, path: str, content: str) -> None:
        with self._lock:
            super().add_content_to_file(path, content)

    def read_content_from_file(self, path: str) -> str:
        with self._lock:
            return super().read_content_from_file(path)

    def ls(self, path: str) -> list[str]:
        with self._lock:
            return super().ls(path)
