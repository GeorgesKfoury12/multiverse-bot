"""Independent reference Standings for the golden-fixture tests.

This is the oracle the engine is checked against, standing in for the
external tournament website whose data proved unrecoverable (issue #13). It
is written directly from ADR-0002's house policy and deliberately imports
nothing from ``multiverse_bot`` — its whole value is being a second, separate
derivation of the Swiss math, so keep it that way.

The policy it encodes (ADR-0002, Riftbound ruleset values):
- Match points 3/1/0 for win/draw/loss; a Bye pays the win points.
- MW% = match points / (3 x matches); GW% = games won / games played, a
  drawn game counting as played but not won. Empty denominators rate 0.
- OMW%/OGW% average the opponents' rates over the player's played Matches,
  flooring each opponent at 1/3; a player's own rates are never floored.
- Byes are excluded from every tiebreaker: no opponent, no games, no rate.
- Standings order by Match Points then OMW% -> GW% -> OGW% descending, with
  registration order settling presentation of full ties; players tied
  through the whole stack share the rank (standard competition ranking).
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Sequence

MATCH_POINTS_WIN = 3
MATCH_POINTS_DRAW = 1
MATCH_POINTS_LOSS = 0
TIEBREAKER_FLOOR = Fraction(1, 3)


@dataclass(frozen=True)
class PlayedMatch:
    """One completed Match: a Bye when ``player_b`` is None, a drawn Match
    when ``winner`` is None. The game score reads from the winner's side and
    is symmetric for draws."""

    player_a: str
    player_b: str | None
    winner: str | None
    games_won: int
    games_lost: int
    games_drawn: int = 0


@dataclass(frozen=True)
class ExpectedStanding:
    rank: int
    player_id: str
    match_points: int
    omw: Fraction
    gw: Fraction
    ogw: Fraction


@dataclass(frozen=True)
class _Line:
    """One player's side of a non-Bye Match, as the tiebreakers see it."""

    opponent: str
    match_points: int
    games_won: int
    games_played: int


def compute_standings(
    players: Sequence[str], matches: Sequence[PlayedMatch]
) -> list[ExpectedStanding]:
    """Standings for ``players`` (registration order) over ``matches``."""
    points = {player: 0 for player in players}
    lines: dict[str, list[_Line]] = {player: [] for player in players}
    for match in matches:
        if match.player_b is None:
            points[match.player_a] += MATCH_POINTS_WIN
            continue
        games_played = match.games_won + match.games_lost + match.games_drawn
        for player, opponent in (
            (match.player_a, match.player_b),
            (match.player_b, match.player_a),
        ):
            if match.winner is None:
                earned, games_won = MATCH_POINTS_DRAW, match.games_won
            elif player == match.winner:
                earned, games_won = MATCH_POINTS_WIN, match.games_won
            else:
                earned, games_won = MATCH_POINTS_LOSS, match.games_lost
            points[player] += earned
            lines[player].append(_Line(opponent, earned, games_won, games_played))

    match_win = {
        player: _rate(
            sum(line.match_points for line in played),
            MATCH_POINTS_WIN * len(played),
        )
        for player, played in lines.items()
    }
    game_win = {
        player: _rate(
            sum(line.games_won for line in played),
            sum(line.games_played for line in played),
        )
        for player, played in lines.items()
    }

    def stack(player: str) -> tuple[int, Fraction, Fraction, Fraction]:
        return (
            points[player],
            _opponents_average(lines[player], match_win),
            game_win[player],
            _opponents_average(lines[player], game_win),
        )

    registration_order = {player: index for index, player in enumerate(players)}
    ordered = sorted(
        players,
        key=lambda p: (*(-value for value in stack(p)), registration_order[p]),
    )
    rows: list[ExpectedStanding] = []
    previous = None
    for position, player in enumerate(ordered, start=1):
        current = stack(player)
        rank = rows[-1].rank if current == previous else position
        previous = current
        rows.append(ExpectedStanding(rank, player, *current))
    return rows


def _rate(earned: int, possible: int) -> Fraction:
    return Fraction(earned, possible) if possible else Fraction(0)


def _opponents_average(played: Sequence[_Line], rates: dict[str, Fraction]) -> Fraction:
    if not played:
        return Fraction(0)
    floored = [max(rates[line.opponent], TIEBREAKER_FLOOR) for line in played]
    return sum(floored, Fraction(0)) / len(floored)
