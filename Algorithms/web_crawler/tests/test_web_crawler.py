"""Tests for the Web Crawler tiers.

Every tier crawls the same link graph and must return the same set of URLs,
so the correctness tests are parametrized over the crawler classes. The
fake HtmlParser is backed by an in-memory dict graph — no network — which
keeps every test deterministic.

Tiers 1, 2, 2b, 3a, and 4 share the synchronous-parser contract and the
CRAWLERS list below. Only Tier 3b (PureAsyncCrawler) takes an async-native
parser, so it gets its own test block.
"""

import asyncio
import collections
import threading
import time

import pytest

from web_crawler.web_crawler import (
    AsyncCrawler,
    LevelCrawler,
    PureAsyncCrawler,
    QueueCrawler,
    SimpleCrawler,
    ThreadPoolCrawler,
)

# Crawlers that take a synchronous HtmlParser (Tiers 1, 2, 2b, 3a, 4).
CRAWLERS = [SimpleCrawler, ThreadPoolCrawler, LevelCrawler, AsyncCrawler, QueueCrawler]


class FakeHtmlParser:
    """An HtmlParser backed by an in-memory {url: [linked urls]} graph.

    Stands in for "fetch the page, parse its HTML, list its links" without
    touching the network. A URL absent from the graph is treated as a page
    with no outgoing links. get_urls returns a fresh list each call, so a
    crawler cannot mutate the graph through it.
    """

    def __init__(self, graph: dict[str, list[str]]) -> None:
        self._graph = graph

    def get_urls(self, url: str) -> list[str]:
        return list(self._graph.get(url, []))


class RecordingHtmlParser:
    """An HtmlParser that records how it was called — for the thread-based
    concurrency tests (Tiers 2, 2b, 3a).

    On top of serving the graph it tracks, thread-safely:
      - call_count[url] — how many times get_urls(url) ran;
      - max_in_flight   — the most get_urls calls active at once.

    An optional per-call delay widens the window in which a missing lock
    would let two workers schedule the same URL, so a race surfaces. Its
    own lock guards only these counters — it is measurement scaffolding,
    not the crawler's visited-set lock (the thing under test).
    """

    def __init__(self, graph: dict[str, list[str]], delay: float = 0.0) -> None:
        self._graph = graph
        self._delay = delay
        self._lock = threading.Lock()
        self.call_count: collections.Counter[str] = collections.Counter()
        self._in_flight = 0
        self.max_in_flight = 0

    def get_urls(self, url: str) -> list[str]:
        with self._lock:
            self.call_count[url] += 1
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            if self._delay:
                time.sleep(self._delay)
            return list(self._graph.get(url, []))
        finally:
            with self._lock:
                self._in_flight -= 1


class AsyncRecordingHtmlParser:
    """An async-native HtmlParser — Tier 3b's parser. get_urls is a coroutine
    that awaits a small sleep (mimicking a network round-trip, and giving
    the event loop a real yield point) before returning the page's links.

    Tracks call_count and max_in_flight, like RecordingHtmlParser — but with
    no lock: asyncio is single-threaded, so the counter updates (which hold
    no await) are atomic on their own.
    """

    def __init__(self, graph: dict[str, list[str]], delay: float = 0.0) -> None:
        self._graph = graph
        self._delay = delay
        self.call_count: collections.Counter[str] = collections.Counter()
        self._in_flight = 0
        self.max_in_flight = 0

    async def get_urls(self, url: str) -> list[str]:
        self.call_count[url] += 1
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            await asyncio.sleep(self._delay)
            return list(self._graph.get(url, []))
        finally:
            self._in_flight -= 1


