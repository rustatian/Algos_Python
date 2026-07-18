"""Web Crawler — concurrency problem (LeetCode #1242).

Given a start URL and an HtmlParser, crawl every page reachable from the
start URL that shares its hostname — following the links each page
exposes, and visiting no page twice. A hostname is the slice between
"http://" and the next "/": "http://a.com/x" and "http://a.com/y" share
one; "http://a.com" and "http://b.com" do not.

Input:
    start_url : str — the page to start from, e.g. "http://a.com/home".
    parser    : HtmlParser — exposes get_urls(url) -> list[str], the URLs
                linked from a page (a stand-in for an HTTP fetch).
Output:
    list[str] — every URL reachable from start_url whose hostname equals
    start_url's hostname, each appearing once; order does not matter.

Example 1:
    Input:  start_url = "http://a.com/home"
            graph     = {"http://a.com/home": []}
    Output: ["http://a.com/home"]
    Explanation: the start page links nowhere — only it is returned.

Example 2:
    Input:  start_url = "http://a.com/home"
            graph     = {
                "http://a.com/home": ["http://a.com/page",
                                      "http://other.com/x"],
                "http://a.com/page": [],
                "http://other.com/x": [],
            }
    Output: ["http://a.com/home", "http://a.com/page"]
    Explanation: /page shares host a.com and is crawled; other.com is a
        foreign host, so it is never followed.

Example 3:  # LeetCode #1242
    Input:  start_url = "http://news.yahoo.com/news/topics/"
            graph     = {
                "http://news.yahoo.com/news/topics/":
                    ["http://news.yahoo.com",
                     "http://news.yahoo.com/news"],
                "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                "http://news.yahoo.com/news": [],
                "http://news.yahoo.com/us":   [],
                "http://news.google.com":
                    ["http://news.yahoo.com/news/topics/"],
            }
    Output: ["http://news.yahoo.com",
             "http://news.yahoo.com/news",
             "http://news.yahoo.com/news/topics/",
             "http://news.yahoo.com/us"]
    Explanation: the crawl reaches news.yahoo.com and /news directly,
        then /us transitively. news.google.com is a foreign host AND
        unreachable from the start — excluded twice over.

Constraints:
    - Every URL uses the http:// scheme.
    - The crawl follows links; it does not enumerate the host, so a
      same-host page nothing links to is never reached.

Six tiers escalate the concurrency model; all produce the same set.

Tier 1  — SimpleCrawler:     single-threaded frontier + visited set.
Tier 2  — ThreadPoolCrawler: page fetches fanned out over a thread pool.
Tier 2b — LevelCrawler:      lock-free variant, level-synchronous map().
Tier 3a — AsyncCrawler:      asyncio loop; blocking parser run on threads.
Tier 3b — PureAsyncCrawler:  asyncio loop; async-native parser, no threads.
Tier 4  — QueueCrawler:      fixed worker pool draining a queue.Queue.

See README.md for the full problem statement.
"""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from typing import Protocol
from urllib.parse import urlparse


class AsyncHtmlParser(Protocol):
    """The async-native page-fetching dependency for Tier 3b.

    Like HtmlParser, but get_urls is a coroutine — the crawler awaits it
    directly, with no thread offload. A real one would await an aiohttp
    request; the tests await an in-memory fake.

    Input:
        url : str — the page to fetch, e.g. "http://a.com/home".
    Output:
        list[str] — awaited from the get_urls coroutine: every URL
        directly linked from that page; a page with no links yields [].

    Example:
        await get_urls("http://a.com/home")
            -> ["http://a.com/page", "http://other.com/x"]
        # get_urls returns links to every host — the crawler, not the
        # parser, applies the same-host filter.
    """

    async def get_urls(self, url: str) -> list[str]: ...


class HtmlParser(Protocol):
    """The page-fetching dependency a crawler is handed.

    get_urls(url) stands in for "HTTP GET the url, parse the HTML, return
    every linked URL". A real implementation hits the network; the tests
    pass a fake backed by an in-memory graph. Any object with a matching
    get_urls method satisfies this type (structural typing).

    Input:
        url : str — the page to fetch, e.g. "http://a.com/home".
    Output:
        list[str] — every URL directly linked from that page (its
        out-edges in the link graph); a page with no links yields [].

    Example:
        get_urls("http://a.com/home")
            -> ["http://a.com/page", "http://other.com/x"]
        # get_urls returns links to every host — the crawler, not the
        # parser, applies the same-host filter.
    """

    def get_urls(self, url: str) -> list[str]: ...


