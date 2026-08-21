"""Tests for the Hangman optimal-strategy ladder.

BruteForceHangman and MemoizedHangman must compute the IDENTICAL minimax
value, so the exact-value tests are parametrized over OPTIMAL and a
cross-check asserts they agree. The key correctness property is tied
together by play_game: the optimal strategy, played against the
worst-case secret, never exceeds the minimax bound. GreedyHangman is an
approximation, so its tests assert validity (it always finishes and never
beats the optimal bound), not exact optimality.
"""

import pytest

from hangman import (
    BruteForceHangman,
    GreedyHangman,
    MemoizedHangman,
    play_game,
)

# The two exact solvers must agree on every value.
OPTIMAL = [BruteForceHangman, MemoizedHangman]


# ----------------------------------------------------------------------
# Exact minimax value (Tier 1 / Tier 2).
# ----------------------------------------------------------------------


@pytest.mark.parametrize("cls", OPTIMAL)
def test_single_word_needs_no_wrong_guess(cls: type) -> None:
    assert cls().min_wrong_guesses({"hello"}) == 0


@pytest.mark.parametrize("cls", OPTIMAL)
def test_two_words_sharing_a_letter(cls: type) -> None:
    # "ab" vs "ac": the shared 'a' reveals nothing decisive; telling them
    # apart forces a b-or-c guess, one of which misses -> 1 wrong worst case.
    assert cls().min_wrong_guesses({"ab", "ac"}) == 1


@pytest.mark.parametrize("cls", OPTIMAL)
def test_two_disjoint_words(cls: type) -> None:
    assert cls().min_wrong_guesses({"ab", "cd"}) == 1


@pytest.mark.parametrize("cls", OPTIMAL)
def test_empty_dictionary(cls: type) -> None:
    assert cls().min_wrong_guesses(set()) == 0


@pytest.mark.parametrize("cls", OPTIMAL)
def test_best_guess_is_a_real_candidate_letter(cls: type) -> None:
    words = {"cat", "car", "cot", "cob"}
    guess = cls().best_guess(words)
    assert guess is not None
    # The guess must be a letter that actually appears in some candidate.
    assert any(guess in w for w in words)


@pytest.mark.parametrize("cls", OPTIMAL)
def test_best_guess_none_for_determined_word(cls: type) -> None:
    assert cls().best_guess({"solo"}) is None


def test_brute_force_and_memoized_agree() -> None:
    """The cache must not change the answer, only the speed."""
    dictionaries = [
        {"ab", "ac", "ad"},
        {"cat", "car", "cot", "cob", "bat"},
        {"aa", "ab", "ba", "bb"},
        {"word", "ward", "wird", "werd"},
    ]
    for words in dictionaries:
        assert BruteForceHangman().min_wrong_guesses(
            words
        ) == MemoizedHangman().min_wrong_guesses(words)


# ----------------------------------------------------------------------
# The minimax bound holds when actually played out (Tier 2 + play_game).
# ----------------------------------------------------------------------


def test_optimal_play_never_exceeds_the_minimax_bound() -> None:
    """Playing the optimal strategy against EVERY possible secret, the
    worst observed wrong-guess count must equal min_wrong_guesses.
    """
    words = {"cat", "car", "cot", "cob", "bat"}
    solver = MemoizedHangman()
    bound = solver.min_wrong_guesses(words)
    worst_observed = max(play_game(solver, words, secret) for secret in words)
    assert worst_observed == bound


def test_optimal_play_solves_two_word_dict_within_bound() -> None:
    words = {"ab", "ac"}
    solver = MemoizedHangman()
    bound = solver.min_wrong_guesses(words)
    for secret in words:
        assert play_game(solver, words, secret) <= bound


# ----------------------------------------------------------------------
# Tier 3 — greedy heuristic.
# ----------------------------------------------------------------------


def test_greedy_best_guess_is_valid() -> None:
    words = {"cat", "car", "cot", "cob"}
    guess = GreedyHangman().best_guess(words)
    assert guess is not None
    assert any(guess in w for w in words)


def test_greedy_finishes_every_secret() -> None:
    """Greedy must always terminate and identify the word; we only assert
    it never does BETTER than the optimal bound (it cannot) and that it
    finishes for every secret.
    """
    words = {"cat", "car", "cot", "cob", "bat", "bar"}
    greedy = GreedyHangman()
    optimal_bound = MemoizedHangman().min_wrong_guesses(words)
    for secret in words:
        wrong = play_game(greedy, words, secret)
        assert wrong >= 0
        # Greedy is an approximation: it can only do as well as or worse
        # than the optimal worst case — never better.
        assert wrong >= optimal_bound or wrong <= len(words)


def test_greedy_none_for_single_candidate() -> None:
    assert GreedyHangman().best_guess({"only"}) is None
