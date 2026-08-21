"""Hangman — optimal guessing strategy (minimax over a dictionary).

Both sides play optimally and the guesser wants to **minimize the
worst-case number of wrong guesses**. The chooser is adversarial: after the
guesser names a letter, the chooser reveals that letter's positions (or
declares it absent) in whatever way is worst for the guesser — as long as
at least one dictionary word remains consistent with everything revealed
so far.

This is **minimax over the candidate dictionary**. Guessing a letter ``L``
partitions the candidates by ``L``'s *signature* — the tuple of positions
where ``L`` occurs in a word (the empty tuple meaning "absent"). The
chooser picks the partition that maximizes the guesser's remaining cost;
the guesser picks the letter that minimizes that maximum. The "absent"
partition costs one wrong guess; a revealing partition costs none but
shrinks the puzzle.

Note the contrast with this repo's ``evil_hangman``: there we *play* the
adversary (pick the cruellest partition for one turn); here we compute the
*guesser's optimal counter-strategy* against that adversary.

This package ports the problem as a tiered learning ladder:

Tier 1: BruteForceHangman — minimax with no memoization; correct, exponential.
Tier 2: MemoizedHangman   — same minimax, memoized on (frozenset, revealed).
Tier 3: GreedyHangman     — fast one-ply heuristic; scales to big dictionaries.
Tier 4: DistributedSolver — HLD only (see README); precompute strategy at scale.

Input:
    min_wrong_guesses(words: Iterable[str]) -> int
        The minimax value: wrong guesses in the worst case under optimal play.
    best_guess(words, revealed: str | None = None) -> str | None
        The optimal next letter (None when the word is already determined).
Output:
    min_wrong_guesses returns the worst-case wrong-guess count; best_guess
    returns one lowercase letter (or None).

All words in a call must be the same length (they are candidates for one
secret). ``revealed`` is a same-length pattern of letters and ``'_'``.

Example 1 (one wrong guess is unavoidable to tell two words apart):
    BruteForceHangman().min_wrong_guesses({"ab", "ac"})  -> 1
    Explanation: 'a' is shared, so it reveals nothing decisive; telling
    "ab" from "ac" forces a b-or-c guess, one of which is wrong.

Example 2 (a single candidate needs no wrong guess):
    MemoizedHangman().min_wrong_guesses({"hello"})  -> 0

Example 3 (the optimal opening letter):
    MemoizedHangman().best_guess({"cat", "car", "cot"})  -> a letter that
    splits the dictionary to minimize the worst surviving partition.

See README.md for the full ladder discussion and the Tier 4 architecture.
"""

from typing import Iterable


# --- shared puzzle mechanics ------------------------------------------------


def _signature(word: str, letter: str) -> tuple[int, ...]:
    """Positions of ``letter`` in ``word`` — the empty tuple if absent.

    This tuple is the chooser's "reveal": guessing ``letter`` against a
    secret partitions the dictionary by exactly this value.
    """
    return tuple(i for i, ch in enumerate(word) if ch == letter)


def _partition(
    words: frozenset[str], letter: str
) -> dict[tuple[int, ...], frozenset[str]]:
    """Group ``words`` by their signature under ``letter``.

    Each distinct signature is one outcome the chooser could announce; the
    empty-tuple key, if present, is the "absent" (wrong-guess) partition.
    """
    groups: dict[tuple[int, ...], set[str]] = {}
    for word in words:
        groups.setdefault(_signature(word, letter), set()).add(word)
    return {sig: frozenset(group) for sig, group in groups.items()}


def _reveal(revealed: str, letter: str, signature: tuple[int, ...]) -> str:
    """Return ``revealed`` with ``letter`` written in at ``signature`` positions."""
    chars = list(revealed)
    for i in signature:
        chars[i] = letter
    return "".join(chars)


def _candidate_letters(words: frozenset[str], revealed: str) -> set[str]:
    """Letters worth guessing: those in some candidate, not yet revealed.

    A letter already in ``revealed`` has had all its positions shown (a
    reveal exposes every occurrence). A letter previously guessed-and-absent
    appears in no remaining candidate, so it never re-enters this set —
    which is why the state ``(words, revealed)`` alone is sufficient and the
    recursion always terminates.
    """
    revealed_letters = {ch for ch in revealed if ch != "_"}
    present = {ch for word in words for ch in word}
    return present - revealed_letters


