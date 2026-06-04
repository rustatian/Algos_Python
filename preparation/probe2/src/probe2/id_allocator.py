from threading import Lock
import collections

class IdAllocator:
    def __init__(self, n: int) -> None:
        """Manage IDs in range [0, n)."""
        self._max_id = n
        self._max_seen = 0
        self._ids: collections.deque[int] = collections.deque()
        self._seen: set[int] = set()
        self._lock = Lock()

    def allocate(self) -> int:
        """Return an unused ID. Raise if pool exhausted."""
        with self._lock:
            if len(self._ids) > 0:
                id = self._ids.popleft()
                self._seen.add(id)
                return id

            if self._max_seen >= self._max_id:
                raise RuntimeError(f"can't allocate more than {self._max_id}")

            id = self._max_seen
            self._max_seen += 1
            self._seen.add(id)
            return id

    def release(self, id: int) -> None:
        """Return id to the pool."""
        with self._lock:
            if id in self._seen:
                self._ids.append(id)
                self._seen.remove(id)
            else:
                raise RuntimeError(f"unknown id {id}")

# Example:
# pool = IdAllocator(1000)
# x = pool.allocate()   # e.g. 0
# y = pool.allocate()   # e.g. 1
# pool.release(x)
# z = pool.allocate()   # could be 0 again