class SimpleCrawler:
    """Tier 1: single-threaded crawl restricted to one hostname.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : HtmlParser — exposes get_urls(url) -> list[str], the
                    URLs linked from a page (a stand-in for an HTTP fetch).
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    Walk the link graph outward from start_url with an explicit frontier
    (a stack) and a visited set: stay on start_url's hostname, and visit
    each page exactly once. This is the baseline every later tier matches.

    Standard library:
        urllib.parse.urlparse — splits a URL into components; .hostname
            returns the host ("a.com"), already lower-cased and with any
            port stripped — exactly what the same-host filter compares.
        list as a stack — append() pushes, pop() takes the most recent,
            giving a depth-first frontier. set — O(1) membership for the
            visited check.

    Pseudocode:
        crawl(start_url, parser):
            stack = [start_url]          # the frontier — pages to fetch
            seen  = {start_url}          # every page ever discovered
            host  = hostname(start_url)
            while stack:
                url = stack.pop()
                for link in parser.get_urls(url):
                    if hostname(link) != host:   # off-host — skip
                        continue
                    if link in seen:             # already discovered — skip
                        continue
                    seen.add(link)               # mark on discovery,
                    stack.append(link)           #   then enqueue
            return list(seen)

    Complexity: O(V + E) over the reachable same-host subgraph — each page
    fetched once, each link inspected once. Space O(V) for seen + stack.

    Key invariant: a page joins seen the moment it is discovered, before
    it is ever fetched, so it can never be enqueued twice — that is what
    makes a cyclic graph terminate.
    """

    def crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        stack = [start_url]
        su = urlparse(start_url)
        seen = set()
        seen.add(start_url)

        while stack:
            ur = stack.pop()
            for link in parser.get_urls(ur):
                if urlparse(link).hostname != su.hostname:
                    continue

                if link in seen:
                    continue

                seen.add(link)
                stack.append(link)

        return list(seen)


class ThreadPoolCrawler:
    """Tier 2: concurrent crawl over a ThreadPoolExecutor.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : HtmlParser — exposes get_urls(url) -> list[str], the
                    URLs linked from a page (a stand-in for an HTTP fetch).
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    get_urls() is network I/O — the slow part — so page fetches are spread
    across a pool of worker threads. Each fetched page submits a fetch task
    for every new same-host link it finds: the pool feeds itself the work
    it discovers.

    Two pieces of state are shared by the workers and need a lock — the
    visited set, and an outstanding-task counter (pending) that tells the
    main thread when the crawl has drained.

    Standard library:
        concurrent.futures.ThreadPoolExecutor — a pool of reusable worker
            threads. submit(fn, arg) schedules fn(arg) on a free thread
            and returns a Future at once (used fire-and-forget here — the
            Future is ignored). shutdown() waits for the pool to drain.
        threading.Lock — a mutex; the `with lock:` block admits one
            thread at a time, making the test-and-add on seen atomic.
        threading.Event — a one-shot cross-thread flag. The main thread
            blocks in finished.wait(); the worker that drops pending to
            zero calls finished.set(), waking it.

    Pseudocode:
        crawl(start_url, parser):
            seen = {start_url};  pending = 0
            lock = Lock();       finished = Event()
            pool = ThreadPoolExecutor(max_workers)

            visit(url):                       # body of one pool task
                for link in parser.get_urls(url):
                    if hostname(link) != host:
                        continue
                    with lock:                # test-and-add must be atomic
                        if link not in seen:
                            seen.add(link)
                            pending += 1      # a new task is about to exist
                            pool.submit(visit, link)
                with lock:
                    pending -= 1              # this task is finishing
                    if pending == 0:
                        finished.set()

            with lock: pending += 1           # count the seed task
            pool.submit(visit, start_url)
            finished.wait()                   # main thread sleeps till drained
            pool.shutdown()
            return list(seen)

    Termination: pending counts tasks that exist but have not finished —
    raised before submit, lowered when a task ends; the crawl is done
    exactly when it returns to zero. A worker must never block on the
    result of a pool task: with the pool saturated, that deadlocks.

    Gotcha: raise pending before submit, never after — otherwise a fast
    child could finish and decrement before the parent counts it, hit zero
    early, and end the crawl mid-flight.
    """

    def __init__(self, max_workers: int = 16) -> None:
        self._max_workers = max_workers

    def crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        seen = set()
        seen.add(start_url)
        pending = 0
        su = urlparse(start_url)
        lock = threading.Lock()
        finished = threading.Event()
        pool = ThreadPoolExecutor(max_workers=self._max_workers)

        def visit(url: str):
            nonlocal pending
            for link in parser.get_urls(url):
                if su.hostname != urlparse(link).hostname:
                    continue
                with lock:
                    if link not in seen:
                        seen.add(link)
                        pending += 1
                        pool.submit(visit, link)

            with lock:
                pending -= 1
                if pending == 0:
                    finished.set()

        with lock:
            pending += 1

        pool.submit(visit, start_url)
        finished.wait()
        pool.shutdown()

        return list(seen)


