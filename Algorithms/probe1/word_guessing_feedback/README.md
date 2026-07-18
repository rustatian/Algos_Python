# Word Guessing Feedback (Wordle-like)

Given a secret word and an equal-length guess, return per-position
feedback — `match`, `exists`, or `not exists` — handling **repeated
letters** correctly. The scoring engine behind Wordle, Mastermind, and
Bulls and Cows.

Modeled on LeetCode #299 (Bulls and Cows — the exact algorithm) and the
classic "Wordle feedback" question. Related references: #843 (Guess the
Word — Mastermind variant), #383 (Ransom Note — multiset counting), #242
(Valid Anagram).

## Problem

For each position of `guess`, report:

- `MATCH` — right letter, right position.
- `EXISTS` — the letter is in the secret, but at a different position.
- `ABSENT` — the letter is not available.

The whole difficulty is repeated letters. The correct algorithm is **two
passes** over a multiset of the secret's letters:

1. **Pass 1** — mark every exact `MATCH`. Build a `Counter` of the
   secret's letters *excluding* the matched positions (a matched letter is
   spoken for and cannot also satisfy an `EXISTS`).
2. **Pass 2** — for each non-match position, if the guessed letter still
   has a remaining count in the Counter, mark `EXISTS` and decrement;
   otherwise `ABSENT`.

A naive single pass that marks `EXISTS` whenever "the letter appears
somewhere in the secret" over-counts on repeats:

```
secret = "aabb", guess = "aaaa"
correct ->  [MATCH, MATCH, ABSENT, ABSENT]   # only two real a's, both matched
naive   ->  [MATCH, MATCH, EXISTS, EXISTS]   # WRONG — invents a's that aren't there
```

```python
feedback("aabb", "aaaa")   # -> [MATCH, MATCH, ABSENT, ABSENT]
```

## Tiers

| Tier | Class / function | Strategy | The lesson |
|------|------------------|----------|------------|
| 1 | `feedback(secret, guess)` | two-pass over a `Counter` | the algorithm — matches first, then claim-and-decrement for repeats |
| 2 | `WordleGame` | Tier 1 + state | a stateful game: fixed secret, guess budget, win/lose tracking |
| 3 | `CandidateFilter` | the inverse of Tier 1 | constraint propagation — narrow a dictionary by observed clues |
| 4 | `DistributedWordleService` | game-as-a-service | the system-design follow-up — session storage, anti-cheat, scale |

Each tier answers a distinct need. Tier 1 is the pure scoring function —
no state, trivially testable, the piece an interviewer actually asks for.
Tier 2 wraps it in a game: it holds the secret, counts down a guess
budget, and reports `WON` / `LOST` / `PLAYING`. Tier 3 runs the function
*backwards* — a solver that, given the clues seen so far, keeps only the
dictionary words that could still be the secret (a word `w` survives iff
`feedback(w, guess) == observed`, reusing Tier 1 so the solver and the
game can never disagree). Tier 4 makes it a network service.

### Why matches are counted out before the EXISTS pass

An exact match consumes that secret letter, so it must not also be
available to satisfy an `EXISTS` elsewhere. Building the Counter from only
the *non-matched* positions enforces this. Interleaving the passes would
let one secret letter be credited twice — the classic repeated-letter bug.

## Tier 4 — the system-design follow-up (Wordle as a service)

The follow-up: *run this as a multiplayer game service for millions of
concurrent players, without the secret ever leaking and without trusting
the client.*

**Opener questions.** One daily word for everyone (Wordle) or a unique
secret per game (Mastermind)? Guess budget enforced server-side? Anti-
cheat threat model — can a player inspect network traffic or client code?
Leaderboards / sharing? Replayability?

**Design sketch.**

```
   client ─► API (stateless) ─► game session store (KV, sharded by session_id)
                                     │
                              feedback() runs SERVER-SIDE only
                                     │
                              daily-word service (one secret/day, cached on edge)
```

- **The secret never leaves the server.** The client sends a guess; the
  server computes `feedback()` and returns only the per-position verdict.
  This is the entire anti-cheat posture — a client that never receives the
  secret cannot reveal it. (Famously, early Wordle shipped the word list
  *in the client*; the lesson is to keep scoring server-side.)
- **Session state** keyed by `session_id` in a sharded KV store (see this
  repo's `kv_store`): `{secret_id, guesses_made, attempts_left, status}`.
  Guesses are a read-modify-write guarded by an optimistic version (CAS) so
  two in-flight guesses on one session can't both spend the last attempt.
- **Daily word** (Wordle mode) is one secret per UTC day, immutable, cached
  at the edge by date — pure read-mostly data. Per-game mode draws a secret
  per session from a word pool.
- **Idempotency.** A client `request_id` per guess dedupes retries so a
  network retry doesn't consume two attempts.

**Failures.** Duplicate guess submission → idempotency key. Clock skew on
"daily" rollover → define the day in UTC, server-authoritative. Session
store unavailable → fail the guess (do not guess blind); the budget must
stay accurate. Leaderboard write lag → eventual consistency is fine for
rankings, never for the secret or the budget.

| Simulation primitive | Production analogue |
|----------------------|---------------------|
| `feedback()` call | the server-side scoring endpoint (never client-side) |
| `WordleGame` object | one row in the session store, keyed by session_id |
| `attempts_left` field | a CAS-guarded counter column |
| in-process secret | a server-only secret, fetched by id, never returned |
| (single process) | stateless API fleet + sharded session KV + daily-word cache |

## Running the tests

```sh
uv run pytest Algorithms/word_guessing_feedback/tests/ -q
```
