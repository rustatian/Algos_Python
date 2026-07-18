"""Evil Hangman — adversarial Hangman, tiered learning port.

Public callables:
    evil_hangman_reveal  — Tier 1: pure function; one adversary decision.

Additional tiers (EvilHangmanGame, IndexedEvilHangman, DistributedEvilHangman)
will land in this namespace as they are added.
"""

from evil_hangman.evil_hangman import evil_hangman_reveal

__all__ = ["evil_hangman_reveal"]