# ---------------------------------------------------------------------------
# Shared correctness — every crawler tier must satisfy these.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_start_url_with_no_links(crawler_cls: type) -> None:
    """A start page that links nowhere → only the start URL is returned."""
    parser = FakeHtmlParser({"http://a.com/home": []})
    got = crawler_cls().crawl("http://a.com/home", parser)
    assert sorted(got) == ["http://a.com/home"]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_follows_links_on_same_host(crawler_cls: type) -> None:
    """Every same-host page reachable from the start is crawled."""
    parser = FakeHtmlParser(
        {
            "http://a.com/1": ["http://a.com/2", "http://a.com/3"],
            "http://a.com/2": [],
            "http://a.com/3": [],
        }
    )
    got = crawler_cls().crawl("http://a.com/1", parser)
    assert sorted(got) == [
        "http://a.com/1",
        "http://a.com/2",
        "http://a.com/3",
    ]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_skips_other_hostnames(crawler_cls: type) -> None:
    """Links to a different hostname are never crawled — and a page reachable
    only *through* a foreign host stays unreached.
    """
    parser = FakeHtmlParser(
        {
            "http://a.com/home": ["http://a.com/page", "http://other.com/x"],
            "http://a.com/page": ["http://other.com/y"],
            "http://other.com/x": ["http://a.com/buried"],
            "http://other.com/y": [],
        }
    )
    got = crawler_cls().crawl("http://a.com/home", parser)
    assert sorted(got) == ["http://a.com/home", "http://a.com/page"]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_transitive_chain(crawler_cls: type) -> None:
    """A → B → C → D: a page reachable only transitively is still crawled."""
    parser = FakeHtmlParser(
        {
            "http://a.com/a": ["http://a.com/b"],
            "http://a.com/b": ["http://a.com/c"],
            "http://a.com/c": ["http://a.com/d"],
            "http://a.com/d": [],
        }
    )
    got = crawler_cls().crawl("http://a.com/a", parser)
    assert sorted(got) == [
        "http://a.com/a",
        "http://a.com/b",
        "http://a.com/c",
        "http://a.com/d",
    ]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_cycle_terminates_without_revisiting(crawler_cls: type) -> None:
    """A → B → C → A: the visited set must break the cycle and terminate."""
    parser = FakeHtmlParser(
        {
            "http://a.com/a": ["http://a.com/b"],
            "http://a.com/b": ["http://a.com/c"],
            "http://a.com/c": ["http://a.com/a"],
        }
    )
    got = crawler_cls().crawl("http://a.com/a", parser)
    assert sorted(got) == [
        "http://a.com/a",
        "http://a.com/b",
        "http://a.com/c",
    ]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_self_link_does_not_loop(crawler_cls: type) -> None:
    """A page linking to itself must not cause an infinite loop."""
    parser = FakeHtmlParser(
        {
            "http://a.com/home": ["http://a.com/home", "http://a.com/next"],
            "http://a.com/next": [],
        }
    )
    got = crawler_cls().crawl("http://a.com/home", parser)
    assert sorted(got) == ["http://a.com/home", "http://a.com/next"]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_diamond_yields_each_page_once(crawler_cls: type) -> None:
    """A→B, A→C, B→D, C→D: D is reachable two ways but returned exactly once."""
    parser = FakeHtmlParser(
        {
            "http://a.com/a": ["http://a.com/b", "http://a.com/c"],
            "http://a.com/b": ["http://a.com/d"],
            "http://a.com/c": ["http://a.com/d"],
            "http://a.com/d": [],
        }
    )
    got = crawler_cls().crawl("http://a.com/a", parser)
    assert sorted(got) == [
        "http://a.com/a",
        "http://a.com/b",
        "http://a.com/c",
        "http://a.com/d",
    ]
    assert len(got) == len(set(got)), "a page was returned more than once"


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_unreachable_same_host_page_is_not_crawled(crawler_cls: type) -> None:
    """The crawl follows links — it does not enumerate the host. A same-host
    page that nothing reachable links to stays out of the result.
    """
    parser = FakeHtmlParser(
        {
            "http://a.com/home": ["http://a.com/about"],
            "http://a.com/about": [],
            "http://a.com/orphan": [],  # same host, but nothing links here
        }
    )
    got = crawler_cls().crawl("http://a.com/home", parser)
    assert sorted(got) == ["http://a.com/about", "http://a.com/home"]


@pytest.mark.parametrize("crawler_cls", CRAWLERS)
def test_leetcode_1242_example(crawler_cls: type) -> None:
    """LeetCode #1242 example 1: the crawl starts mid-graph; news.google.com
    is a different host *and* unreachable from the start — excluded twice over.
    """
    parser = FakeHtmlParser(
        {
            "http://news.yahoo.com/news/topics/": [
                "http://news.yahoo.com",
                "http://news.yahoo.com/news",
            ],
            "http://news.yahoo.com": ["http://news.yahoo.com/us"],
            "http://news.yahoo.com/news": [],
            "http://news.google.com": [
                "http://news.yahoo.com/news/topics/",
                "http://news.yahoo.com/news",
            ],
            "http://news.yahoo.com/us": [],
        }
    )
    got = crawler_cls().crawl("http://news.yahoo.com/news/topics/", parser)
    assert sorted(got) == [
        "http://news.yahoo.com",
        "http://news.yahoo.com/news",
        "http://news.yahoo.com/news/topics/",
        "http://news.yahoo.com/us",
    ]


