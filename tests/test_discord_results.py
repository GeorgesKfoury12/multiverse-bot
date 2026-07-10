"""Tests for the result flow's Discord-side seams (ticket #10).

The adapter stays thin: everything here is a pure helper — reading a score
string, resolving a thread to its open Match, rendering Standings — with the
Discord objects themselves kept out of reach. What a helper returns is exactly
what the handlers send.
"""

from pathlib import Path

import pytest
from conftest import confirm_round, create_tournament_with_players

from multiverse_bot.bot import (
    CommandError,
    format_score,
    open_match_by_id,
    open_match_for_thread,
    parse_score,
    standings_lines,
)
from multiverse_bot.engine import TournamentEngine
from multiverse_bot.store import BindingsStore


def start_tournament(engine: TournamentEngine) -> str:
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=42)
    return tournament_id


# -- reading a reported score ------------------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        ("2-0", (2, 0, 0)),
        ("2-1", (2, 1, 0)),
        ("1-1-1", (1, 1, 1)),
        ("2-0-1", (2, 0, 1)),
    ],
)
def test_a_score_reads_as_wins_losses_draws(
    score: str, expected: tuple[int, int, int]
) -> None:
    assert parse_score(score) == expected


@pytest.mark.parametrize("score", ["", "2", "2-1-0-0", "two-one", "2--1"])
def test_an_unreadable_score_is_refused(score: str) -> None:
    with pytest.raises(CommandError):
        parse_score(score)


def test_a_score_formats_back_with_draws_only_when_present() -> None:
    assert format_score(2, 1, 0) == "2-1"
    assert format_score(1, 1, 1) == "1-1-1"


# -- resolving what a thread or button click is about ------------------------


def test_a_current_round_match_resolves_by_id() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)

    tournament, match = open_match_by_id(engine, first.match_id)

    assert tournament.tournament_id == tournament_id
    assert match.match_id == first.match_id


def test_a_closed_round_match_is_refused_as_frozen() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=1)

    with pytest.raises(CommandError, match="frozen"):
        open_match_by_id(engine, first.match_id)


def test_an_unknown_match_id_is_refused() -> None:
    engine = TournamentEngine()
    start_tournament(engine)

    with pytest.raises(CommandError):
        open_match_by_id(engine, "T9-R9-M9")


def test_a_match_thread_resolves_to_its_open_match(tmp_path: Path) -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    store = BindingsStore(tmp_path / "tournaments.db")
    store.save_match_thread(first.match_id, 555)
    store.save_match_thread(second.match_id, 666)

    tournament, match = open_match_for_thread(engine, store, 666)

    assert tournament.tournament_id == tournament_id
    assert match.match_id == second.match_id


def test_a_thread_that_hosts_no_match_is_refused(tmp_path: Path) -> None:
    engine = TournamentEngine()
    start_tournament(engine)
    store = BindingsStore(tmp_path / "tournaments.db")

    with pytest.raises(CommandError, match="Match thread"):
        open_match_for_thread(engine, store, 555)


# -- rendering Standings ------------------------------------------------------


def test_standings_between_rounds_show_the_round_being_entered() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)

    lines = standings_lines(
        engine.tournament(tournament_id), engine.standings(tournament_id)
    )

    assert "entering Round 2/2" in lines[0]
    # One row per player, ranked: both Round 1 winners on 3 points first.
    assert len(lines) == 1 + 4
    assert lines[1].startswith("1. ")
    assert "**3 pts**" in lines[1]
    assert "**0 pts**" in lines[3]
    # Every row mentions its player and carries the full Tiebreaker stack.
    for row in lines[1:]:
        assert "<@" in row
        assert "OMW" in row and "GW" in row and "OGW" in row


def test_final_standings_crown_the_winner() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=2)

    tournament = engine.tournament(tournament_id)
    standings = engine.standings(tournament_id)
    lines = standings_lines(tournament, standings)

    assert tournament.phase == "completed"
    assert "Final Standings" in lines[0]
    champion = standings[0].player_id
    assert f"<@{champion}>" in lines[-1]
    assert "🏆" in lines[-1]


def test_a_dead_heat_final_names_co_champions() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine, players=("alice", "bob"))
    engine.start_tournament(tournament_id, seed=42)
    (match,) = engine.pairings(tournament_id, round_number=1)
    engine.report_result(
        tournament_id,
        match.match_id,
        reported_by=match.player_a,
        winner=None,
        games_won=1,
        games_lost=1,
        games_drawn=1,
    )
    assert match.player_b is not None
    engine.confirm_result(tournament_id, match.match_id, confirmed_by=match.player_b)

    lines = standings_lines(
        engine.tournament(tournament_id), engine.standings(tournament_id)
    )

    # A 1-1-1 draw leaves the two players identical through the whole stack.
    assert "<@alice>" in lines[-1] and "<@bob>" in lines[-1]


def test_standings_mark_dropped_players() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    quitter = engine.standings(tournament_id)[-1].player_id
    engine.drop_player(tournament_id, quitter, dropped_by=quitter)

    lines = standings_lines(
        engine.tournament(tournament_id), engine.standings(tournament_id)
    )

    dropped_rows = [line for line in lines if "(dropped)" in line]
    assert len(dropped_rows) == 1
    assert f"<@{quitter}>" in dropped_rows[0]
