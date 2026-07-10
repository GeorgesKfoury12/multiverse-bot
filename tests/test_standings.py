"""Worked-example tests for Standings Tiebreakers (OMW% -> GW% -> OGW%).

Every expected value is hand-computed from the scenario's match results and
asserted as an exact Fraction, so a formula drift of any size fails loudly.
Players are identified by their seats in the actual (seed-determined) Round 1
Pairings, which keeps the arithmetic valid under any pairing shuffle.
"""

from fractions import Fraction

import pytest
from conftest import register_with_deck
from conftest import report_and_confirm as submit

from multiverse_bot.engine import (
    EngineError,
    Match,
    Ruleset,
    RULESETS,
    TournamentEngine,
)


def match_of(
    engine: TournamentEngine, tournament_id: str, round_number: int, player: str
) -> Match:
    (match,) = [
        m
        for m in engine.pairings(tournament_id, round_number)
        if player in (m.player_a, m.player_b)
    ]
    return match


def standings_rows(
    engine: TournamentEngine, tournament_id: str
) -> list[tuple[int, str, int, Fraction, Fraction, Fraction]]:
    return [
        (row.rank, row.player_id, row.match_points, row.omw, row.gw, row.ogw)
        for row in engine.standings(tournament_id)
    ]


def test_worked_example_gw_breaks_a_match_point_and_omw_tie() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol", "dave"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    # Name players by Round 1 seats: A vs B, C vs D.
    match_ab, match_cd = engine.pairings(tournament_id, round_number=1)
    a, b = match_ab.player_a, match_ab.player_b
    c, d = match_cd.player_a, match_cd.player_b
    assert b is not None and d is not None
    submit(engine, tournament_id, match_ab, winner=a, games_won=2, games_lost=1)
    submit(engine, tournament_id, match_cd, winner=c, games_won=2, games_lost=0)

    # Round 2 must pair the winners (A, C) and the losers (B, D).
    match_ac = match_of(engine, tournament_id, 2, a)
    match_bd = match_of(engine, tournament_id, 2, b)
    assert {match_ac.player_a, match_ac.player_b} == {a, c}
    assert {match_bd.player_a, match_bd.player_b} == {b, d}
    submit(engine, tournament_id, match_ac, winner=a, games_won=2, games_lost=1)
    submit(engine, tournament_id, match_bd, winner=b, games_won=2, games_lost=1)

    # Records: A 2-0 (6 pts), B 1-1 (3), C 1-1 (3), D 0-2 (0).
    # MW%:  A 2/2=1,  B 1/2,  C 1/2,  D 0/2=0 (floors to 1/3 as an opponent).
    # GW%:  A (2+2)/(3+3)=2/3   B (1+2)/(3+3)=1/2
    #       C (2+1)/(2+3)=3/5   D (0+1)/(2+3)=1/5 (floors to 1/3 as an opponent).
    # OMW%: A avg(1/2, 1/2)=1/2          B avg(MW(A)=1, MW(D)->1/3)=2/3
    #       C avg(MW(D)->1/3, MW(A)=1)=2/3   D avg(1/2, 1/2)=1/2
    # OGW%: A avg(1/2, 3/5)=11/20        B avg(2/3, GW(D)->1/3)=1/2
    #       C avg(GW(D)->1/3, 2/3)=1/2   D avg(3/5, 1/2)=11/20
    # B and C tie on Match Points AND OMW%; C's 3/5 GW% beats B's 1/2.
    assert standings_rows(engine, tournament_id) == [
        (1, a, 6, Fraction(1, 2), Fraction(2, 3), Fraction(11, 20)),
        (2, c, 3, Fraction(2, 3), Fraction(3, 5), Fraction(1, 2)),
        (3, b, 3, Fraction(2, 3), Fraction(1, 2), Fraction(1, 2)),
        (4, d, 0, Fraction(1, 2), Fraction(1, 5), Fraction(11, 20)),
    ]