# ---------------------------------------------------------------------------
# Tier 2 only: ThreadPoolCrawler concurrency.
#
# These pass deterministically for a correctly-locked crawler. For one with
# a missing or wrong lock they are probabilistic — RecordingHtmlParser's
# delay widens the test-then-add race and the wide graphs give many chances
# to hit it, so a bug is very likely (not certain) to surface on a run.
#
# A crawl whose termination counter is wrong will *hang* here rather than
# fail an assertion — if a run seems stuck, that is the bug.
# ---------------------------------------------------------------------------


def test_threadpool_fetches_each_page_exactly_once() -> None:
    """No double-crawl: with a proper lock, get_urls runs exactly once per
    reachable same-host page. A missing lock lets two workers both pass the
    `link not in seen` check and schedule the same URL, so get_urls runs for
    it more than once. (seen is a set, so the result still dedups — the
    wasted re-fetch is the real symptom, which is why we count calls.)
    """
    # A root fanning out to 40 children that all link to one shared page,
    # so 40 workers race to claim "shared" at almost the same instant.
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/p{i}" for i in range(40)],
        "http://a.com/shared": [],
    }
    for i in range(40):
        graph[f"http://a.com/p{i}"] = ["http://a.com/shared"]

    parser = RecordingHtmlParser(graph, delay=0.001)
    got = ThreadPoolCrawler(max_workers=16).crawl("http://a.com/root", parser)

    expected = {"http://a.com/root", "http://a.com/shared"}
    expected |= {f"http://a.com/p{i}" for i in range(40)}
    assert set(got) == expected
    for url in expected:
        assert parser.call_count[url] == 1, (
            f"{url} fetched {parser.call_count[url]}x — a worker lost the "
            f"test-then-add race on the visited set"
        )


def test_threadpool_runs_fetches_concurrently() -> None:
    """The pool must really parallelize. A root linking to many children
    means those children can be fetched at the same time; with a per-fetch
    delay a concurrent crawler overlaps them, while a secretly-sequential
    one never shows more than one fetch in flight.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/c{i}" for i in range(8)],
    }
    for i in range(8):
        graph[f"http://a.com/c{i}"] = []

    parser = RecordingHtmlParser(graph, delay=0.02)
    ThreadPoolCrawler(max_workers=8).crawl("http://a.com/root", parser)

    assert parser.max_in_flight >= 2, (
        "fetches never overlapped — the crawler is not using the pool"
    )


def test_threadpool_crawls_a_large_cross_linked_graph() -> None:
    """A 200-page chain where every page also links back to the root, so the
    pool churns through many tasks with heavy re-discovery. Every reachable
    page must come back exactly once — a corrupted termination counter would
    drop pages (stops early) or hang (never reaches zero).
    """
    n = 200
    graph: dict[str, list[str]] = {}
    for i in range(n):
        links = ["http://a.com/0"]
        if i + 1 < n:
            links.append(f"http://a.com/{i + 1}")
        graph[f"http://a.com/{i}"] = links

    parser = RecordingHtmlParser(graph)
    got = ThreadPoolCrawler(max_workers=16).crawl("http://a.com/0", parser)

    assert sorted(got) == sorted(f"http://a.com/{i}" for i in range(n))
    for i in range(n):
        assert parser.call_count[f"http://a.com/{i}"] == 1


# ---------------------------------------------------------------------------
# Tier 2b only: LevelCrawler.
#
# LevelCrawler has no lock, so there is no test-then-add race to surface.
# What still needs checking: that it genuinely parallelizes a wave (a
# secretly-sequential implementation would pass the shared correctness
# tests too), and that it fetches each page exactly once.
# ---------------------------------------------------------------------------


def test_level_crawler_fetches_a_wave_concurrently() -> None:
    """LevelCrawler must fetch a whole wave in parallel via executor.map.
    A root linking to many children makes the second wave a batch of
    independent fetches; with a per-fetch delay they overlap. A
    secretly-sequential crawl would show max_in_flight == 1.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/c{i}" for i in range(8)],
    }
    for i in range(8):
        graph[f"http://a.com/c{i}"] = []

    parser = RecordingHtmlParser(graph, delay=0.02)
    got = LevelCrawler(max_workers=8).crawl("http://a.com/root", parser)

    assert parser.max_in_flight >= 2, (
        "a wave's fetches never overlapped — executor.map is not parallelizing"
    )
    expected = {"http://a.com/root"} | {f"http://a.com/c{i}" for i in range(8)}
    assert set(got) == expected
    for url in expected:
        assert parser.call_count[url] == 1