class BruteForceHangman:
    """Tier 1: exact minimax solver, no memoization.

    Computes the worst-case wrong-guess count under optimal play by full
    recursion over the dictionary partitions. Correct but exponential —
    the same ``(words, revealed)`` state is recomputed along many paths.
    Tier 2 fixes that with a cache.

    Input / Output:
        min_wrong_guesses(words) -> int
        best_guess(words, revealed=None) -> str | None

    Example:
        BruteForceHangman().min_wrong_guesses({"ab", "ac"})  -> 1

    Standard library:
        (none beyond builtins) — the recursion is plain dict/set work.

    Pseudocode:
        solve(words, revealed):
            if len(words) <= 1: return 0          # word determined; rest are correct
            best = +inf
            for L in candidate_letters(words, revealed):
                parts = partition(words, L)
                worst = max over (sig, group) in parts of:
                    if sig == ():  1 + solve(group, revealed)       # wrong guess
                    else:          solve(group, reveal(revealed,L,sig))  # correct
                best = min(best, worst)
            return best

    Why the terminal is ``len(words) <= 1``:
        Once a single candidate remains the secret is known; every
        remaining letter can be guessed correctly, so zero further WRONG
        guesses are needed — and wrong guesses are all we are counting.

    Why the chooser takes the MAX and the guesser the MIN:
        The chooser is adversarial and will announce whichever consistent
        outcome hurts most (max); the guesser, anticipating that, picks the
        letter whose worst outcome is least bad (min). That is minimax.

    Complexity:
        Exponential without memoization — each state fans out over every
        candidate letter and every partition. Fine for tiny dictionaries;
        Tier 2 is what makes real ones tractable.
    """

    def min_wrong_guesses(self, words: Iterable[str]) -> int:
        ws = frozenset(words)
        if not ws:
            return 0
        revealed = "_" * len(next(iter(ws)))
        return self._solve(ws, revealed)

    def best_guess(
        self, words: Iterable[str], revealed: str | None = None
    ) -> str | None:
        ws = frozenset(words)
        if len(ws) <= 1:
            return None
        if revealed is None:
            revealed = "_" * len(next(iter(ws)))
        letters = _candidate_letters(ws, revealed)
        if not letters:
            return None
        # Pick the letter with the least-bad worst case; ties broken
        # alphabetically for determinism.
        return min(letters, key=lambda L: (self._worst_case(ws, revealed, L), L))

    def _solve(self, words: frozenset[str], revealed: str) -> int:
        if len(words) <= 1:
            return 0
        letters = _candidate_letters(words, revealed)
        if not letters:
            return 0
        return min(self._worst_case(words, revealed, L) for L in letters)

    def _worst_case(self, words: frozenset[str], revealed: str, letter: str) -> int:
        """The chooser's best (worst-for-guesser) response to guessing ``letter``."""
        worst = 0
        for sig, group in _partition(words, letter).items():
            if sig == ():  # letter absent -> a wrong guess
                cost = 1 + self._solve(group, revealed)
            else:  # letter revealed -> no wrong guess, smaller puzzle
                cost = self._solve(group, _reveal(revealed, letter, sig))
            worst = max(worst, cost)
        return worst


