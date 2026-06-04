"""In-Memory File System — path-based design, tiered learning port.

Public classes:
    SimpleFileSystem          — Tier 1: ls / mkdir / add / read (#588).
    PathNormalizingFileSystem — Tier 2: resolves "." / ".." / "//" (#71).
    ThreadSafeFileSystem      — Tier 3: Tier 2 under one lock.

Tier 4 (DistributedFileSystem) is an architecture discussion, not code —
see README.md.
"""

from build_fs.build_fs import (
    PathNormalizingFileSystem,
    SimpleFileSystem,
    ThreadSafeFileSystem,
)

__all__ = [
    "SimpleFileSystem",
    "PathNormalizingFileSystem",
    "ThreadSafeFileSystem",
]
