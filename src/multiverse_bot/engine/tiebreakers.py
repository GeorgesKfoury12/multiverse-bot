"""Tiebreaker math for Standings: OMW%, GW%, OGW% (order applied by caller).

All rates are exact Fractions in [0, 1]. The caller passes one MatchRecord
per completed two-player Match — Byes and unreported Matches never appear
here, so their exclusion from Tiebreakers is structural. OMW% and OGW% floor
each opponent's rate; a player's own rates are never floored. Rates with an
empty denominator (no completed Matches, no opponents) are 0.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping, Sequence


@dataclass(frozen=True)
class MatchRecord:
    """One completed non-Bye Match, seen from one player's side."""

    opponent: str
    match_points: int
    games_won: int
    games_played: int


@dataclass(frozen=True)
class Tiebreakers:
    omw: Fraction
    gw: Fraction
    ogw: Fraction


def compute_tiebreakers(
    records: Mapping[str, Sequence[MatchRecord]],
    *,
    match_points_win: int,
    floor: Fraction,
) -> dict[str, Tiebreakers]:
    match_win = {
        player: _rate(
            sum(r.match_points for r in matches),
            match_points_win * len(matches),
        )
        for player, matches in records.items()
    }
    game_win = {
        player: _rate(
            sum(r.games_won for r in matches),
            sum(r.games_played for r in matches),
        )
        for player, matches in records.items()
    }
    return {
        player: Tiebreakers(
            omw=_opponents_average(matches, match_win, floor),
            gw=game_win[player],
            ogw=_opponents_average(matches, game_win, floor),
        )
        for player, matches in records.items()
    }


def _rate(earned: int, possible: int) -> Fraction:
    return Fraction(earned, possible) if possible else Fraction(0)


def _opponents_average(
    matches: Sequence[MatchRecord],
    rates: Mapping[str, Fraction],
    floor: Fraction,
) -> Fraction:
    if not matches:
        return Fraction(0)
    # Averages per Match, which equals per-opponent only while pairing
    # guarantees no rematches — dedupe by opponent if that ever relaxes.
    floored = [max(rates[r.opponent], floor) for r in matches]
    return sum(floored, Fraction(0)) / len(floored)
