import time
from threading import Lock


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float) -> None:
        """
        capacity: max tokens the bucket can hold.
        refill_rate: tokens added per second.
        """
        self._cap = capacity
        self._refill_rate = refill_rate
        self._bucket = capacity
        self._lock = Lock()
        self._last_refill = time.monotonic()

    def try_acquire(self, tokens: int) -> bool:
        """
        Try to consume `tokens` from the bucket.
        Returns True on success, False if insufficient tokens.
        Non-blocking.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            tokens_all = elapsed * self._refill_rate
            new_tokens = min(tokens_all, self._cap - self._bucket)
            self._last_refill = now
            self._bucket += new_tokens
            if self._bucket >= tokens:
                self._bucket -= tokens
                return True

        return False


# Example:
# bucket = TokenBucket(capacity=1000, refill_rate=100)
# bucket.try_acquire(50)    # True
# bucket.try_acquire(2000)  # False (exceeds capacity)
