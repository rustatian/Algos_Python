# Evil Hangman — adversarial dictionary partitioning

A Hangman variant where the computer never commits to a secret word
upfront. After each guess, the adversary partitions its remaining
candidate dictionary into groups keyed by the reveal pattern that
guess *would* produce, then picks the group that lets it delay the
player longest. The choice stays fair — every word in the chosen
group is consistent with every reveal so far — but the adversary
maximises cruelty.

```
candidates              partition by pattern              commit + reveal
{sale, have,            ┌─ "_a__" → {sale, have, rate}    ─→  pick the
 rate, bone,    ──→     │                                      cruellest
 shoe}                  └─ "____" → {bone, shoe}               group
```

Modeled on the Stanford CS106B "Evil Hangman" assignment (Keith
Schwarz, 2007) and the classic "design Hangman" interview question.

## Problem

Standard Hangman: the computer picks a word, the player guesses
letters, the computer reveals matches, the player loses after N wrong
guesses. **Evil** Hangman changes one rule: the computer doesn't pick
a word until the very end. After each guess, it picks whichever group
of still-consistent words keeps the most words alive — equivalent to
*lazily* committing to whichever word lets it stall longest.

The chosen group must be:
- **Self-consistent**: every word in the group produces the same
  reveal pattern under the current guess.
- **Non-empty**: there must always be at least one word the adversary
  could legally claim was its secret.

The cruelty policy:
1. Pick the group with the *most words* (more options → more delay).
2. On a tie, pick the group that reveals the *fewest letters* (less
   information for the player).

## Tiers

| Tier | Class / function | Strategy | The lesson |
|------|------------------|----------|------------|
| 1 | `evil_hangman_reveal(...)` | partition by pattern; pick largest+fewest-reveals | the core algorithm — pure function, no game state |
| 2 | `EvilHangmanGame` | wraps Tier 1 in turn loop; tracks revealed + guesses remaining | game state — the surface a player interacts with |
| 3 | `IndexedEvilHangman` | precomputed pattern index for the dictionary; positional letter indices | scaling — handles 10k+ word dictionaries without scanning every word per turn |
| 4 | `DistributedEvilHangman` | session-sharded API; persistent dictionary; quorum reads | the system-design follow-up — game-as-a-service architecture |

All tiers compose: Tier 2's game loop calls Tier 1's reveal function
once per guess. Tier 3 swaps in the indexed pattern computation
without changing the per-turn contract. Tier 4 wraps Tier 2 (or 3) in
an HTTP API.

## Tier 1: `evil_hangman_reveal`

```python
def evil_hangman_reveal(
    candidates: set[str],
    guess: str,
    revealed: str,
) -> tuple[set[str], str]:
    ...
```

One call = one adversary decision. The full game (Tier 2) calls this
repeatedly:

```python
words = {"sale", "have", "rate", "bone", "shoe"}
revealed = "____"

# Turn 1: player guesses 'a'
words, revealed = evil_hangman_reveal(words, "a", revealed)
# words = {"bone", "shoe"}, revealed = "____"  (adversary picked the no-reveal group)

# Turn 2: player guesses 'o'
words, revealed = evil_hangman_reveal(words, "o", revealed)
# words = {"bone", "shoe"}, revealed = "_o__"  (both have 'o' at index 1 — single group)
```

The pure-function shape makes Tier 1 trivially testable: fix
`(candidates, guess, revealed)`, assert the return. No fixtures,
no setup, no I/O.

## Tier 2: `EvilHangmanGame` (planned)

Stateful object holding the candidate set, the reveal pattern, the
guesses remaining, and the set of letters already guessed. Surface:

```python
game = EvilHangmanGame(dictionary, word_length=5, max_guesses=8)
game.display()         -> "_____" + "8 guesses left"
game.guess("a")        -> Result(hit=False, revealed="_____", guesses_left=7)
game.guess("e")        -> Result(hit=True,  revealed="__e_e", guesses_left=7)
game.won()             -> False
game.lost()            -> False
```

The implementation reduces to: store `(candidates, revealed,
guesses_left)`, call `evil_hangman_reveal` per guess, decrement
`guesses_left` on misses, check win/loss conditions.

## Tier 3: `IndexedEvilHangman` (planned)

Tier 1 is `O(N * L)` per call — scans every candidate. For a 250k-word
dictionary this is fine on modern hardware, but the textbook
optimisation precomputes:

- A `dict[length, set[word]]` index so we only consider words of the
  right length.
- A per-position letter index: `dict[(position, letter), set[word]]`
  for instant set-intersection between candidates and "words with this
  letter at this position."

Pattern computation then becomes set arithmetic rather than per-word
string building. The trade-off: index construction is `O(N * L²)`
upfront, queries become `O(K * L)` where K = current candidate count.
Wins for long games on large dictionaries.

## Tier 4: `DistributedEvilHangman` (HLD)

The system-design follow-up. Single-machine Tier 2 doesn't scale to many
concurrent players or persistent game state. The distributed design:

**Game session storage.** Each in-progress game becomes a row keyed
by `session_id`:

```
session_id : UUID
word_length : int
revealed : str
candidates : list[str]   (or a reference to a saved candidate set)
guesses_remaining : int
guesses_made : list[str]
created_at, updated_at : timestamps
```

Stored in a sharded KV store (Cassandra, DynamoDB, or sharded Redis),
partition key = `session_id`. Sessions are read-modify-write per
guess; concurrent guesses on the same session are serialised by an
optimistic lock (compare-and-swap on a `version` column).

**The dictionary.** Read-mostly; small (a few MB). Replicated on every
game server. Loaded at startup; refreshed on a slow cron.

**API.**

```
POST /games                 → 201 {session_id, revealed}
POST /games/{id}/guesses    → 200 {hit, revealed, guesses_left, status}
GET  /games/{id}            → 200 {revealed, guesses_left, status}
```

**Scaling concerns.**

- *Hot sessions* — a streamer playing publicly might have one
  session with high read traffic. Cache the latest `Result` on a
  CDN edge with TTL ≈ "time between guesses."
- *Cold sessions* — abandoned games. Background job sweeps sessions
  inactive >24h to a cold store.
- *Anti-cheat* — the candidate set is server-only; a leaked endpoint
  must not expose it. Return only `revealed` and `guesses_left`.
- *Determinism for replay* — store every guess + every adversary
  decision (pattern chosen) so a session can be replayed deterministically.

**The core algorithm is unchanged.** Tier 1's pure function is what
each game server still runs for the per-guess decision. The
distribution adds session storage, an API layer, and scaling — not a
new algorithm.

## Running the tests

```sh
uv run pytest Algorithms/evil_hangman/tests/ -q
```

Tier 1 has 10 tests covering: single-group fast path, larger-wins,
tie-on-size, no-match guess, prior-reveal preservation,
multiple-positions-per-word, singleton candidates, three-way
partitions, group/pattern consistency, and revealed letters staying
fixed when guessing a different letter.