class LevelCrawler:
    """Tier 2b: lock-free concurrent crawl, level-synchronous over a pool.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : HtmlParser — exposes get_urls(url) -> list[str], the
                    URLs linked from a page (a stand-in for an HTTP fetch).
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    A variant of Tier 2 that needs no lock. The crawl runs in waves: each
    wave hands the whole current frontier to ThreadPoolExecutor.map, which
    fetches every page in parallel, and the main thread folds the results
    into the next wave's frontier. Worker threads run only get_urls — a
    pure read — so the visited set, touched solely by the main thread, is
    thread-safe by *confinement* rather than by locking.

    Standard library:
        concurrent.futures.ThreadPoolExecutor — a pool of worker threads,
            used here through .map(fn, iterable): it runs fn on every item
            across the pool in parallel and yields the results back in
            input order, so one .map call fetches an entire wave. The
            `with ... as pool` form shuts the pool down at block exit.

    Pseudocode:
        crawl(start_url, parser):
            seen = {start_url};  host = hostname(start_url)
            with ThreadPoolExecutor(max_workers) as pool:
                frontier = [start_url]
                while frontier:                    # one wave per iteration
                    next_frontier = []
                    # map fetches the whole wave in parallel,
                    # yielding results back in frontier order
                    for links in pool.map(parser.get_urls, frontier):
                        for link in links:
                            if link in seen:
                                continue
                            if hostname(link) == host:
                                seen.add(link)
                                next_frontier.append(link)
                    frontier = next_frontier       # swap in the next wave
            return list(seen)

    Complexity: O(V + E) work; wall-clock is the sum over waves of each
    wave's slowest fetch. Termination is simply an empty wave — no task
    counter, no event.

    Trade-off vs ThreadPoolCrawler: simpler (no lock, no counter) but
    level-synchronous — the pool idles at every wave boundary, waiting on
    that wave's slowest fetch. Only the main thread touches seen, so there
    is no race for a lock to guard.
    """

    def __init__(self, max_workers: int = 16) -> None:
        self._max_workers = max_workers

    def crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        seen = set()
        hn = urlparse(start_url).hostname
        seen.add(start_url)

        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            stack = [start_url]
            while stack:
                level = []
                for links in pool.map(parser.get_urls, stack):
                    for link in links:
                        if link in seen:
                            continue
                        if urlparse(link).hostname == hn:
                            seen.add(link)
                            level.append(link)
                stack = level
        return list(seen)


class AsyncCrawler:
    """Tier 3a: asyncio crawl driving a *blocking* parser.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : HtmlParser — exposes get_urls(url) -> list[str], the
                    URLs linked from a page (a stand-in for an HTTP fetch).
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    The parser here is the ordinary synchronous HtmlParser — its get_urls
    blocks the calling thread for the whole fetch. An event loop is a
    single thread, so calling get_urls straight from a coroutine would
    freeze the loop: every other visit() would stall behind that one
    fetch, and the "concurrency" would be a fiction.

    The fix is asyncio.to_thread: it runs the blocking call on a worker
    thread and hands back an awaitable. `await asyncio.to_thread(...)`
    yields the loop while the thread fetches, so other coroutines run.
    Tier 3a is thus a hybrid — asyncio schedules, a thread pool fetches.

    Standard library:
        asyncio.run(coro) — the sync-to-async bridge: starts an event
            loop, runs the coroutine to completion, returns its result,
            then closes the loop. It keeps crawl() an ordinary sync method.
        asyncio.TaskGroup — an async context manager that owns child
            tasks; create_task() schedules a coroutine, and the `async
            with` block does not exit until every task it owns (and every
            task they spawn) has finished — built-in termination detection.
            (Python 3.11+.)
        asyncio.to_thread(fn, *args) — runs the blocking fn(*args) on a
            background thread and returns an awaitable; awaiting it frees
            the event loop while the thread works.

    Pseudocode:
        crawl(start_url, parser):                  # plain sync method
            return asyncio.run(_crawl(start_url, parser))

        async _crawl(start_url, parser):
            seen = {start_url};  host = hostname(start_url)
            async with asyncio.TaskGroup() as tg:
                async visit(url):
                    # offload the blocking fetch onto a worker thread
                    links = await asyncio.to_thread(parser.get_urls, url)
                    for link in links:
                        if hostname(link) != host:
                            continue
                        if link in seen:
                            continue
                        seen.add(link)
                        tg.create_task(visit(link))   # fire-and-forget child
                tg.create_task(visit(start_url))
            return list(seen)             # block exits once every task drains

    seen needs no lock: it is touched only inside visit(), which runs only
    on the loop thread, and the check-and-add holds no await, so it cannot
    be interleaved. The worker threads run only get_urls, a pure read.
    Termination: the TaskGroup block exits once every task — and every task
    those tasks spawned — has finished.
    """

    async def _crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        hn = urlparse(start_url).hostname
        seen = set()
        seen.add(start_url)

        async with asyncio.TaskGroup() as tg:

            async def visit(url: str):
                links = await asyncio.to_thread(parser.get_urls, url)
                for link in links:
                    if hn != urlparse(link).hostname:
                        continue
                    if link in seen:
                        continue
                    seen.add(link)
                    tg.create_task(visit(link))

            tg.create_task(visit(start_url))

        return list(seen)

    def crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        return asyncio.run(self._crawl(start_url, parser))


