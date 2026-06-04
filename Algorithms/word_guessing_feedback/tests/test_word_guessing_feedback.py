"""Tests for the Word Guessing Feedback ladder.

Tier 1 (feedback) is the heart — its repeated-letter cases are the bugs
the two-pass algorithm exists to prevent, so they get the most coverage.
Tier 2 (WordleGame) and Tier 3 (CandidateFilter) build on it.
"""

from word_guessing_feedback import (
    CandidateFilter,
    Feedback,
    GameStatus,
    WordleGame,
    feedback,
)

M = Feedback.MATCH
E = Feedback.EXISTS
A = Feedback.ABSENT


# ----------------------------------------------------------------------
# Tier 1 — feedback().
# ----------------------------------------------------------------------


def test_all_match() -> None:
    assert feedback("apple", "apple") == [M, M, M, M, M]


def test_all_absent() -> None:
    assert feedback("abc", "xyz") == [A, A, A]


def test_all_exists_full_anagram() -> None:
    # Every letter present but none in place.
    assert feedback("abcd", "dcba") == [E, E, E, E]


def test_repeated_guess_letters_do_not_overclaim() -> None:
    """The canonical trap: two trailing guess-'a's have no unmatched 'a'."""
    assert feedback("aabb", "aaaa") == [M, M, A, A]


def test_repeated_secret_letters() -> None:
    # secret has two 'a's; guess has one 'a' (last pos) and one 'b' (first).
    assert feedback("aabb", "bxxa") == [E, A, A, E]


def test_one_exists_when_only_one_copy_available() -> None:
    """secret has a single 't'; guess has two — only one can be EXISTS."""
    # secret "cat": one 't'. guess "ttt": pos2 t==t MATCH; the other two t's
    # find no remaining 't' -> ABSENT.
    assert feedback("cat", "ttt") == [A, A, M]


def test_match_takes_priority_over_exists() -> None:
    # secret "aba", guess "aaa": pos0 match, pos2 match consume both a's;
    # pos1 'a' finds no remaining a -> ABSENT (not EXISTS).
    assert feedback("aba", "aaa") == [M, A, M]


def test_feedback_values_equal_curriculum_strings() -> None:
    """Feedback is a StrEnum, so it compares equal to the wording."""
    assert feedback("ab", "ab") == ["match", "match"]
    assert Feedback.ABSENT == "not exists"


# ----------------------------------------------------------------------
# Tier 2 — WordleGame.
# ----------------------------------------------------------------------


def test_game_win_on_exact_guess() -> None:
    g = WordleGame("apple", max_attempts=6)
    result = g.guess("apple")
    assert result.status is GameStatus.WON
    assert result.feedback == [M, M, M, M, M]


def test_game_decrements_attempts() -> None:
    g = WordleGame("apple", max_attempts=3)
    r1 = g.guess("alpha")
    assert r1.status is GameStatus.PLAYING
    assert r1.attempts_left == 2


def test_game_lost_when_attempts_exhausted() -> None:
    g = WordleGame("apple", max_attempts=2)
    g.guess("wrong")
    result = g.guess("nopes")
    assert result.status is GameStatus.LOST
    assert result.attempts_left == 0


def test_game_win_on_last_attempt() -> None:
    g = WordleGame("apple", max_attempts=2)
    g.guess("alpha")  # PLAYING, 1 left
    result = g.guess("apple")  # exact on the last attempt
    assert result.status is GameStatus.WON


def test_guess_after_game_over_is_noop() -> None:
    g = WordleGame("apple", max_attempts=1)
    g.guess("apple")  # WON immediately
    after = g.guess("xxxxx")
    assert after.status is GameStatus.WON
    assert after.feedback == []  # nothing computed after the game ends


# ----------------------------------------------------------------------
# Tier 3 — CandidateFilter.
# ----------------------------------------------------------------------


def test_filter_narrows_to_consistent_words() -> None:
    words = ["apple", "ample", "amply", "maple"]
    cf = CandidateFilter(words)
    # Pretend the secret is "apple"; observe the clue from guessing "maple".
    cf.observe("maple", feedback("apple", "maple"))
    survivors = cf.candidates()
    assert "apple" in survivors
    # "maple" itself cannot be the secret (it would have been all MATCH).
    assert "maple" not in survivors


def test_filter_converges_to_single_word() -> None:
    words = ["crane", "slate", "trace", "crate", "brace"]
    secret = "trace"
    cf = CandidateFilter(words)
    for guess in ("crane", "slate"):
        cf.observe(guess, feedback(secret, guess))
    survivors = cf.candidates()
    assert secret in survivors
    # Every survivor must itself be consistent with the clues.
    for w in survivors:
        assert feedback(w, "crane") == feedback(secret, "crane")


def test_filter_eliminates_all_when_inconsistent() -> None:
    cf = CandidateFilter(["abc", "abd"])
    # A clue no candidate can satisfy (all-match on a word not in the set).
    cf.observe("xyz", [Feedback.MATCH, Feedback.MATCH, Feedback.MATCH])
    assert cf.candidates() == []


def test_filter_candidates_returns_copy() -> None:
    cf = CandidateFilter(["abc"])
    got = cf.candidates()
    got.append("mutated")
    assert cf.candidates() == ["abc"]
