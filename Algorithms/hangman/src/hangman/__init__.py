"""Hangman — optimal guessing strategy, tiered learning port.

Public API:
    BruteForceHangman — Tier 1: exact minimax, no memoization.
    MemoizedHangman   — Tier 2: exact minimax, memoized (the practical solver).
    GreedyHangman     — Tier 3: one-ply heuristic for large dictionaries.
    play_game         — shared driver: simulate a strategy vs. a fixed secret,
                        returning the wrong-guess count.

Tier 4 (DistributedSolver) is an architecture discussion, not code — see
README.md.
"""

from hangman.hangman import (
    BruteForceHangman,
    GreedyHangman,
    MemoizedHangman,
    play_game,
)

__all__ = [
    "BruteForceHangman",
    "MemoizedHangman",
    "GreedyHangman",
    "play_game",
]
