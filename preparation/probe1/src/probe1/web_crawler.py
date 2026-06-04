import datetime
import threading
from curses import nonl
from queue import Queue
from turtle import pen
from concurrent.futures import ThreadPoolExecutor
from threading import Thread, Lock, Event
from urllib.parse import urlparse
class HtmlClient:
    def fetch(self, url: str) -> str:
        return "html"


def extract_urls(html: str, base_url: str) -> list[str]:
    return []

def crawl(start_url: str, client: HtmlClient) -> set[str]:
    """Returns the set of URLs reachable from seed_url on the same domain."""
    seen = set()
    seen.add(start_url)
    q = Queue()
    q.put(start_url)
    lock = Lock()

    h = urlparse(start_url).hostname

    def worker():
        while True:
            url = q.get()
            if url is None:
                q.task_done()
                break
            try:
                fhtml = client.fetch(url)
            except Exception:
                q.task_done()
                continue

            eurls = extract_urls(fhtml, url)

            for link in eurls:
                if urlparse(link).hostname != h:
                    continue
                with lock:
                    if link in seen:
                        continue
                    seen.add(link)
                q.put(link)
            q.task_done()

    threads: list[threading.Thread] = []
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
