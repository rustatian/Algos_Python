from collections import defaultdict


def evil_hangman_reveal(
    candidates: set[str],
    guess: str,
    revealed: str,
) -> tuple[set[str], str]:
    """
    candidates: words still consistent with reveals so far. All same length.
    guess: a single lowercase letter the player just guessed.
    revealed: current revealed pattern, e.g. "_a__" — underscores for unrevealed.
              Length equals word length.

    Returns: (new_candidates, new_revealed) — the adversary's chosen partition
             and the new revealed pattern after this guess.
    """

    # Partition the surviving words by WHERE the guessed letter lands.
    # Two words fall in the same bucket iff `guess` appears at exactly the
    # same positions in both — that shared shape is all the guess reveals.
    groups: dict[str, set[str]] = defaultdict(set)
    for word in candidates:
        pattern = ""
        for ch in word:
            if ch == guess:
                pattern += guess
            else:
                pattern += "_"
        groups[pattern].add(word)

    # The adversary keeps the largest bucket (most ambiguity remaining).
    # Ties break toward the bucket that reveals the fewest letters (most
    # underscores), then lexicographically so the choice is deterministic.
    best_pattern = max(
        groups,
        key=lambda p: (len(groups[p]), p.count("_"), p),
    )

    # Fold the chosen bucket's guess-pattern into what was already shown:
    # keep old revealed letters, fill in the positions this guess uncovers.
    new_revealed = ""
    for i in range(len(revealed)):
        if best_pattern[i] == guess:
            new_revealed += guess
        else:
            new_revealed += revealed[i]

    return groups[best_pattern], new_revealed


def evil_guess(candidates: set[str], guess: str) -> tuple[set[str], str]:
    d: dict[str, set[str]] = defaultdict(set)

    for cand in candidates:
        pattern = ""
        for ch in cand:
            if ch == guess:
                pattern += guess
            else:
                pattern += "_"
        d[pattern].add(cand)

    best_pattern = max(d, key=lambda p: (len(d[p]), p.count('_'), p))
    return set(d[best_pattern]), best_pattern


print(evil_guess({"ally", "cool", "good", "hood", "wood"}, "o"))
print(evil_hangman_reveal({"ally", "cool", "good", "hood", "wood"}, "o", "____"))
