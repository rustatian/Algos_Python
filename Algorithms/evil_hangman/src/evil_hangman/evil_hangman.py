"""Evil Hangman — adversarial dictionary partitioning (CS106B).

A variant of Hangman where the computer never commits to a secret word
upfront. After each guess, the adversary partitions its remaining
candidate dictionary into groups keyed by where the guessed letter
*would* appear in the reveal pattern, then commits only to the group
that lets it delay the player longest. The chosen group is always
self-consistent (every word in it is compatible with every reveal so
far) — the adversary is maximally cruel but never cheats.

This package ports the problem as a tiered learning ladder. Tier 1
is the pure per-turn decision; subsequent tiers wrap it in a game
loop, optimise pattern computation for large dictionaries, and
finally lift the whole thing into a distributed game-as-a-service.

Tier 1: evil_hangman_reveal(...)  — pure function; one adversary decision.
Tier 2: EvilHangmanGame           — stateful wrapper; full game across turns.
Tier 3: IndexedEvilHangman        — precomputed pattern index; scales to large dicts.
Tier 4: DistributedEvilHangman    — game-as-a-service HLD; sharded sessions.

Input:
    candidates : set[str]
        Words still consistent with the reveals so far. All entries
        have the same length (the game's word length).
    guess : str
        A single lowercase letter the player just guessed.
    revealed : str
        Current reveal pattern. ``'_'`` marks un-revealed slots;
        non-underscore characters are letters revealed in prior turns.
        ``len(revealed)`` equals the word length.

Output:
    tuple[set[str], str]
        (new_candidates, new_revealed) — the adversary's committed
        partition and the updated reveal pattern.

Example 1 (single group — every word maps to the same pattern):
    candidates = {"sale", "have", "rate", "bale"}
    guess = "e", revealed = "_a__"
    -> ({"sale", "have", "rate", "bale"}, "_a_e")
    Explanation: every word ends in 'e' and 'a' was already revealed
    at index 1 — only one pattern exists, so it is trivially chosen.

Example 2 (two groups — size wins outright):
    candidates = {"sale", "have", "rate", "bone", "shoe"}
    guess = "a", revealed = "____"
    -> ({"sale", "have", "rate"}, "_a__")
    Explanation: three words place 'a' at index 1, two don't.
    3 > 2 — the larger group is picked.

Example 3 (tie broken by fewest reveals = most underscores):
    candidates = {"sale", "have", "bone", "shoe"}
    guess = "a", revealed = "____"
    -> ({"bone", "shoe"}, "____")
    Explanation: both groups have 2 words. The "_a__" group reveals
    one letter; the "____" group reveals none. Cruellest = fewest
    reveals — the adversary picks the no-reveal group.

See README.md for the full tier ladder and the Tier 4 architecture.
"""

from collections import defaultdict


def _pattern_for(word: str, revealed: str, guess: str) -> str:
    """Return the reveal pattern ``word`` would produce under ``guess``.

    Three branches per position: a letter already revealed in a prior turn
    stays put; the guessed letter is filled in wherever it occurs in this
    word; every other slot stays hidden as ``'_'``. This string is the key
    that groups words into partitions — two words share a partition exactly
    when the guess would reveal the same thing about both.
    """
    chars: list[str] = []
    for i in range(len(word)):
        if revealed[i] != "_":
            chars.append(revealed[i])  # a prior reveal sticks
        elif word[i] == guess:
            chars.append(guess)  # the guess lands here
        else:
            chars.append("_")  # still hidden
    return "".join(chars)


