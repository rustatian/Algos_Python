from collections.abc import Sequence


class RoundRobinStrategy:
    def __init__(self):
        self._index = 0

    def choose(self, servers: Sequence[str]) -> str:
        server: str = servers[self._index]
        self._index: int = (self._index % len(servers)) + 1
        return server
