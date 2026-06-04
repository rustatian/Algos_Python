from threading import Lock


class RWLock:
    def __init__(self):
        self._resource = Lock()
        self._writers_count_lock = Lock()
        self._readers_count_lock = Lock()
        self._reader_gate = Lock()
        self._readers_num = 0
        self._writers_num = 0

    def acquire_read(self) -> None:
        self._reader_gate.acquire()
        try:
            self._readers_count_lock.acquire()
            try:
                self._readers_num += 1
                if self._readers_num == 1:
                    try:
                        self._resource.acquire()
                    except BaseException:
                        self._readers_num -= 1
                        raise
            finally:
                self._readers_count_lock.release()
        finally:
            self._reader_gate.release()
        return None

    def release_read(self) -> None:
        self._readers_count_lock.acquire()
        self._readers_num -= 1
        if self._readers_num == 0:
            self._resource.release()
        self._readers_count_lock.release()
        return None

    def acquire_write(self) -> None:
        self._writers_count_lock.acquire()
        self._writers_num += 1
        if self._writers_num == 1:
            self._reader_gate.acquire()
        self._writers_count_lock.release()
        self._resource.acquire()
        return None

    def release_write(self) -> None:
        self._writers_count_lock.acquire()
        self._writers_num -= 1
        if self._writers_num == 0:
            self._reader_gate.release()
        self._writers_count_lock.release()
        self._resource.release()
        return None
