import threading


class HitCounter:
    def __init__(self) -> None:
        self._times = [0] * 300
        self._hits = [0] * 300
        self._lock = threading.Lock()

    def hit(self, timestamp: int) -> None:
        slot = timestamp % 300
        with self._lock:
            if self._times[slot] == timestamp:
                self._hits[slot] += 1
            else:
                self._times[slot] = timestamp
                self._hits[slot] = 1
        return None

    def get_hits(self, timestamp: int) -> int:
        with self._lock:
            ans = 0
            for i in range(0, 300):
                slot = i % 300
                if timestamp - self._times[slot] < 300:
                    ans += self._hits[slot]

            return ans
