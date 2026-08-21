import threading
from collections.abc import Sequence

import pytest

from probe7.src import CapacityExceededError, DuplicateInstanceError, LoadBalancer
from probe7.src.strategies import RoundRobinStrategy


class FirstGet:
    def choose(self, servers: Sequence[str]):
        return servers[0]


@pytest.mark.asyncio
async def test_general_valid():
    lb = LoadBalancer()
    for i in range(3):
        lb.register(f"server-{i}")
    assert len(lb._servers) == 3


def test_register():
    lb = LoadBalancer()
    for i in range(3):
        lb.register(f"server-{i}")

    with pytest.raises(DuplicateInstanceError):
        lb.register("server-1")


@pytest.mark.asyncio
async def test_capacity_exceeded():
    lb = LoadBalancer(capacity=3)
    for i in range(3):
        lb.register(f"server-{i}")

    with pytest.raises(CapacityExceededError):
        lb.register("server-3")


@pytest.mark.asyncio
async def test_first_get():
    lb = LoadBalancer(strategy=FirstGet())
    lb.register("a")
    lb.register("b")

    assert lb.get() == "a"


@pytest.mark.asyncio
async def test_excepton_on_empty():
    lb = LoadBalancer()
    with pytest.raises(ValueError):
        lb.get()


@pytest.mark.asyncio
async def test_round_robin():
    lb = LoadBalancer(strategy=RoundRobinStrategy())
    for i in range(3):
        lb.register(f"server-{i}")

    assert [lb.get() for _ in range(3)] == ["server-0", "server-1", "server-2"]


@pytest.mark.asyncio
async def test_unregister():
    lb = LoadBalancer()
    for i in range(3):
        lb.register(f"server-{i}")
    lb.unregister("server-1")
    assert lb._servers == ["server-0", "server-2"]


@pytest.mark.asyncio
async def test_concurrent_access():
    lb = LoadBalancer(capacity=100)
    # b = threading.Barrier(3)
    rejected: list[int] = []
    for i in range(100):
        lb.register(f"server-{i}")

    def worker(n: int):
        # b.wait()
        try:
            lb.register(f"server-{n}")
        except DuplicateInstanceError:
            rejected.append(n)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(rejected) == 100
    assert len(lb._servers) == 100