def evil_hangman_reveal(
    candidates: set[str],
    guess: str,
    revealed: str,
) -> tuple[set[str], str]:
    """Tier 1: one adversary decision in evil hangman.

    Partitions ``candidates`` by their reveal-pattern under ``guess``,
    then commits to the group that maximises ``(group_size, hidden_slots)``.
    Pure function — no state carried between calls. The full game
    (Tier 2) calls this once per player guess.

    Input:
        candidates : set[str] — current candidate dictionary; all
            words have the same length as ``revealed``.
        guess : str — one lowercase letter.
        revealed : str — current reveal pattern; ``'_'`` for hidden.
    Output:
        tuple[set[str], str]
            (new_candidates, new_revealed) — the words still in play
            after this turn and the reveal pattern shown to the player.

    Example 1 (single group):
        candidates = {"sale", "have", "rate", "bale"}
        guess = "e", revealed = "_a__"
        result = ({"sale", "have", "rate", "bale"}, "_a_e")

    Example 2 (size wins outright):
        candidates = {"sale", "have", "rate", "bone", "shoe"}
        guess = "a", revealed = "____"
        result = ({"sale", "have", "rate"}, "_a__")

    Example 3 (tie broken by fewest reveals):
        candidates = {"sale", "have", "bone", "shoe"}
        guess = "a", revealed = "____"
        result = ({"bone", "shoe"}, "____")

    Standard library:
        collections.defaultdict — auto-creates an empty set per
            pattern; one-liner for "group-by".

    Pseudocode:
        helper pattern_for(word, revealed, guess):
            chars = []
            for i in 0 .. len(word) - 1:
                if revealed[i] != '_':
                    chars.append(revealed[i])      # prior reveal sticks
                elif word[i] == guess:
                    chars.append(guess)            # guess lands here
                else:
                    chars.append('_')              # still hidden
            return ''.join(chars)

        groups = defaultdict(set)
        for word in candidates:
            groups[pattern_for(word, revealed, guess)].add(word)

        # Cruelty score for a pattern p: (len(groups[p]), p.count('_')).
        # Tuple comparison: size wins outright; underscores break ties.
        best_pattern = max(groups, key=lambda p: (len(groups[p]), p.count('_')))

        return (groups[best_pattern], best_pattern)

    Why max with a tuple key:
        Python sorts tuples lexicographically. ``(5, 2) > (4, 9)`` because
        5 > 4 ("size wins"); ``(3, 4) > (3, 2)`` because the sizes tie
        and 4 > 2 ("more underscores wins the tie"). Two ranked
        comparisons in one expression — no nested if/else.

    Why a separate ``pattern_for`` helper:
        The per-word logic is three-branch and is called once per
        candidate. Naming it lets the main loop read as one sentence —
        "for each word, find its bucket". Inlining would tangle
        partition logic with bucket-key construction.

    Why ``defaultdict(set)`` over ``dict.setdefault``:
        ``defaultdict`` auto-creates the empty set on first hit; the
        ``setdefault`` form requires writing the default each time
        ``groups.setdefault(p, set()).add(word)``. Same semantics,
        slightly less repetitive.

    Why 'fewest reveals' as the tie-breaker:
        The adversary is maximally cruel — if it can preserve N words
        either way, it picks the option that gives the player less
        information. This is the Schwarz / CS106B standard definition.
        Alternative tie-breakers (alphabetical, most reveals) produce
        valid-but-less-cruel adversaries.

    Why returning the chosen pattern (not just the words):
        The caller (Tier 2's game loop) needs both: the new candidate
        set to feed into the next call, and the reveal string to show
        the player. Returning both in one tuple keeps the contract
        minimal — no second function call to "compute the reveal".

    Complexity:
        Let N = |candidates|, L = word length, K = distinct patterns.
        Time:  O(N * L)        — pattern_for is L work per word; the
                                 final max is O(K) ≤ O(N).
        Space: O(N + K * L)    — every candidate appears in exactly
                                 one group set; K pattern keys of
                                 length L.
    """
    # Partition the candidates by the pattern this guess would produce.
    groups: dict[str, set[str]] = defaultdict(set)
    for word in candidates:
        groups[_pattern_for(word, revealed, guess)].add(word)

    # Cruellest legal choice: the largest surviving group; ties broken
    # toward the pattern that reveals the FEWEST letters (most underscores).
    # Tuple comparison ranks size first, then underscore count.
    best_pattern = max(groups, key=lambda p: (len(groups[p]), p.count("_")))

    return groups[best_pattern], best_pattern
