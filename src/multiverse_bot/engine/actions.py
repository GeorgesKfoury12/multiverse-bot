"""Actions recorded in a tournament's history.

Engine state is a pure function of the ordered action history: replaying the
actions through the engine reproduces identical state. Anything the engine
derives (pairings, standings) is recomputed during replay, so only inputs are
recorded here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TournamentCreated:
    tournament_id: str
    name: str


@dataclass(frozen=True)
class PlayerRegistered:
    tournament_id: str
    player_id: str


@dataclass(frozen=True)
class TournamentStarted:
    tournament_id: str
    seed: int


@dataclass(frozen=True)
class ResultSubmitted:
    tournament_id: str
    match_id: str
    winner: str
    games_won: int
    games_lost: int


@dataclass(frozen=True)
class TournamentClosed:
    """The TO ends the Tournament early; Standings-so-far become final."""

    tournament_id: str


Action = (
    TournamentCreated
    | PlayerRegistered
    | TournamentStarted
    | ResultSubmitted
    | TournamentClosed
)
