"""Pure tournament engine: no Discord, no database — see ticket #2 / spec #1."""

from multiverse_bot.engine.engine import (
    EngineError,
    Match,
    Standing,
    Tournament,
    TournamentEngine,
)
from multiverse_bot.engine.ruleset import RIFTBOUND, RULESETS, Ruleset

__all__ = [
    "RIFTBOUND",
    "RULESETS",
    "EngineError",
    "Match",
    "Ruleset",
    "Standing",
    "Tournament",
    "TournamentEngine",
]