class MemoizedHangman(BruteForceHangman):
    """Tier 2: the same exact minimax, memoized on ``(words, revealed)``.

    Identical results to Tier 1 — but each distinct ``(frozenset(words),
    revealed)`` state is solved once and cached, collapsing the exponential
    recomputation into work proportional to the number of reachable states.
    This is the practical optimal solver.

    Input / Output:
        Same as BruteForceHangman.

    Standard library:
        dict — the memo table keyed by ``(words, revealed)``. ``frozenset``
            is hashable, so it is a legal key (a plain ``set`` is not).

    Why the memo key is ``(frozenset(words), revealed)``:
        The optimal play from a position depends ONLY on which candidates
        remain and what has been revealed — not on the path taken to get
        there. Two different guess sequences that arrive at the same
        (words, revealed) have the same future, so one cached answer serves
        both. ``frozenset`` makes the candidate set hashable and
        order-independent.

    Complexity:
        Time proportional to (reachable states) × (letters × partition
        work) — polynomial in the dictionary for fixed word length, versus
        Tier 1's exponential blowup.
    """

    def __init__(self) -> None:
        self._memo: dict[tuple[frozenset[str], str], int] = {}

    def _solve(self, words: frozenset[str], revealed: str) -> int:
        key = (words, revealed)
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        value = super()._solve(words, revealed)
        self._memo[key] = value
        return value


class GreedyHangman:
    """Tier 3: fast one-ply heuristic guesser for large dictionaries.

    Full minimax (Tier 2) is exact but explores the whole game tree. For a
    big dictionary that is too slow, so this tier looks just ONE ply ahead:
    guess the letter whose worst immediate partition is smallest — i.e.
    minimize ``max(len(partition))`` over the letter's outcomes. That keeps
    the surviving candidate set as small as possible after one reveal, a
    well-known near-optimal heuristic (it is greedy on partition size, not
    on the full game value).

    Input / Output:
        best_guess(words, revealed=None) -> str | None
            The heuristic next letter; None when the word is determined.

    Example:
        GreedyHangman().best_guess({"cat", "cot", "cut"})  -> "a"/"o"/"u"-style
        split letter (the vowel position is what distinguishes them).

    Standard library:
        (none beyond builtins).

    Pseudocode:
        best_guess(words, revealed):
            if len(words) <= 1: return None
            for L in candidate_letters(words, revealed):
                score[L] = max(len(group) for group in partition(words, L))
            return argmin_L score[L]      # smallest worst-case survivor set

    Why minimize the LARGEST partition (not maximize information gain):
        The largest partition is the chooser's adversarial pick — it is the
        worst case for *this* turn. Minimizing it greedily shrinks the
        worst surviving set fastest. This ignores deeper consequences (so
        it is not guaranteed optimal), but it is O(letters × words) per
        move and empirically close to optimal.

    Complexity:
        best_guess: O(L × N) for L candidate letters over N words — no
        recursion, so it scales to dictionaries Tier 2 cannot.
    """

    def best_guess(
        self, words: Iterable[str], revealed: str | None = None
    ) -> str | None:
        ws = frozenset(words)
        if len(ws) <= 1:
            return None
        if revealed is None:
            revealed = "_" * len(next(iter(ws)))
        letters = _candidate_letters(ws, revealed)
        if not letters:
            return None

        def largest_partition(letter: str) -> int:
            return max(len(group) for group in _partition(ws, letter).values())

        # Smallest worst-case surviving set; alphabetical tie-break.
        return min(letters, key=lambda L: (largest_partition(L), L))


def play_game(strategy: object, words: Iterable[str], secret: str) -> int:
    """Simulate ``strategy`` guessing ``secret`` and count the WRONG guesses.

    A shared driver for any tier exposing ``best_guess(words, revealed)``.
    It walks the real game: ask the strategy for a letter, reveal it (or
    count a miss), prune the candidate set by what was learned, and repeat
    until a single candidate remains (the word is then determined and every
    further guess is correct).

    For the optimal strategies, ``play_game``'s result over the
    worst-case ``secret`` equals ``min_wrong_guesses(words)`` — the property
    the tests use to tie the solver and the simulator together.
    """
    candidates = frozenset(words)
    revealed = "_" * len(secret)
    wrong = 0

    while len(candidates) > 1:
        letter = strategy.best_guess(candidates, revealed)  # type: ignore[attr-defined]
        if letter is None:
            break
        sig = _signature(secret, letter)
        if sig == ():  # letter not in the secret -> a wrong guess
            wrong += 1
            candidates = frozenset(w for w in candidates if letter not in w)
        else:
            revealed = _reveal(revealed, letter, sig)
            candidates = frozenset(
                w for w in candidates if _signature(w, letter) == sig
            )

    return wrong
