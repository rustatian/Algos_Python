"""Word Guessing Feedback (Wordle-like) — tiered learning port.

Public API:
    Feedback        — MATCH / EXISTS / ABSENT verdict enum.
    feedback        — Tier 1: the two-pass per-position feedback function.
    WordleGame      — Tier 2: stateful game with a secret + guess budget.
    GameStatus      —         PLAYING / WON / LOST.
    GuessResult     —         the result of one WordleGame.guess().
    CandidateFilter — Tier 3: the solver side; narrow a dictionary by clues.

Tier 4 (DistributedWordleService) is an architecture discussion, not code
— see README.md.
"""

from word_guessing_feedback.word_guessing_feedback import (
    CandidateFilter,
    Feedback,
    GameStatus,
    GuessResult,
    WordleGame,
    feedback,
)

__all__ = [
    "Feedback",
    "feedback",
    "WordleGame",
    "GameStatus",
    "GuessResult",
    "CandidateFilter",
]
