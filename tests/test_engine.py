"""Tests for the tournament-engine facade.

Per the spec, tests exercise only the facade: commands in, Pairings/Standings/
state out. If a test needs to reach past the facade, the facade is missing a
query.
"""

import subprocess
import sys

import pytest

from multiverse_bot.engine import EngineError, TournamentEngine

PLAYERS = ("alice", "bob", "carol", "dave")


def start_four_player_tournament(engine: TournamentEngine, seed: int = 42) -> str:
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    for player_id in PLAYERS:
        engine.register_player(tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=seed)
    return tournament_id


def test_engine_imports_without_discord_or_database() -> None:
    check = (
        "import sys; import multiverse_bot.engine; "
        "leaked = [m for m in ('discord', 'sqlite3') if m in sys.modules]; "
        "sys.exit(repr(leaked) if leaked else 0)"
    )
    subprocess.run([sys.executable, "-c", check], check=True)


def test_created_tournament_registers_players() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")

    engine.register_player(tournament_id, "alice")
    engine.register_player(tournament_id, "bob")

    tournament = engine.tournament(tournament_id)
    assert tournament.name == "Weekly Riftbound #1"
    assert tournament.phase == "registration"
    assert tournament.players == ("alice", "bob")


def test_start_computes_swiss_round_count_and_pairs_round_one() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "in_progress"
    assert tournament.round_count == 2  # ceil(log2(4))
    assert tournament.current_round == 1

    pairings = engine.pairings(tournament_id, round_number=1)
    assert len(pairings) == 2
    paired = {
        player for match in pairings for player in (match.player_a, match.player_b)
    }
    assert paired == set(PLAYERS)


def test_same_seed_reproduces_identical_pairings() -> None:
    engines = TournamentEngine(), TournamentEngine()
    ids = [start_four_player_tournament(engine, seed=7) for engine in engines]

    first, second = (
        engine.pairings(tid, round_number=1) for engine, tid in zip(engines, ids)
    )
    assert first == second


def test_full_results_update_standings_and_auto_advance_the_round() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)

    round_one = engine.pairings(tournament_id, round_number=1)
    for match in round_one:
        engine.submit_result(
            tournament_id,
            match.match_id,
            winner=match.player_a,
            games_won=2,
            games_lost=0,
        )

    tournament = engine.tournament(tournament_id)
    assert tournament.current_round == 2

    standings = engine.standings(tournament_id)
    assert [(row.rank, row.match_points) for row in standings] == [
        (1, 3),
        (1, 3),
        (3, 0),
        (3, 0),
    ]

    round_two = engine.pairings(tournament_id, round_number=2)
    winners = {match.player_a for match in round_one}
    round_two_pairs = {
        frozenset((match.player_a, match.player_b)) for match in round_two
    }
    # Score groups: the two 3-point players face each other, as do the two on 0.
    # That is also the only rematch-free pairing.
    assert round_two_pairs == {frozenset(winners), frozenset(set(PLAYERS) - winners)}


def test_happy_path_four_players_two_rounds_end_to_end() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)

    for round_number in (1, 2):
        for match in engine.pairings(tournament_id, round_number):
            engine.submit_result(
                tournament_id,
                match.match_id,
                winner=match.player_a,
                games_won=2,
                games_lost=1,
            )

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"

    standings = engine.standings(tournament_id)
    # 2-0, then the round-2 winner of the 0-point group, then the two 1-loss
    # players' complement: exactly one player on 6, two on 3, one on 0.
    assert [row.match_points for row in standings] == [6, 3, 3, 0]
    assert [row.rank for row in standings] == [1, 2, 2, 4]
    winner = standings[0].player_id
    round_two_winners = {
        match.player_a for match in engine.pairings(tournament_id, round_number=2)
    }
    assert winner in round_two_winners


def test_replaying_the_history_reproduces_identical_state() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    for match in engine.pairings(tournament_id, round_number=1):
        engine.submit_result(
            tournament_id,
            match.match_id,
            winner=match.player_b,
            games_won=2,
            games_lost=1,
        )

    replayed = TournamentEngine.replay(engine.history)

    assert replayed.history == engine.history
    assert replayed.tournament(tournament_id) == engine.tournament(tournament_id)
    assert replayed.standings(tournament_id) == engine.standings(tournament_id)
    for round_number in (1, 2):
        assert replayed.pairings(tournament_id, round_number) == engine.pairings(
            tournament_id, round_number
        )


def test_concurrent_tournaments_are_independent() -> None:
    engine = TournamentEngine()
    running_id = start_four_player_tournament(engine)
    upcoming_id = engine.create_tournament(name="Weekly Riftbound #2")
    assert upcoming_id != running_id

    # The same players can register for next week while this week runs.
    engine.register_player(upcoming_id, "alice")
    engine.register_player(upcoming_id, "bob")

    match = engine.pairings(running_id, round_number=1)[0]
    engine.submit_result(
        running_id, match.match_id, winner=match.player_a, games_won=2, games_lost=0
    )

    assert engine.tournament(upcoming_id).phase == "registration"
    assert engine.tournament(running_id).phase == "in_progress"
    assert {row.match_points for row in engine.standings(running_id)} == {0, 3}


def test_start_rejects_odd_player_counts_until_byes_exist() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    for player_id in PLAYERS[:3]:
        engine.register_player(tournament_id, player_id)

    with pytest.raises(EngineError):
        engine.start_tournament(tournament_id, seed=42)


def test_pairings_show_results_as_they_come_in() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    assert first.winner is None and second.winner is None

    engine.submit_result(
        tournament_id, first.match_id, winner=first.player_b, games_won=2, games_lost=1
    )

    first, second = engine.pairings(tournament_id, round_number=1)
    assert first.winner == first.player_b
    assert (first.games_won, first.games_lost) == (2, 1)
    assert second.winner is None


def test_submit_result_rejects_a_score_the_winner_did_not_win() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    match = engine.pairings(tournament_id, round_number=1)[0]

    for games_won, games_lost in ((1, 2), (1, 1), (-2, 0)):
        with pytest.raises(EngineError):
            engine.submit_result(
                tournament_id,
                match.match_id,
                winner=match.player_a,
                games_won=games_won,
                games_lost=games_lost,
            )


def test_ending_early_freezes_standings_so_far() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    engine.submit_result(
        tournament_id, first.match_id, winner=first.player_a, games_won=2, games_lost=0
    )

    engine.end_tournament(tournament_id)

    assert engine.tournament(tournament_id).phase == "completed"
    assert [row.match_points for row in engine.standings(tournament_id)] == [3, 0, 0, 0]
    with pytest.raises(EngineError):
        engine.submit_result(
            tournament_id,
            second.match_id,
            winner=second.player_a,
            games_won=2,
            games_lost=0,
        )
