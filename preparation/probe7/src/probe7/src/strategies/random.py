import random


class RandomStrategy:
    def choose(self, servers: list[str]) -> str:
        return random.choice(servers)
