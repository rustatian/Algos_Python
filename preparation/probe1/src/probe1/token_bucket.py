import collections
import time
from threading import Lock
from time import monotonic

class TokenBucket:
    def __init__(self, capacity: int, refill_rate_per_sec: float):
        self._capacity = capacity
        self._refill_rate = refill_rate_per_sec
        self._bucket = capacity # full bucket
        self._prev_refill = monotonic()
        self._lock = Lock()

    def _refill(self) -> None:
        # how much time elapsed from the prev refill
        now = time.monotonic()
        elapsed = now - self._prev_refill
        tokens_available = elapsed * self._refill_rate
        # cap = 100, 100 = 0
        tokens_to_put = min(tokens_available, self._capacity - self._bucket)
        self._prev_refill = now

        if tokens_to_put == 0:
            return None
        self._bucket += tokens_to_put
        return None

    def try_acquire(self, tokens: int) -> bool:
        with self._lock:
            self._refill()

            if self._bucket >= tokens:
                self._bucket -= tokens
                return True

        return False
