"""Word Guessing Feedback (Wordle-like) — match / exists / not-exists.

Given a ``secret`` word and a ``guess`` of the same length, return
per-position feedback:

    MATCH  ("match")      — right letter, right position.
    EXISTS ("exists")     — the letter is in the secret, but elsewhere.
    ABSENT ("not exists") — the letter is not (or no longer) available.

The whole difficulty is **repeated letters**. The correct algorithm is
two passes over the word against a multiset (Counter) of the secret's
letters:

    Pass 1 — mark every exact MATCH, and build a Counter of the secret's
             letters EXCLUDING the matched positions (those are spoken for).
    Pass 2 — for each non-match position, if the guessed letter still has a
             remaining count, mark EXISTS and decrement; otherwise ABSENT.

A naive single pass that marks EXISTS for "letter appears somewhere in the
secret" over-counts when letters repeat. Example: secret ``AABB``, guess
``AAAA`` must yield ``MATCH, MATCH, ABSENT, ABSENT`` — the two trailing A's
have no unmatched A left to claim. Bulls and Cows (LeetCode #299) is this
exact algorithm.

This package ports the problem as a tiered learning ladder:

Tier 1: feedback(secret, guess)  — the two-pass algorithm (the core).
Tier 2: WordleGame               — stateful game: a fixed secret, a guess
                                    budget, win/lose tracking.
Tier 3: CandidateFilter          — the solver side: narrow a dictionary to
                                    the words consistent with all feedback.
Tier 4: DistributedWordleService — HLD only (see README); game-as-a-service.

Input:
    feedback(secret: str, guess: str) -> list[Feedback]
        secret, guess — equal-length lowercase words.
Output:
    A list of Feedback values, one per position, parallel to ``guess``.

Example 1 (no repeats):
    feedback("apple", "alpha")
    a:MATCH (pos 0), l:EXISTS, p:EXISTS, h:ABSENT, a:ABSENT
    Explanation: 'a' at 0 matches; 'l' and 'p' exist elsewhere; 'h' is
    absent; the second 'a' has no unmatched 'a' left -> ABSENT.

Example 2 (repeated guess letters — the tricky case):
    feedback("aabb", "aaaa")  -> [MATCH, MATCH, ABSENT, ABSENT]
    Explanation: positions 0,1 match the two real a's; positions 2,3 find
    no remaining 'a' (both were consumed by the matches) -> ABSENT.

Example 3 (repeated secret letters):
    feedback("aabb", "bxxa")
    b:EXISTS (a 'b' exists), x:ABSENT, x:ABSENT, a:EXISTS (an 'a' exists)

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable


class Feedback(StrEnum):
    """Per-position verdict. Values are the curriculum's wording, so a
    Feedback compares equal to its string ("match" / "exists" /
    "not exists") and prints cleanly.
    """

    MATCH = "match"
    EXISTS = "exists"
    ABSENT = "not exists"


def feedback(secret: str, guess: str) -> list[Feedback]:
    """Tier 1: two-pass per-position feedback for a guess against a secret.

    Input:
        secret, guess — equal-length words.
    Output:
        list[Feedback] parallel to ``guess``: MATCH / EXISTS / ABSENT.

    Example 1:
        feedback("aabb", "aaaa") -> [MATCH, MATCH, ABSENT, ABSENT]
    Example 2:
        feedback("abcd", "dcba") -> [EXISTS, EXISTS, EXISTS, EXISTS]
    Example 3:
        feedback("hello", "world")
        w:ABSENT, o:EXISTS, r:ABSENT, l:MATCH, d:ABSENT

    Standard library:
        collections.Counter — a multiset of the secret's unmatched
            letters. The decrement-on-claim in pass 2 is what makes
            repeated letters come out right.

    Pseudocode:
        result = [ABSENT] * n
        remaining = Counter()
        # Pass 1: exact matches; the rest of the secret is "available".
        for i in range(n):
            if guess[i] == secret[i]: result[i] = MATCH
            else:                     remaining[secret[i]] += 1
        # Pass 2: claim from the available pool, left to right.
        for i in range(n):
            if result[i] == MATCH: continue
            if remaining[guess[i]] > 0:
                result[i] = EXISTS; remaining[guess[i]] -= 1
        return result

    Why matches are counted out FIRST (pass 1 before pass 2):
        An exact match consumes that secret letter, so it must not also be
        available to satisfy an EXISTS elsewhere. Building the Counter from
        only the non-matched positions enforces that. Interleaving the two
        passes would let a matched letter be double-counted.

    Why left-to-right EXISTS assignment is fine:
        When more guess letters want a letter than the secret has spare,
        SOME must be ABSENT; which specific positions get EXISTS is a
        convention. Wordle and #299 both assign greedily left to right, and
        the *count* of EXISTS is fixed regardless of order.

    Complexity:
        Time O(n), space O(k) for the k distinct secret letters.
    """
    n = len(secret)
    result = [Feedback.ABSENT] * n

    # Pass 1: exact matches; everything else feeds the "available" multiset.
    remaining: Counter[str] = Counter()
    for i in range(n):
        if guess[i] == secret[i]:
            result[i] = Feedback.MATCH
        else:
            remaining[secret[i]] += 1

    # Pass 2: non-matches claim a remaining secret letter if one is left.
    for i in range(n):
        if result[i] is Feedback.MATCH:
            continue
        if remaining[guess[i]] > 0:
            result[i] = Feedback.EXISTS
            remaining[guess[i]] -= 1
        # else: stays ABSENT — no unmatched copy of this letter remains.

    return result


class GameStatus(StrEnum):
    """Lifecycle of a WordleGame."""

    PLAYING = "playing"
    WON = "won"
    LOST = "lost"


@dataclass(frozen=True)
class GuessResult:
    """The outcome of one WordleGame.guess() call."""

    feedback: list[Feedback]
    status: GameStatus
    attempts_left: int


class WordleGame:
    """Tier 2: a stateful guessing game over a fixed secret.

    Wraps Tier 1's pure feedback function with the game's state: the
    hidden secret, a budget of attempts, and a win/lose verdict.

    Input:
        __init__(secret: str, max_attempts: int = 6)
        guess(word: str) -> GuessResult
    Output:
        Each guess returns a GuessResult(feedback, status, attempts_left).
        Status is WON when every position matches, LOST when the budget
        is spent without a win, else PLAYING.

    Example:
        g = WordleGame(secret="apple", max_attempts=2)
        g.guess("alpha").status   -> PLAYING   (1 attempt left)
        g.guess("apple").status   -> WON

    Why a guess after the game is over is a no-op:
        Once WON or LOST, the secret is effectively revealed/spent. Further
        guesses return the terminal status with the same attempts_left and
        do NOT compute feedback — there is nothing left to play.

    Complexity:
        guess: O(n) for the feedback computation.
    """

    def __init__(self, secret: str, max_attempts: int = 6) -> None:
        self._secret = secret
        self._attempts_left = max_attempts
        self._status = GameStatus.PLAYING

    def guess(self, word: str) -> GuessResult:
        if self._status is not GameStatus.PLAYING:
            # Game already over — report the terminal state, play nothing.
            return GuessResult([], self._status, self._attempts_left)

        fb = feedback(self._secret, word)
        self._attempts_left -= 1

        if all(f is Feedback.MATCH for f in fb):
            self._status = GameStatus.WON
        elif self._attempts_left == 0:
            self._status = GameStatus.LOST

        return GuessResult(fb, self._status, self._attempts_left)

    @property
    def status(self) -> GameStatus:
        return self._status

    @property
    def attempts_left(self) -> int:
        return self._attempts_left


class CandidateFilter:
    """Tier 3: the solver side — narrow a dictionary by observed feedback.

    The inverse of Tier 1. Given a candidate dictionary and a history of
    (guess, feedback) observations, keep only the words that could still be
    the secret. A word ``w`` is consistent with an observation
    ``(guess, fb)`` iff playing that guess against ``w`` as the secret would
    have produced exactly ``fb`` — i.e. ``feedback(w, guess) == fb``. This
    is the constraint-propagation a Wordle solver runs after each turn.

    Input:
        __init__(words: Iterable[str])
        observe(guess: str, fb: list[Feedback]) -> None
            Filter the candidate set in place by this observation.
        candidates() -> list[str]
            The words still consistent with every observation so far.
    Output:
        candidates() returns the surviving candidate words.

    Example:
        words = ["apple", "ample", "amply", "maple"]
        cf = CandidateFilter(words)
        cf.observe("maple", feedback("apple", "maple"))   # secret is "apple"
        cf.candidates()  -> ["apple"]   (only word consistent with the clue)

    Why ``feedback(candidate, guess) == fb`` (candidate as the secret):
        We are testing "if the secret were this candidate, would the guess
        have produced what we saw?" So the candidate plays the role of the
        secret in the feedback function. Reusing Tier 1 keeps the solver and
        the game provably consistent — same function, both directions.

    Complexity:
        observe: O(C · n) — recompute feedback for each of C candidates.
    """

    def __init__(self, words: Iterable[str]) -> None:
        self._candidates: list[str] = list(words)

    def observe(self, guess: str, fb: list[Feedback]) -> None:
        self._candidates = [
            w for w in self._candidates if feedback(w, guess) == fb
        ]

    def candidates(self) -> list[str]:
        return list(self._candidates)
