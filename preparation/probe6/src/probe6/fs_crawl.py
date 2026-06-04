# PROVIDED — you do not implement these:
import os.path
import threading
import uuid
from collections import deque


def list_dir(path: str) -> list[str]:
    """Returns the immediate child entry names (files and subdirs) under `path`.
    Raises if `path` is not a directory."""
    return []


def is_dir(path: str) -> bool:
    """True if `path` is a directory, False if it's a file."""
    return True


def crawl(root: str) -> list[str]:
    ans = []

    start: deque[str] = deque()
    start.append(root)

    while start:
        # popleft to have a constant pop
        node = start.popleft()

        if is_dir(node):
            try:
                files = list_dir(node)
            except OSError:
                # probably permission error
                # skip that file
                continue

            for entry in files:
                fpath = os.path.join(node, entry)
                if is_dir(fpath):
                    # append dir
                    start.append(fpath)
                else:
                    # append file
                    ans.append(fpath)
        else:
            ans.append(node)

    return ans


class CrawlManager:
    def __init__(self):
        self._jobs: dict[str, dict] = {}  # job_id -> {status, results, error}
        self._lock = threading.Lock()

    def submit_crawl(self, root: str) -> str:
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "RUNNING", "results": [], "error": None}
        # kick off the crawl on a background thread so this returns immediately
        threading.Thread(target=self._run, args=(job_id, root), daemon=True).start()
        return job_id

    def _run(self, job_id, root):
        try:
            files = crawl(root)  # the function you wrote
            self._jobs[job_id]["results"] = files
            self._jobs[job_id]["status"] = "DONE"
        except Exception as e:
            self._jobs[job_id]["status"] = "FAILED"
            self._jobs[job_id]["error"] = str(e)

    def get_status(self, job_id):
        return self._jobs[job_id]  # <-- the hazard lives here