# ---------------------------------------------------------------------------
# Tier 3a only: AsyncCrawler.
#
# AsyncCrawler takes the ordinary synchronous HtmlParser, so its correctness
# is already covered by the shared CRAWLERS tests above. What is left to
# check is the offload itself: get_urls blocks, and AsyncCrawler must push it
# onto a worker thread (asyncio.to_thread) so fetches truly overlap. A crawl
# that ran the blocking get_urls straight from a coroutine would freeze the
# loop and never show more than one fetch in flight.
# ---------------------------------------------------------------------------


def test_async_crawler_offloads_blocking_fetches_concurrently() -> None:
    """AsyncCrawler drives a *blocking* parser. To parallelize it must hand
    each get_urls to a worker thread via asyncio.to_thread; a root linking
    to many children then has those fetches overlap. Calling the blocking
    get_urls straight from a coroutine would freeze the loop, so
    max_in_flight would never exceed 1. Also confirms each page is fetched
    exactly once.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/c{i}" for i in range(8)],
    }
    for i in range(8):
        graph[f"http://a.com/c{i}"] = []

    parser = RecordingHtmlParser(graph, delay=0.02)
    got = AsyncCrawler().crawl("http://a.com/root", parser)

    assert parser.max_in_flight >= 2, (
        "fetches never overlapped — the blocking get_urls is being run "
        "straight from the coroutine instead of via asyncio.to_thread"
    )
    expected = {"http://a.com/root"} | {f"http://a.com/c{i}" for i in range(8)}
    assert set(got) == expected
    for url in expected:
        assert parser.call_count[url] == 1


# ---------------------------------------------------------------------------
# Tier 3b only: PureAsyncCrawler.
#
# PureAsyncCrawler.crawl is synchronous to *call* — it runs asyncio.run()
# inside — so these are ordinary (non-async) test functions. It takes an
# async-native parser (AsyncRecordingHtmlParser), whose get_urls is a
# coroutine awaited directly: no threads, no offload. There is no lock to
# test for — asyncio is single-threaded. What to check is that it crawls
# correctly and that the coroutines genuinely interleave at the await.
# ---------------------------------------------------------------------------


def test_pure_async_crawler_follows_a_transitive_chain() -> None:
    """A → B → C → D: pages reachable only transitively are still crawled."""
    parser = AsyncRecordingHtmlParser(
        {
            "http://a.com/a": ["http://a.com/b"],
            "http://a.com/b": ["http://a.com/c"],
            "http://a.com/c": ["http://a.com/d"],
            "http://a.com/d": [],
        }
    )
    got = PureAsyncCrawler().crawl("http://a.com/a", parser)
    assert sorted(got) == [
        "http://a.com/a",
        "http://a.com/b",
        "http://a.com/c",
        "http://a.com/d",
    ]


def test_pure_async_crawler_skips_other_hostnames() -> None:
    """Links to a different hostname are never crawled."""
    parser = AsyncRecordingHtmlParser(
        {
            "http://a.com/home": ["http://a.com/page", "http://other.com/x"],
            "http://a.com/page": [],
            "http://other.com/x": [],
        }
    )
    got = PureAsyncCrawler().crawl("http://a.com/home", parser)
    assert sorted(got) == ["http://a.com/home", "http://a.com/page"]


def test_pure_async_crawler_terminates_on_a_cycle() -> None:
    """A → B → C → A: the visited set breaks the cycle; the TaskGroup still
    exits, since every spawned task finishes.
    """
    parser = AsyncRecordingHtmlParser(
        {
            "http://a.com/a": ["http://a.com/b"],
            "http://a.com/b": ["http://a.com/c"],
            "http://a.com/c": ["http://a.com/a"],
        }
    )
    got = PureAsyncCrawler().crawl("http://a.com/a", parser)
    assert sorted(got) == ["http://a.com/a", "http://a.com/b", "http://a.com/c"]


def test_pure_async_crawler_leetcode_1242_example() -> None:
    """LeetCode #1242 example 1 — multi-level descent plus a foreign host."""
    parser = AsyncRecordingHtmlParser(
        {
            "http://news.yahoo.com/news/topics/": [
                "http://news.yahoo.com",
                "http://news.yahoo.com/news",
            ],
            "http://news.yahoo.com": ["http://news.yahoo.com/us"],
            "http://news.yahoo.com/news": [],
            "http://news.google.com": ["http://news.yahoo.com/news/topics/"],
            "http://news.yahoo.com/us": [],
        }
    )
    got = PureAsyncCrawler().crawl("http://news.yahoo.com/news/topics/", parser)
    assert sorted(got) == [
        "http://news.yahoo.com",
        "http://news.yahoo.com/news",
        "http://news.yahoo.com/news/topics/",
        "http://news.yahoo.com/us",
    ]


