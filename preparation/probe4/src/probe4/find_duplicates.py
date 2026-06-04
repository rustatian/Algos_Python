import hashlib
import os
from collections import defaultdict


def list_dir(path: str) -> list[str]:
    # Returns names of entries directly under path (files and subdirs, no recursion).
    return []


def is_dir(path: str) -> bool:
    return True


def file_size(path: str) -> int:
    # Bytes. O(1), reads metadata only.
    return 0


def read_file(path: str) -> bytes:
    # Reads entire file into memory. Expensive for large files.
    return bytes()


def find_duplicates(root: str) -> list[list[str]]:
    by_size: dict[int, list[str]] = defaultdict(list)
    start = [root]
    while start:
        # can be:
        # dir -> /root/bar
        # file -> /root/bar.txt
        node = start.pop()
        if is_dir(node):
            dirs = list_dir(node)
            for directory in dirs:
                # /root/bar/foo
                start.append(os.path.join(node, directory))
        else:
            # we have a file /root/bar/foo.txt
            # 100 -> /root/bar/foo.txt
            # so we're saving the full path
            by_size[file_size(node)].append(node)

    for k in list(by_size.keys()):
        if len(by_size[k]) <= 1:
            del by_size[k]

    by_hash_1: dict[str, list[str]] = defaultdict(list)

    for group in by_size.values():
        for file in group:
            # we don't have a specified API for reading a part of the file
            # I'll use open stdlib function for that
            with open(file, "rb") as f:
                chunk = f.read(65536)
                by_hash_1[hashlib.sha256(chunk).hexdigest()].append(file)

    for kk in list(by_hash_1.keys()):
        if len(by_hash_1[kk]) <= 1:
            del by_hash_1[kk]

    by_hash_2: dict[str, list[str]] = defaultdict(list)
    for group in by_hash_1.values():
        for file in group:
            h = hashlib.sha256()
            # we don't have an API -> I'll use open
            with open(file, "rb") as f:
                while True:
                    has_done = True
                    try:
                        chunk = f.read(65536)
                        if not chunk:
                            # reached the end
                            break
                        h.update(chunk)
                    except IOError:
                        # probably put that file for investigation
                        # can be a permission error, fs error
                        has_done = False
                        break
                if has_done:
                    by_hash_2[h.hexdigest()].append(file)

    return [kk for kk in by_hash_2.values() if len(kk) > 1]
