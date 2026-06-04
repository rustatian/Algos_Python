from urllib.parse import urlparse
import threading
from queue import Queue
from typing import Callable


def crawl(start_url: str, fetch: Callable[[str], list[str]]) -> set[str]:
    seen = set()
    seen.add(start_url)
    hn = urlparse(start_url).hostname
    lock = threading.Lock()

    q = Queue()

    def worker():
        while True:
            url = q.get()
            if url is None:
                q.task_done()
                return

            try:
                urls = fetch(url)

                for urll in urls:
                    if urlparse(urll).hostname != hn:
                        continue

                    with lock:
                        if urll in seen:
                            continue
                        seen.add(urll)
                    q.put(urll)
            except Exception:
                continue
            finally:
                q.task_done()

    threads = []
    for _ in range(10):
        t = threading.Thread(target=worker)
        t.start()
        threads.append(t)

    q.join()

    for _ in range(10):
        q.put(None)

    for t in threads:
        t.join()

    return seen
