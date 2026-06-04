import threading
import collections
from collections import deque
from enum import Enum
import time


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, max_reqs: int):
        self._state: State = State.CLOSED
        self._failure_threshold = 10
        self._probe_success = 0
        self._success_threshold = 10
        self._probe_quota = 1  # half-open quota
        self._reqs: deque[bool] = collections.deque(maxlen=max_reqs)
        self._lock = threading.Lock()
        self._opened_at = 0
        self._cooldown = 10
        self._probes_in_flight = 0

    def _on_success(self):
        self._reqs.append(True)
        if self._state == State.HALF_OPEN:
            self._probe_success += 1
            if self._probe_success >= self._success_threshold:
                self._state = State.CLOSED
                self._reqs.clear()
                self._probe_success = 0

        return None

    def _on_failure(self):
        self._reqs.append(False)
        failures = self._reqs.count(False)

        if self._state == State.CLOSED:
            if failures >= self._failure_threshold:
                self._state = State.OPEN
                self._opened_at = time.monotonic()
        elif self._state == State.HALF_OPEN:
            # on any failure -> OPEN
            self._state = State.OPEN
            self._opened_at = time.monotonic()

        return None

    def call(self, fn, *args):
        with self._lock:
            if self._state == State.OPEN:
                if time.monotonic() - self._opened_at >= self._cooldown:
                    self._state = State.HALF_OPEN
                    self._probe_success = 0
                    self._probes_in_flight = 0
                else:
                    raise RuntimeError("failure")

            if self._state == State.HALF_OPEN:
                if self._probes_in_flight >= self._probe_quota:
                    raise RuntimeError("quota exhausted")
                self._probes_in_flight += 1

        try:
            result = fn(*args)
        except Exception:
            with self._lock:
                self._on_failure()
                self._probes_in_flight = max(0, self._probes_in_flight - 1)
            raise
        else:
            with self._lock:
                self._on_success()
                self._probes_in_flight = max(0, self._probes_in_flight - 1)
            return result
