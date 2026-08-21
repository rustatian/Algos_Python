import asyncio
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Protocol
import random
import threading

mydata = threading.local()


class DuplicateInstanceError(Exception):
    pass


class CapacityExceededError(Exception):
    pass


# example with ABC -----------
class StrategyABC(ABC):
    @abstractmethod
    def choose(self, servers: Sequence[str]) -> str: ...


class RandomStrategy(StrategyABC):
    def choose(self, servers: Sequence[str]) -> str:
        return random.choice(servers)


# ---------------------------


class Strategy(Protocol):
    def choose(self, servers: Sequence[str]) -> str: ...


class LoadBalancer:
    __slots__ = ["_servers", "_capacity", "_lock", "_strategy"]

    def __init__(self, capacity=10, strategy: Strategy | None = None):
        self._servers: list[str] = []
        self._capacity: int = capacity
        self._lock: threading.Lock = threading.Lock()
        self._strategy: Strategy = strategy or RandomStrategy()

    def register(self, server: str):
        with self._lock:
            if server in self._servers:
                raise DuplicateInstanceError(f"Server {server} is already registered")
            if len(self._servers) >= self._capacity:
                raise CapacityExceededError("LoadBalancer capacity exceeded")
            self._servers.append(server)

    def get(self) -> str:
        with self._lock:
            if not self._servers:
                raise ValueError("No servers registered")
            return self._strategy.choose(self._servers)

    def unregister(self, server: str):
        with self._lock:
            if server not in self._servers:
                raise ValueError(f"Server {server} is not registered")
            self._servers.remove(server)