def test_pure_async_crawler_fetches_concurrently() -> None:
    """The coroutines must genuinely interleave: a root linking to many
    children means those fetches can all be in flight at once. With a
    per-fetch await-sleep, a concurrent crawl overlaps them — a crawl that
    awaited each child fully before the next would show max_in_flight == 1.
    Also confirms each page is fetched exactly once.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/c{i}" for i in range(8)],
    }
    for i in range(8):
        graph[f"http://a.com/c{i}"] = []

    parser = AsyncRecordingHtmlParser(graph, delay=0.02)
    got = PureAsyncCrawler().crawl("http://a.com/root", parser)

    assert parser.max_in_flight >= 2, (
        "fetches never overlapped — visit() is awaiting each child instead "
        "of scheduling it as a task"
    )
    expected = {"http://a.com/root"} | {f"http://a.com/c{i}" for i in range(8)}
    assert set(got) == expected
    for url in expected:
        assert parser.call_count[url] == 1


# ---------------------------------------------------------------------------
# Tier 4 only: QueueCrawler.
#
# QueueCrawler shares the synchronous-parser contract, so the CRAWLERS tests
# above already cover correctness. Tier-4-specific: the visited set still
# needs a lock (the queue guards the frontier, not `seen`), and termination
# rides on queue.join()/task_done() rather than a hand-rolled counter — a
# miscounted task_done() will hang here instead of failing an assertion.
# ---------------------------------------------------------------------------


def test_queue_crawler_fetches_each_page_exactly_once() -> None:
    """40 workers race to claim one shared page: with a lock on the visited
    set, get_urls runs exactly once per page. A missing lock lets two
    workers both pass `link not in seen` and enqueue the same URL.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/p{i}" for i in range(40)],
        "http://a.com/shared": [],
    }
    for i in range(40):
        graph[f"http://a.com/p{i}"] = ["http://a.com/shared"]

    parser = RecordingHtmlParser(graph, delay=0.001)
    got = QueueCrawler(max_workers=16).crawl("http://a.com/root", parser)

    expected = {"http://a.com/root", "http://a.com/shared"}
    expected |= {f"http://a.com/p{i}" for i in range(40)}
    assert set(got) == expected
    for url in expected:
        assert parser.call_count[url] == 1, (
            f"{url} fetched {parser.call_count[url]}x — a worker lost the "
            f"test-then-add race on the visited set"
        )


def test_queue_crawler_runs_fetches_concurrently() -> None:
    """The worker pool must really parallelize. A root linking to many
    children gives independent fetches; with a per-fetch delay a concurrent
    crawler overlaps them, a secretly-sequential one never does.
    """
    graph: dict[str, list[str]] = {
        "http://a.com/root": [f"http://a.com/c{i}" for i in range(8)],
    }
    for i in range(8):
        graph[f"http://a.com/c{i}"] = []

    parser = RecordingHtmlParser(graph, delay=0.02)
    QueueCrawler(max_workers=8).crawl("http://a.com/root", parser)

    assert parser.max_in_flight >= 2, (
        "fetches never overlapped — the workers are not draining the queue "
        "in parallel"
    )


def test_queue_crawler_crawls_a_large_cross_linked_graph() -> None:
    """A 200-page chain where every page also links back to the root. Every
    reachable page must come back exactly once — a wrong join()/task_done()
    balance would stop early (drops pages) or hang (count never hits zero).
    """
    n = 200
    graph: dict[str, list[str]] = {}
    for i in range(n):
        links = ["http://a.com/0"]
        if i + 1 < n:
            links.append(f"http://a.com/{i + 1}")
        graph[f"http://a.com/{i}"] = links

    parser = RecordingHtmlParser(graph)
    got = QueueCrawler(max_workers=16).crawl("http://a.com/0", parser)

    assert sorted(got) == sorted(f"http://a.com/{i}" for i in range(n))
    for i in range(n):
        assert parser.call_count[f"http://a.com/{i}"] == 1