class PureAsyncCrawler:
    """Tier 3b: asyncio crawl driving an async-native parser.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : AsyncHtmlParser — exposes the coroutine get_urls(url),
                    awaited for the URLs linked from a page.
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    When the parser is async-native — its get_urls is a coroutine that
    awaits the network itself — there is no blocking call to hide.
    `await parser.get_urls(url)` yields the loop directly, so Tier 3a's
    thread offload is unnecessary: no worker threads, no thread pool.

    This is the lighter, further-scaling design — a coroutine costs far
    less than a thread — but it demands an async-native stack the whole
    way down; one blocking call anywhere reintroduces Tier 3a's problem.

    Standard library:
        asyncio.run(coro) — starts an event loop, runs the coroutine to
            completion, closes the loop; keeps crawl() a sync method.
        asyncio.TaskGroup — async context manager owning child tasks;
            create_task() schedules one, and the `async with` block exits
            only once every owned task (transitively) has finished — the
            termination signal. (Python 3.11+.) No asyncio.to_thread is
            needed: the parser is already a coroutine, awaited directly.

    Pseudocode:
        crawl(start_url, parser):                  # plain sync method
            return asyncio.run(_crawl(start_url, parser))

        async _crawl(start_url, parser):
            seen = {start_url};  host = hostname(start_url)
            async with asyncio.TaskGroup() as tg:
                async visit(url):
                    # await the coroutine directly — no thread offload
                    links = await parser.get_urls(url)
                    for link in links:
                        if hostname(link) != host:
                            continue
                        if link in seen:
                            continue
                        seen.add(link)
                        tg.create_task(visit(link))
                tg.create_task(visit(start_url))
            return list(seen)

    The one line that differs from Tier 3a is the fetch — here
    `await parser.get_urls(url)`, there `await asyncio.to_thread(...)`.
    seen needs no lock for the same reason: one loop thread, and no await
    between its check and its add. Termination is asyncio.TaskGroup's.
    """

    async def _crawl(self, start_url: str, parser: AsyncHtmlParser) -> list[str]:
        hn = urlparse(start_url).hostname
        seen = set()
        seen.add(start_url)

        async with asyncio.TaskGroup() as tg:

            async def visit(url: str):
                links = await parser.get_urls(url)
                for link in links:
                    if hn != urlparse(link).hostname:
                        continue
                    if link in seen:
                        continue
                    seen.add(link)
                    tg.create_task(visit(link))

            tg.create_task(visit(start_url))

        return list(seen)

    def crawl(self, start_url: str, parser: AsyncHtmlParser) -> list[str]:
        return asyncio.run(self._crawl(start_url, parser))


