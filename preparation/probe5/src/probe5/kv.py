import datetime
import threading
import time
from heapq import heappop, heappush


class KVStore:
    def __init__(self):
        # value-expirity-version
        self._store: dict[str, tuple[str, float | None, int]] = {}
        # (expitiry, key, version)
        self._heap: list[tuple[float, str, int]] = []

        # dictionary with versions key -> version, key never deleted
        self._versions: dict[str, int] = {}

        self._bt = threading.Thread(target=self._sweep_expire)
        self._bt.start()
        self._lock = threading.Lock()

    def _sweep_expire(self) -> None:
        while True:
            time.sleep(60)
            with self._lock:
                # while checking, time can move a few seconds further, but these few seconds
                # we'll swipe on get. time.monotonic call is not free to use it in while loop
                self._lazy_delete()

    def _check_helper(self, **kwargs):
        for name, value in kwargs.items():
            if value is None:
                raise ValueError(f"{name} cannot be None")

            if not isinstance(value, str):
                raise ValueError(f"{name} must be string")

            if value == "":
                raise ValueError(f"{name} cannot be empty")

    def _lazy_delete(self) -> None:
        # no lock here, should be called from the function with lock, not thread safe
        now = time.monotonic()
        while self._heap:
            if self._heap[0][0] > now:
                break

            # ttl-key-version
            heap_item: tuple[float, str, int] = heappop(self._heap)
            if heap_item[1] in self._store:
                dict_item = self._store[heap_item[1]]
                # if the version of dict and heap are the same - remove it from the dict as well as expired
                if dict_item[2] == heap_item[2]:
                    del self._store[heap_item[1]]

    def put(self, key: str, value: str, ttl_seconds: float | None = None) -> None:
        self._check_helper(key=key, value=value)
        with self._lock:
            self._lazy_delete()
            # no ttl - simply overwrite old value
            # 0 -> to update to 1
            version = self._versions.get(key, 0)
            # bump the version, 1 or self._versions[key] + 1
            version += 1
            self._versions[key] = version

            if not ttl_seconds:
                self._store[key] = (value, None, version)
                return None

            expiration_time = time.monotonic() + ttl_seconds
            self._store[key] = (value, expiration_time, version)
            heappush(self._heap, (expiration_time, key, version))

        return None

    def get(self, key: str) -> str | None:
        with self._lock:
            self._lazy_delete()
            if key in self._store:
                return self._store[key][0]
            return None

    def delete(self, key: str) -> bool:
        with self._lock:
            self._lazy_delete()
            if key in self._store:
                del self._store[key]
                return True
            return False
