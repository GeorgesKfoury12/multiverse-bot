"""Tests for the tournament-engine facade.

Per the spec, tests exercise only the facade: commands in, Pairings/Standings/
state out. If a test needs to reach past the facade, the facade is missing a
query.
"""

import random
import subprocess
import sys

import pytest
from conftest import (
    PLAYERS,
    confirm_round,
    create_tournament_with_players,
    register_with_deck,
    report_and_confirm,
    report_last_pairable_round,
    start_four_player_tournament,
)

from multiverse_bot.engine import EngineError, TournamentEngine


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
    engine.open_registration(tournament_id)

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


def test_standard_round_count_is_queryable_before_the_start() -> None:
    """The start command warns about a short round-count override *before*
    starting (ticket #12), so the standard count must be readable while the
    Tournament is still startable."""
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)  # 4 players

    assert engine.standard_round_count(tournament_id) == 2  # ceil(log2(4))


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
        report_and_confirm(
            engine,
            tournament_id,
            match,
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
            report_and_confirm(
                engine,
                tournament_id,
                match,
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
        assert match.player_b is not None
        report_and_confirm(
            engine,
            tournament_id,
            match,
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
    engine.open_registration(upcoming_id)
    assert upcoming_id != running_id

    # The same players can register for next week while this week runs.
    engine.register_player(upcoming_id, "alice")
    engine.register_player(upcoming_id, "bob")

    match = engine.pairings(running_id, round_number=1)[0]
    report_and_confirm(
        engine, running_id, match, winner=match.player_a, games_won=2, games_lost=0
    )

    assert engine.tournament(upcoming_id).phase == "registration"
    assert engine.tournament(running_id).phase == "in_progress"
    assert {row.match_points for row in engine.standings(running_id)} == {0, 3}


def test_odd_player_count_starts_with_a_bye_scored_as_a_two_zero_win() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in PLAYERS[:3]:
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    pairings = engine.pairings(tournament_id, round_number=1)
    byes = [match for match in pairings if match.is_bye]
    real = [match for match in pairings if not match.is_bye]
    assert len(byes) == 1 and len(real) == 1

    (bye,) = byes
    assert bye.player_b is None
    assert bye.winner == bye.player_a
    assert (bye.games_won, bye.games_lost) == (2, 0)
    # The byed player holds 3 Match Points before any result comes in.
    standings = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert standings[bye.player_a] == 3
    assert {bye.player_a, real[0].player_a, real[0].player_b} == set(PLAYERS[:3])


def test_round_one_bye_recipient_varies_with_the_seed() -> None:
    recipients = set()
    for seed in range(10):
        engine = TournamentEngine()
        tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
        engine.open_registration(tournament_id)
        for player_id in PLAYERS[:3]:
            register_with_deck(engine, tournament_id, player_id)
        engine.start_tournament(tournament_id, seed=seed)
        (bye,) = [m for m in engine.pairings(tournament_id, round_number=1) if m.is_bye]
        recipients.add(bye.player_a)
    # Everyone is on 0 points, so the Round 1 Bye is random: across seeds it
    # must not stick to one player.
    assert len(recipients) > 1


def test_bye_goes_to_the_lowest_ranked_player_without_a_prior_bye() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in PLAYERS[:3]:
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    round_one = engine.pairings(tournament_id, round_number=1)
    (played,) = [m for m in round_one if not m.is_bye]
    (first_bye,) = [m for m in round_one if m.is_bye]
    report_and_confirm(
        engine, tournament_id, played, winner=played.player_a, games_won=2, games_lost=0
    )

    # Standings: winner 3, round-1 bye 3, loser 0. The loser is the lowest
    # ranked of the two bye-less players, so the round-2 Bye is theirs, and
    # the winner pairs down against the round-1 bye.
    round_two = engine.pairings(tournament_id, round_number=2)
    (second_bye,) = [m for m in round_two if m.is_bye]
    (pair_down,) = [m for m in round_two if not m.is_bye]
    assert second_bye.player_a == played.player_b
    assert {pair_down.player_a, pair_down.player_b} == {
        played.player_a,
        first_bye.player_a,
    }


@pytest.mark.parametrize("player_count", range(3, 11))
def test_property_no_tournament_ever_repeats_an_opponent_or_doubles_a_bye(
    player_count: int,
) -> None:
    for seed in range(25):
        engine = TournamentEngine()
        tournament_id = engine.create_tournament(name=f"prop-{player_count}-{seed}")
        engine.open_registration(tournament_id)
        players = [f"p{i}" for i in range(player_count)]
        for player_id in players:
            register_with_deck(engine, tournament_id, player_id)
        engine.start_tournament(tournament_id, seed=seed)

        results_rng = random.Random(seed)
        met: set[frozenset[str]] = set()
        byes: list[str] = []
        while engine.tournament(tournament_id).phase == "in_progress":
            round_number = engine.tournament(tournament_id).current_round
            assert round_number is not None
            paired_players: list[str] = []
            for match in engine.pairings(tournament_id, round_number):
                if match.is_bye:
                    byes.append(match.player_a)
                    paired_players.append(match.player_a)
                    continue
                assert match.player_b is not None
                pair = frozenset((match.player_a, match.player_b))
                assert pair not in met, f"rematch in round {round_number}: {pair}"
                met.add(pair)
                paired_players.extend(pair)
                winner = results_rng.choice((match.player_a, match.player_b))
                report_and_confirm(
                    engine, tournament_id, match, winner, games_won=2, games_lost=1
                )
            assert sorted(paired_players) == sorted(players)

        if player_count % 2 == 0:
            assert byes == []
        else:
            assert len(byes) == engine.tournament(tournament_id).round_count
            assert len(byes) == len(set(byes)), f"second bye: {byes}"


def test_pairings_show_results_as_they_come_in() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    assert first.winner is None and second.winner is None

    assert first.player_b is not None
    report_and_confirm(
        engine, tournament_id, first, winner=first.player_b, games_won=2, games_lost=1
    )

    first, second = engine.pairings(tournament_id, round_number=1)
    assert first.winner == first.player_b
    assert (first.games_won, first.games_lost) == (2, 1)
    assert second.winner is None


def test_a_bye_match_does_not_accept_reported_results() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in PLAYERS[:3]:
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    (bye,) = [m for m in engine.pairings(tournament_id, round_number=1) if m.is_bye]

    with pytest.raises(EngineError):
        engine.report_result(
            tournament_id,
            bye.match_id,
            reported_by=bye.player_a,
            winner=bye.player_a,
            games_won=2,
            games_lost=0,
        )


def test_report_result_rejects_a_score_the_winner_did_not_win() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    match = engine.pairings(tournament_id, round_number=1)[0]

    for games_won, games_lost in ((1, 2), (1, 1), (-2, 0)):
        with pytest.raises(EngineError):
            engine.report_result(
                tournament_id,
                match.match_id,
                reported_by=match.player_a,
                winner=match.player_a,
                games_won=games_won,
                games_lost=games_lost,
            )


def test_ending_early_freezes_standings_so_far() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    report_and_confirm(
        engine, tournament_id, first, winner=first.player_a, games_won=2, games_lost=0
    )
    # Round 1 is in progress, so the TO force-closes it (Assigned Result for
    # the unfinished Match) and ends at the top of the untouched Round 2.
    engine.assign_result(
        tournament_id,
        second.match_id,
        assigned_by="georges-to",
        winner=second.player_a,
        games_won=2,
        games_lost=0,
    )

    engine.end_tournament(tournament_id)

    assert engine.tournament(tournament_id).phase == "completed"
    assert [row.match_points for row in engine.standings(tournament_id)] == [3, 3, 0, 0]
    with pytest.raises(EngineError):
        engine.report_result(
            tournament_id,
            first.match_id,
            reported_by=first.player_a,
            winner=first.player_a,
            games_won=2,
            games_lost=0,
        )


def test_a_close_that_cannot_pair_the_next_round_completes_the_tournament() -> None:
    """When Drops leave no rematch-free pairing for the next scheduled Round,
    the confirmation that closes the current one completes the Tournament
    with Standings-so-far final, automatically (issue #37). The snapshot
    names the Round that could not be paired so callers can announce why the
    schedule was cut short."""
    engine = TournamentEngine()
    tournament_id, last_pairable = report_last_pairable_round(engine)

    engine.confirm_result(
        tournament_id, last_pairable.match_id, confirmed_by=last_pairable.player_b
    )

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"
    assert not tournament.ended_early
    assert tournament.current_round == 2
    assert tournament.unpairable_round == 3
    # Standings-so-far are final: the double winner leads on 6 points.
    leader = engine.standings(tournament_id)[0]
    assert leader.player_id == last_pairable.player_a
    assert leader.match_points == 6
    # A normal completion carries no unpairable Round.
    replayed = TournamentEngine.replay(engine.history)
    assert replayed.tournament(tournament_id) == tournament


def test_a_force_close_that_cannot_pair_the_next_round_also_completes() -> None:
    """The TO's escape hatch (ADR-0001): force-closing the last pairable Round
    with an Assigned Result completes the Tournament the same way a player
    confirmation does."""
    engine = TournamentEngine()
    tournament_id, last_pairable = report_last_pairable_round(engine)

    engine.assign_result(
        tournament_id,
        last_pairable.match_id,
        assigned_by="georges-to",
        winner=last_pairable.player_b,
        games_won=2,
        games_lost=1,
    )

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"
    assert tournament.unpairable_round == 3


def test_a_completion_on_the_full_schedule_names_no_unpairable_round() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=2)

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"
    assert tournament.unpairable_round is None


def test_reopening_an_unpairable_completion_takes_corrections() -> None:
    """A completion forced by an unpairable Round is still a completion on
    results, so the reopen window applies: the TO can correct the last Round's
    result, and the correction re-completes the Tournament the same way."""
    engine = TournamentEngine()
    tournament_id, last_pairable = report_last_pairable_round(engine)
    engine.confirm_result(
        tournament_id, last_pairable.match_id, confirmed_by=last_pairable.player_b
    )

    engine.reopen_round(tournament_id, reopened_by="georges-to")
    reopened = engine.tournament(tournament_id)
    assert reopened.phase == "in_progress"
    assert reopened.current_round == 2
    assert reopened.unpairable_round is None

    engine.assign_result(
        tournament_id,
        last_pairable.match_id,
        assigned_by="georges-to",
        winner=last_pairable.player_b,
        games_won=2,
        games_lost=0,
    )
    corrected = engine.tournament(tournament_id)
    assert corrected.phase == "completed"
    assert corrected.unpairable_round == 3
    assert engine.standings(tournament_id)[0].player_id == last_pairable.player_b