class QueueCrawler:
    """Tier 4: concurrent crawl over a fixed thread pool and a queue.Queue.

    Input:
        start_url : str — the page to start from, e.g. "http://a.com/home".
        parser    : HtmlParser — exposes get_urls(url) -> list[str], the
                    URLs linked from a page (a stand-in for an HTTP fetch).
    Output:
        list[str] — every URL reachable from start_url whose hostname
        equals start_url's hostname, each appearing once; order irrelevant.

    Example 1:
        Input:  start_url = "http://a.com/home"
                graph     = {"http://a.com/home": []}
        Output: ["http://a.com/home"]
        Explanation: the start page links nowhere — only it is returned.

    Example 2:
        Input:  start_url = "http://a.com/home"
                graph     = {
                    "http://a.com/home": ["http://a.com/page",
                                          "http://other.com/x"],
                    "http://a.com/page": [],
                    "http://other.com/x": [],
                }
        Output: ["http://a.com/home", "http://a.com/page"]
        Explanation: /page shares host a.com and is crawled; other.com is
            a foreign host, so it is never followed.

    Example 3:  # LeetCode #1242
        Input:  start_url = "http://news.yahoo.com/news/topics/"
                graph     = {
                    "http://news.yahoo.com/news/topics/":
                        ["http://news.yahoo.com",
                         "http://news.yahoo.com/news"],
                    "http://news.yahoo.com":      ["http://news.yahoo.com/us"],
                    "http://news.yahoo.com/news": [],
                    "http://news.yahoo.com/us":   [],
                    "http://news.google.com":
                        ["http://news.yahoo.com/news/topics/"],
                }
        Output: ["http://news.yahoo.com",
                 "http://news.yahoo.com/news",
                 "http://news.yahoo.com/news/topics/",
                 "http://news.yahoo.com/us"]
        Explanation: the crawl reaches news.yahoo.com and /news directly,
            then /us transitively. news.google.com is a foreign host AND
            unreachable from the start — excluded twice over.

    A fixed set of worker threads each loop forever: take a URL off a
    shared queue, fetch it, and put every new same-host link back on the
    queue. The queue is the work channel — and, through its built-in
    unfinished-task counter, also the termination detector.

    Contrast with Tier 2: ThreadPoolCrawler submitted tasks dynamically
    and hand-rolled a `pending` counter plus an Event to detect the end.
    Here the threads are fixed and the queue carries the work; the pair
    queue.join() / queue.task_done() *is* the termination machinery, so
    there is no counter of your own to get wrong.

    Standard library:
        queue.Queue — a thread-safe FIFO queue; put() and get() are
            atomic, so workers share it with no lock. It also keeps an
            internal count of unfinished tasks: put() raises it,
            task_done() lowers it, and join() blocks until it hits zero —
            the termination signal, built in.
        threading.Thread — an OS thread; target= is the function it runs,
            start() launches it, join() waits for it to end. The pool is a
            fixed list of these, every one running worker().
        threading.Lock — a mutex guarding the visited set; the queue is
            thread-safe on its own, but seen is separate shared state.

    Pseudocode:
        crawl(start_url, parser):
            seen = {start_url};  host = hostname(start_url)
            q    = Queue();      lock = Lock()

            q.put(start_url)                       # seed the frontier
            start `max_workers` threads running worker()

            q.join()          # blocks until every put is matched by task_done

            for _ in range(max_workers):           # shut the workers down
                q.put(None)                        #   one sentinel each
            join every worker thread
            return list(seen)

        worker():                  # every pool thread runs this loop
            while True:
                url = q.get()                      # blocks until work arrives
                if url is None:                    # sentinel — leave the loop
                    q.task_done()
                    break
                for link in parser.get_urls(url):
                    if hostname(link) != host:
                        continue
                    with lock:                     # test-and-add stays atomic
                        if link not in seen:
                            seen.add(link)
                            q.put(link)            # discovered work: count +1
                q.task_done()                      # this url done:    count -1

    Termination: a queue.Queue keeps an internal count of unfinished
    tasks — put() raises it, task_done() lowers it, join() blocks until it
    is zero. Each worker enqueues every child *before* the task_done() for
    the current URL, so the count cannot touch zero while work remains.

    Still needs a lock: the queue is thread-safe and guards the frontier,
    but the visited set is separate shared state — without the lock two
    workers can both pass `link not in seen` and enqueue the same page,
    the identical test-and-add race as Tier 2.
    """

    def __init__(self, max_workers: int = 16) -> None:
        self._max_workers = max_workers

    def crawl(self, start_url: str, parser: HtmlParser) -> list[str]:
        seen = set()
        seen.add(start_url)
        q = Queue()
        q.put(start_url)
        lock = threading.Lock()

        h = urlparse(start_url).hostname

        def worker():
            while True:
                url = q.get()
                if url is None:
                    q.task_done()
                    break
                for link in parser.get_urls(url):
                    if urlparse(link).hostname != h:
                        continue
                    with lock:
                        if link in seen:
                            continue
                        seen.add(link)
                    q.put(link)
                q.task_done()

        threads: list[threading.Thread] = []
        for _ in range(self._max_workers):
            t = threading.Thread(target=worker)
            t.start()
            threads.append(t)

        q.join()

        for _ in range(self._max_workers):
            q.put(None)

        for t in threads:
            t.join()

        return list(seen)
