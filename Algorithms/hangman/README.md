# Hangman — Optimal Strategy

Both players play optimally; the guesser wants to **minimize the worst-case
number of wrong guesses**. This is **minimax over a candidate dictionary**.
The companion to this repo's `evil_hangman` (which *is* the adversary) —
here we compute the guesser's optimal counter-strategy against that
adversary.

Modeled on the classic "optimal Hangman" question. Related LeetCode
references: #843 (Guess the Word — Mastermind), #486 (Predict the Winner —
minimax), #464 (Can I Win — memoized game), #375 (Guess Number Higher or
Lower II).

## Problem

A dictionary of equal-length words is the set of possible secrets. The
guesser names a letter; the adversary, who has not committed to a secret,
announces the letter's positions (or "absent") in whichever consistent way
hurts the guesser most. A guess of a letter that turns out **absent** costs
one wrong guess. Find the minimum number of wrong guesses the guesser can
guarantee.

Guessing a letter `L` **partitions** the candidates by `L`'s *signature* —
the tuple of positions where `L` appears (the empty tuple = "absent"). The
adversary picks the partition that maximizes the guesser's remaining cost;
the guesser picks the letter that minimizes that maximum:

```python
MemoizedHangman().min_wrong_guesses({"ab", "ac"})  # -> 1
# 'a' is shared (reveals nothing decisive); telling "ab" from "ac" forces a
# b-or-c guess, one of which misses -> 1 wrong in the worst case.
```

## Tiers

| Tier | Class | Strategy | The lesson |
|------|-------|----------|------------|
| 1 | `BruteForceHangman` | minimax, no cache | the recurrence — chooser maxes, guesser mins, over dictionary partitions |
| 2 | `MemoizedHangman` | minimax memoized on `(frozenset, revealed)` | the optimization — collapse the exponential tree by caching states |
| 3 | `GreedyHangman` | one-ply "minimize the largest partition" | a fast near-optimal heuristic that scales past minimax |
| 4 | `DistributedSolver` | precompute strategy at scale | the system-design follow-up — strategy tables, sharded by dictionary |

`play_game(strategy, words, secret)` is a shared driver that simulates any
strategy against a fixed secret and returns the wrong-guess count — the
glue the tests use to confirm the solver's value matches reality.

Each tier answers the previous one's weak spot. Tier 1 expresses the
minimax recurrence directly but recomputes the same `(words, revealed)`
state along many paths — exponential. Tier 2 memoizes on
`(frozenset(words), revealed)`: optimal play depends only on which
candidates remain and what is revealed, never on the path taken there, so
one cached answer serves every path into a state. Tier 3 abandons exact
optimality for speed — it looks one ply ahead and guesses the letter whose
*largest* resulting partition is smallest (shrinking the worst-case
surviving set fastest), which is O(letters × words) per move and scales to
dictionaries the full game tree cannot. Tier 4 precomputes strategy at
scale.

### Why memoize on `(frozenset(words), revealed)`

The future of the game depends only on the surviving candidate set and the
revealed pattern. A `frozenset` is hashable (a plain `set` is not) and
order-independent, so two different guess sequences arriving at the same
candidates + reveals hit the same cache entry. Crucially, a
previously-guessed-and-absent letter appears in *no* surviving candidate,
so it never re-enters the "letters worth guessing" set — which is why
`(words, revealed)` alone is a sufficient state and the recursion
terminates (the candidate set strictly shrinks on every move).

### Why the "absent" outcome is just another partition

A natural mistake is to treat "letter not in the word" as a special case.
It is not: the absent words form the partition keyed by the empty
signature, and the adversary weighs it alongside the revealing partitions.
The only difference is that taking the absent partition costs `+1` wrong
guess; the recursion is otherwise uniform.

## Tier 4 — the system-design follow-up (optimal solver at scale)

Minimax over a real dictionary (tens of thousands of words, multiple
lengths) is expensive to compute on demand. The follow-up: *serve optimal
(or near-optimal) Hangman play to many concurrent games.*

**Opener questions.** One fixed dictionary or many (per language / word
length)? Must play be provably optimal, or is the greedy heuristic
acceptable? Latency budget per move? Are games independent, or is there a
shared adversary?

**Design sketch.**

- **Precompute, don't solve live.** For a fixed dictionary, the optimal
  first several moves are the same for every game, so precompute a
  **strategy table**: `(candidate-set-id, revealed) -> best letter`,
  built offline by the Tier 2 minimax and stored in a KV store. Serving a
  move is then a lookup, not a search.
- **Shard by dictionary slice.** Partition the precompute by word length
  (and language); each shard's strategy table is independent. The offline
  minimax job is itself the recursive-self-spawning pattern — each
  `(words, revealed)` state spawns child states.
- **Fall back to greedy** for states not in the table (deep, rare
  positions): Tier 3 runs in milliseconds and is near-optimal, so the
  table need only cover the common early game.
- **Per-game state** (current candidates, revealed, wrong count) lives in a
  session store keyed by `game_id`, exactly like the `word_guessing_feedback`
  and `evil_hangman` services.

**Failures.** Strategy-table miss → greedy fallback (always available).
Precompute too large to store fully → store only the first K plies; greedy
beyond. Dictionary update → rebuild the affected shard's table offline,
swap atomically.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `MemoizedHangman` recursion | the offline minimax job that builds the strategy table |
| the memo dict | a persisted `(state -> best letter)` table in a KV store |
| `GreedyHangman` | the online fallback for uncached states |
| `play_game` loop | one game session's move history in the session store |

## Running the tests

```sh
uv run pytest Algorithms/hangman/tests/ -q
```
