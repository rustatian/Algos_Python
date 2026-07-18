"""Tests for evil_hangman_reveal (Tier 1).

Every test fixes (candidates, guess, revealed) and asserts the returned
(new_candidates, new_revealed). The adversary's policy is:

    1. Partition candidates by their reveal-pattern under `guess`.
    2. Pick the group maximising (group_size, count_of_underscores).
       — size wins outright; underscores break ties.
    3. Return (group, pattern).

A few tests assert "the chosen group is consistent with the chosen
pattern" rather than naming a specific group — that's the contract,
and it survives any internal renaming.
"""

from evil_hangman import evil_hangman_reveal


def test_single_group_all_words_match_one_pattern() -> None:
    """Every word produces the same pattern → trivially one group."""
    candidates = {"sale", "have", "rate", "bale"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "e", "_a__")
    assert new_cand == candidates
    assert new_revealed == "_a_e"


def test_larger_group_wins_outright() -> None:
    """When sizes differ, the larger group wins regardless of underscores."""
    candidates = {"sale", "have", "rate", "bone", "shoe"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "a", "____")
    # Three words have 'a' at index 1; two don't.
    assert new_cand == {"sale", "have", "rate"}
    assert new_revealed == "_a__"


def test_tie_broken_by_fewest_reveals() -> None:
    """On a size tie, the pattern with the most underscores wins."""
    candidates = {"sale", "have", "bone", "shoe"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "a", "____")
    # Both groups have 2 words. "_a__" reveals 1 letter; "____" reveals 0.
    assert new_cand == {"bone", "shoe"}
    assert new_revealed == "____"


def test_letter_absent_from_every_word_keeps_pattern() -> None:
    """A guess that no candidate contains → single group, no new reveals."""
    candidates = {"bone", "shoe", "dome"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "z", "____")
    assert new_cand == candidates
    assert new_revealed == "____"


def test_prior_reveals_are_preserved() -> None:
    """Letters already revealed remain in the pattern, position by position."""
    candidates = {"abba", "anna"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "b", "_a__")
    # "abba" → "_b_a"? no — revealed is "_a__", word is "abba"
    #   i=0: revealed='_', word[0]='a' (not 'b') → '_'
    #   i=1: revealed='a' (sticks) → 'a'
    #   i=2: revealed='_', word[2]='b' (== guess) → 'b'
    #   i=3: revealed='_', word[3]='a' (not 'b') → '_'
    #   pattern = "_ab_"
    # "anna" → no 'b' anywhere → "_a__"
    # Group sizes: {"_ab_": {abba}} (size 1, 2 underscores),
    #              {"_a__": {anna}} (size 1, 3 underscores).
    # Tie on size; "_a__" wins on underscores.
    assert new_cand == {"anna"}
    assert new_revealed == "_a__"


def test_multiple_positions_for_guess_in_a_word() -> None:
    """A word with the guess at multiple positions reveals all of them."""
    candidates = {"banana"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "a", "______")
    # 'a' at indices 1, 3, 5.
    assert new_cand == {"banana"}
    assert new_revealed == "_a_a_a"


def test_singleton_candidate_set() -> None:
    """One word in, one word out — pattern reflects that single word."""
    candidates = {"hello"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "l", "_____")
    assert new_cand == {"hello"}
    assert new_revealed == "__ll_"


def test_three_way_partition_picks_largest() -> None:
    """With three distinct patterns, the largest wins."""
    # All length 4. Guess 'e'.
    # "tree" → "__ee", "test" → "_e__", "true" → "___e",
    # "best" → "_e__", "type" → "__pe"? no, "type" is t-y-p-e → "___e"
    # Let's redo:
    #   tree: t-r-e-e → "__ee"
    #   test: t-e-s-t → "_e__"
    #   true: t-r-u-e → "___e"
    #   best: b-e-s-t → "_e__"
    #   type: t-y-p-e → "___e"
    # Groups: "__ee" → {tree}                 (size 1, 2 underscores)
    #         "_e__" → {test, best}           (size 2, 3 underscores)
    #         "___e" → {true, type}           (size 2, 3 underscores)
    # Tie at size 2; same underscore count (3 each).
    # Adversary picks whichever max() encounters first — non-deterministic
    # given set iteration order. Assert the contract holds either way.
    candidates = {"tree", "test", "true", "best", "type"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "e", "____")
    assert len(new_cand) == 2
    assert new_revealed in {"_e__", "___e"}
    if new_revealed == "_e__":
        assert new_cand == {"test", "best"}
    else:
        assert new_cand == {"true", "type"}


def test_returned_group_is_consistent_with_returned_pattern() -> None:
    """Property check: every word in new_cand maps back to new_revealed.

    This catches an accidental return of (group_for_A, pattern_for_B) —
    if the chosen group and chosen pattern come from different buckets,
    the per-word re-derivation will disagree with new_revealed.
    """
    candidates = {"sale", "have", "rate", "bone", "shoe"}
    guess = "a"
    revealed = "____"
    new_cand, new_revealed = evil_hangman_reveal(candidates, guess, revealed)
    for word in new_cand:
        # Re-derive the pattern for this specific word; it must equal new_revealed.
        rebuilt = "".join(
            revealed[i] if revealed[i] != "_"
            else (guess if word[i] == guess else "_")
            for i in range(len(word))
        )
        assert rebuilt == new_revealed, (
            f"{word!r} re-derives {rebuilt!r}, not {new_revealed!r}"
        )


def test_revealed_letter_in_pattern_stays_when_player_guesses_different_letter() -> None:
    """A previously-revealed 'a' must remain even when guessing 'e'."""
    candidates = {"made", "fade", "page"}
    new_cand, new_revealed = evil_hangman_reveal(candidates, "e", "_a__")
    # All three end in 'e' (made -> _a_e, fade -> _a_e, page -> _a_e).
    # Wait: "page" → p-a-g-e → "_a_e". Yes.
    # Single group.
    assert new_cand == {"made", "fade", "page"}
    assert new_revealed == "_a_e"
