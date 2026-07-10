"""Actions recorded in a tournament's history.

Engine state is a pure function of the ordered action history: replaying the
actions through the engine reproduces identical state. Anything the engine
derives (pairings, standings) is recomputed during replay, so only inputs are
recorded here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class TournamentCreated:
    """Names the Game so replay resolves the same ruleset from ``RULESETS``."""

    tournament_id: str
    name: str
    game: str


@dataclass(frozen=True)
class PlayerRegistered:
    tournament_id: str
    player_id: str


@dataclass(frozen=True)
class TournamentStarted:
    """``round_count`` is the TO's override; None means the ruleset's standard
    Swiss count for the player count."""

    tournament_id: str
    seed: int
    round_count: int | None = None


@dataclass(frozen=True)
class ResultReported:
    """A player reports their Match's result; Pending until confirmed.

    ``winner is None`` is a drawn Match: ``games_won``/``games_lost`` are then
    equal and the score reads e.g. 1-1-1 with ``games_drawn``. Otherwise the
    score is from the winner's side.
    """

    tournament_id: str
    match_id: str
    reported_by: str
    winner: str | None
    games_won: int
    games_lost: int
    games_drawn: int


@dataclass(frozen=True)
class ResultConfirmed:
    """A Pending result becomes the Match's result; by the opponent or the TO."""

    tournament_id: str
    match_id: str
    confirmed_by: str


@dataclass(frozen=True)
class ResultDisputed:
    """The opponent rejects a Pending result, flagging it for TO resolution."""

    tournament_id: str
    match_id: str
    disputed_by: str


@dataclass(frozen=True)
class ResultAssigned:
    """The TO sets the Match's result by fiat: no-shows, Dispute rulings, and
    corrections of confirmed results alike. Counts identically to a reported
    result once set."""

    tournament_id: str
    match_id: str
    assigned_by: str
    winner: str | None
    games_won: int
    games_lost: int
    games_drawn: int


@dataclass(frozen=True)
class PlayerDropped:
    """A player leaves the Tournament for good — self-initiated or by the TO
    (``dropped_by`` records which). They are never paired again; their played
    Matches keep counting. Irreversible and never retroactive."""

    tournament_id: str
    player_id: str
    dropped_by: str


@dataclass(frozen=True)
class TournamentEnded:
    """The TO ends the Tournament early between Rounds; the untouched current
    Round is voided and Standings-so-far become final."""

    tournament_id: str


Action = (
    TournamentCreated
    | PlayerRegistered
    | TournamentStarted
    | ResultReported
    | ResultConfirmed
    | ResultDisputed
    | ResultAssigned
    | PlayerDropped
    | TournamentEnded
)