def test_worked_example_byes_are_excluded_from_the_byed_players_tiebreakers() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    # Round 1: A beats B 2-0; C has the Bye.
    round_one = engine.pairings(tournament_id, round_number=1)
    (played,) = [m for m in round_one if not m.is_bye]
    (round_one_bye,) = [m for m in round_one if m.is_bye]
    a, b, c = played.player_a, played.player_b, round_one_bye.player_a
    assert b is not None
    submit(engine, tournament_id, played, winner=a, games_won=2, games_lost=0)

    # Round 2: B (lowest-ranked byeless) gets the Bye; C beats A 2-1.
    round_two = engine.pairings(tournament_id, round_number=2)
    (round_two_bye,) = [m for m in round_two if m.is_bye]
    (match_ac,) = [m for m in round_two if not m.is_bye]
    assert round_two_bye.player_a == b
    assert {match_ac.player_a, match_ac.player_b} == {a, c}
    submit(engine, tournament_id, match_ac, winner=c, games_won=2, games_lost=1)

    # Byes count for Match Points (B and C each hold a 3-point Bye win) but
    # their rounds vanish from the byed player's own MW% and GW%:
    # MW%:  A 1/2   B 0/1=0 (bye excluded; would be 1/2 if it counted)   C 1/1=1
    # GW%:  A (2+1)/(2+3)=3/5   B 0/2=0 (would be 1/2 with the bye's 2-0)
    #       C 2/3 (would be 4/5 with the bye's 2-0)
    # OMW%: A avg(MW(B)->1/3, MW(C)=1)=2/3 (3/4 if B's bye counted)
    #       B MW(A)=1/2   C MW(A)=1/2
    # OGW%: A avg(GW(B)->1/3, 2/3)=1/2   B GW(A)=3/5   C GW(A)=3/5
    # A and B tie on 3 Match Points; A's 2/3 OMW% beats B's 1/2.
    assert standings_rows(engine, tournament_id) == [
        (1, c, 6, Fraction(1, 2), Fraction(2, 3), Fraction(3, 5)),
        (2, a, 3, Fraction(2, 3), Fraction(3, 5), Fraction(1, 2)),
        (3, b, 3, Fraction(1, 2), Fraction(0), Fraction(3, 5)),
    ]


def test_players_tied_through_the_whole_stack_share_the_placement() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol", "dave"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    # Every result 2-0: A beats B, C beats D, then A beats C, B beats D.
    match_ab, match_cd = engine.pairings(tournament_id, round_number=1)
    a, b = match_ab.player_a, match_ab.player_b
    c = match_cd.player_a
    submit(engine, tournament_id, match_ab, winner=a, games_won=2, games_lost=0)
    submit(engine, tournament_id, match_cd, winner=c, games_won=2, games_lost=0)
    submit(
        engine,
        tournament_id,
        match_of(engine, tournament_id, 2, a),
        winner=a,
        games_won=2,
        games_lost=0,
    )
    submit(
        engine,
        tournament_id,
        match_of(engine, tournament_id, 2, b),
        winner=b,
        games_won=2,
        games_lost=0,
    )

    # B and C are 1-1 with identical Tiebreakers by symmetry:
    # OMW% avg(1, 1/3)=2/3, GW% 2/4=1/2, OGW% avg(1, 1/3)=2/3.
    standings = engine.standings(tournament_id)
    assert [(row.rank, row.match_points) for row in standings] == [
        (1, 6),
        (2, 3),
        (2, 3),
        (4, 0),
    ]
    middle = standings[1:3]
    assert {row.player_id for row in middle} == {b, c}
    for row in middle:
        assert (row.omw, row.gw, row.ogw) == (
            Fraction(2, 3),
            Fraction(1, 2),
            Fraction(2, 3),
        )


def test_create_tournament_rejects_an_unknown_game() -> None:
    engine = TournamentEngine()
    with pytest.raises(EngineError):
        engine.create_tournament(name="Weekly Mystery #1", game="not-a-game")


def test_scoring_comes_from_the_ruleset_config_not_engine_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toy = Ruleset(
        game="toygame",
        match_points_win=4,
        match_points_draw=2,
        match_points_loss=1,
        tiebreaker_floor=Fraction(1, 2),
        bye_game_score=(1, 0),
    )
    monkeypatch.setitem(RULESETS, "toygame", toy)

    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Toy Cup", game="toygame")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    round_one = engine.pairings(tournament_id, round_number=1)
    (played,) = [m for m in round_one if not m.is_bye]
    (bye,) = [m for m in round_one if m.is_bye]
    # The Bye scores with toygame's game score and win points, not Riftbound's.
    assert (bye.games_won, bye.games_lost) == (1, 0)
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[bye.player_a] == 4

    # Round 2 starts on this result and hands its Bye to the Round 1 loser.
    submit(
        engine, tournament_id, played, winner=played.player_a, games_won=2, games_lost=1
    )

    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[bye.player_a] == 4
    assert points[played.player_a] == 4
    # toygame pays the loser 1, plus 4 for the Round 2 Bye.
    assert points[played.player_b] == 5

    # Loser's MW% is 1/4 (1 of 4 possible points), floored to toygame's 1/2
    # inside the winner's OMW%.
    (winner_row,) = [
        row
        for row in engine.standings(tournament_id)
        if row.player_id == played.player_a
    ]
    assert winner_row.omw == Fraction(1, 2)


def test_riftbound_is_the_default_game() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    (bye,) = [m for m in engine.pairings(tournament_id, 1) if m.is_bye]
    assert (bye.games_won, bye.games_lost) == (2, 0)
    standings = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert standings[bye.player_a] == 3
