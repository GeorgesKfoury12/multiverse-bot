"""Per-Game ruleset configuration (ADR-0002).

The engine is game-agnostic: house-policy values — match points, Tiebreaker
floor, Bye scoring — live here as fields, keyed by Game in ``RULESETS``; the
Swiss round table is the shared house default, overridable per Game by
subclassing. The field defaults are the ADR-0002 house policy (MTG-derived);
Riftbound uses them unmodified. Adding another TCG means registering another
``Ruleset``, not touching the engine.

Still to come from later tickets: the TO round-count override at start
(ADR-0002 / spec #1 story 15), and how a drawn game counts once results can
express draws (``match_points_draw`` is already policy; GW% draw counting
is not).

Histories record only the Game's name (in ``TournamentCreated``), so replay
resolves the ruleset through ``RULESETS``.
"""

import math
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class Ruleset:
    """One Game's tournament-scoring policy."""

    game: str
    match_points_win: int = 3
    match_points_draw: int = 1
    match_points_loss: int = 0
    # OMW%/OGW% floor each opponent's rate at this value.
    tiebreaker_floor: Fraction = Fraction(1, 3)
    # A Bye scores as a Match win with this game score, excluded from the
    # byed player's own Tiebreakers.
    bye_game_score: tuple[int, int] = (2, 0)

    def swiss_round_count(self, player_count: int) -> int:
        """The standard Swiss round table: ceil(log2 n)."""
        return math.ceil(math.log2(player_count))


RIFTBOUND = Ruleset(game="riftbound")

RULESETS: dict[str, Ruleset] = {RIFTBOUND.game: RIFTBOUND}
