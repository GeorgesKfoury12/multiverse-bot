"""Tests for the result flow: report, confirm/Dispute, TO corrections, freeze.

Per the spec, tests exercise only the facade: commands in, Pairings/Standings/
state/history out. If a test needs to reach past the facade, the facade is
missing a query.
"""

from fractions import Fraction

import pytest
from conftest import PLAYERS, confirm_round, start_four_player_tournament

from multiverse_bot.engine import EngineError, Match, TournamentEngine
from multiverse_bot.engine.actions import (
    ResultAssigned,
    ResultConfirmed,
    ResultDisputed,
    ResultReported,
)


def match_by_id(engine: TournamentEngine, tournament_id: str, match_id: str) -> Match:
    current_round = engine.tournament(tournament_id).current_round
    assert current_round is not None
    for round_number in range(1, current_round + 1):
        for match in engine.pairings(tournament_id, round_number):
            if match.match_id == match_id:
                return match
    raise AssertionError(f"no such match: {match_id}")


def test_either_player_can_report_and_the_result_sits_pending() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)

    # One match reported by player_a, the other by player_b: both accepted.
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.report_result(
        tournament_id,
        second.match_id,
        reported_by=second.player_b,
        winner=second.player_a,
        games_won=2,
        games_lost=1,
    )

    first, second = engine.pairings(tournament_id, round_number=1)
    assert first.status == "pending"
    assert first.winner == first.player_a
    assert (first.games_won, first.games_lost, first.games_drawn) == (2, 0, 0)
    assert first.reported_by == first.player_a
    assert second.status == "pending"
    assert second.reported_by == second.player_b

    # A Pending result scores nothing and does not advance the Round.
    assert engine.tournament(tournament_id).current_round == 1
    assert all(row.match_points == 0 for row in engine.standings(tournament_id))


def test_round_advances_the_moment_all_results_are_confirmed_and_not_before() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    for match in (first, second):
        engine.report_result(
            tournament_id,
            match.match_id,
            reported_by=match.player_a,
            winner=match.player_a,
            games_won=2,
            games_lost=0,
        )

    engine.confirm_result(tournament_id, first.match_id, confirmed_by=first.player_b)

    # One confirmed, one still Pending: the confirmed Match scores, the
    # Round stays open.
    updated_first = match_by_id(engine, tournament_id, first.match_id)
    assert updated_first.status == "confirmed"
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[first.player_a] == 3
    assert points[second.player_a] == 0
    assert engine.tournament(tournament_id).current_round == 1

    engine.confirm_result(tournament_id, second.match_id, confirmed_by=second.player_b)

    assert engine.tournament(tournament_id).current_round == 2


def test_only_the_opponent_can_confirm_a_pending_result() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )

    # Not the reporter, and not a bystander from another Match.
    outsider = next(p for p in PLAYERS if p not in (first.player_a, first.player_b))
    with pytest.raises(EngineError):
        engine.confirm_result(
            tournament_id, first.match_id, confirmed_by=first.player_a
        )
    with pytest.raises(EngineError):
        engine.confirm_result(tournament_id, first.match_id, confirmed_by=outsider)

    assert match_by_id(engine, tournament_id, first.match_id).status == "pending"


def test_a_dispute_flags_the_match_and_holds_the_round_open() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    for match in (first, second):
        engine.report_result(
            tournament_id,
            match.match_id,
            reported_by=match.player_a,
            winner=match.player_a,
            games_won=2,
            games_lost=0,
        )
    engine.confirm_result(tournament_id, second.match_id, confirmed_by=second.player_b)

    engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)

    disputed = match_by_id(engine, tournament_id, first.match_id)
    assert disputed.status == "disputed"
    # A disputed result scores nothing and holds the Round open.
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[first.player_a] == 0
    assert engine.tournament(tournament_id).current_round == 1
    # Only the opponent can dispute, and only while the result is Pending.
    with pytest.raises(EngineError):
        engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)
    with pytest.raises(EngineError):
        engine.dispute_result(
            tournament_id, second.match_id, disputed_by=second.player_a
        )


def test_a_new_report_replaces_a_pending_or_disputed_result() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)

    # After the players sort it out, either can re-report; the fresh report
    # goes back to Pending for the other to confirm.
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_b,
        winner=first.player_a,
        games_won=2,
        games_lost=1,
    )

    replaced = match_by_id(engine, tournament_id, first.match_id)
    assert replaced.status == "pending"
    assert replaced.reported_by == first.player_b
    assert (replaced.games_won, replaced.games_lost) == (2, 1)

    engine.confirm_result(tournament_id, first.match_id, confirmed_by=first.player_a)
    assert match_by_id(engine, tournament_id, first.match_id).status == "confirmed"


def test_to_can_confirm_a_pending_or_disputed_result() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    for match in (first, second):
        engine.report_result(
            tournament_id,
            match.match_id,
            reported_by=match.player_a,
            winner=match.player_a,
            games_won=2,
            games_lost=0,
        )
    engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)

    # The TO resolves the Dispute by confirming the report as-is, and clears
    # the stalled Pending one; the Round then closes.
    engine.confirm_result_as_to(tournament_id, first.match_id, actor="georges-to")
    engine.confirm_result_as_to(tournament_id, second.match_id, actor="georges-to")

    assert match_by_id(engine, tournament_id, first.match_id).status == "confirmed"
    assert engine.tournament(tournament_id).current_round == 2


def test_to_can_assign_a_result_to_an_unreported_match() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)

    # No-show: nothing was reported, the TO assigns the result directly.
    engine.assign_result(
        tournament_id,
        first.match_id,
        assigned_by="georges-to",
        winner=first.player_b,
        games_won=2,
        games_lost=0,
    )

    assigned = match_by_id(engine, tournament_id, first.match_id)
    assert assigned.status == "confirmed"
    assert assigned.winner == first.player_b
    assert assigned.reported_by is None
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[first.player_b] == 3


def test_to_can_correct_a_confirmed_result_until_the_round_closes() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.confirm_result(tournament_id, first.match_id, confirmed_by=first.player_b)

    # Both players misreported; while the other Match keeps the Round open,
    # the TO flips the confirmed result and no stale points remain.
    engine.assign_result(
        tournament_id,
        first.match_id,
        assigned_by="georges-to",
        winner=first.player_b,
        games_won=2,
        games_lost=1,
    )

    corrected = match_by_id(engine, tournament_id, first.match_id)
    assert corrected.winner == first.player_b
    assert (corrected.games_won, corrected.games_lost) == (2, 1)
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[first.player_a] == 0
    assert points[first.player_b] == 3


def test_results_freeze_the_moment_the_round_closes() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=1)
    assert engine.tournament(tournament_id).current_round == 2

    # Round 1 is closed: no report, and no TO correction either.
    with pytest.raises(EngineError):
        engine.report_result(
            tournament_id,
            first.match_id,
            reported_by=first.player_b,
            winner=first.player_b,
            games_won=2,
            games_lost=0,
        )
    with pytest.raises(EngineError):
        engine.assign_result(
            tournament_id,
            first.match_id,
            assigned_by="georges-to",
            winner=first.player_b,
            games_won=2,
            games_lost=0,
        )


def test_the_final_confirmation_completes_the_tournament_and_freezes_it() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=2)

    assert engine.tournament(tournament_id).phase == "completed"
    last = engine.pairings(tournament_id, round_number=2)[0]
    with pytest.raises(EngineError):
        engine.assign_result(
            tournament_id,
            last.match_id,
            assigned_by="georges-to",
            winner=last.player_b,
            games_won=2,
            games_lost=0,
        )


def test_a_drawn_match_scores_a_point_each_and_counts_in_tiebreakers() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)

    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=None,
        games_won=1,
        games_lost=1,
        games_drawn=1,
    )
    engine.confirm_result(tournament_id, first.match_id, confirmed_by=first.player_b)

    drawn = match_by_id(engine, tournament_id, first.match_id)
    assert drawn.status == "confirmed"
    assert drawn.winner is None
    assert (drawn.games_won, drawn.games_lost, drawn.games_drawn) == (1, 1, 1)

    rows = {row.player_id: row for row in engine.standings(tournament_id)}
    assert rows[first.player_a].match_points == 1
    assert rows[first.player_b].match_points == 1
    # GW%: 1 win of 3 games played — the drawn game counts as played, not won.
    assert rows[first.player_a].gw == Fraction(1, 3)


def test_a_report_cannot_exceed_the_best_of_three_game_count() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)

    # 3-0 (a Bo3 ends at two wins), 2-1-1 and 2-0-2 (four games) are all
    # impossible in a best-of-3 Match.
    for games_won, games_lost, games_drawn in ((3, 0, 0), (2, 1, 1), (2, 0, 2)):
        with pytest.raises(EngineError):
            engine.report_result(
                tournament_id,
                first.match_id,
                reported_by=first.player_a,
                winner=first.player_a,
                games_won=games_won,
                games_lost=games_lost,
                games_drawn=games_drawn,
            )


def test_a_draw_report_must_carry_a_drawn_score() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)

    for games_won, games_lost, games_drawn in ((2, 1, 0), (0, 0, 0), (-1, -1, 1)):
        with pytest.raises(EngineError):
            engine.report_result(
                tournament_id,
                first.match_id,
                reported_by=first.player_a,
                winner=None,
                games_won=games_won,
                games_lost=games_lost,
                games_drawn=games_drawn,
            )


def test_a_decisive_score_without_a_winner_says_how_to_fix_it() -> None:
    """Reporting 2-1 with no winner is a fixable mistake, not a riddle: the
    error names the fix — fill the winner field or report a draw (ticket #32)."""
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, _ = engine.pairings(tournament_id, round_number=1)

    with pytest.raises(EngineError, match=r"pick who won in the winner field.*1-1-1"):
        engine.report_result(
            tournament_id,
            first.match_id,
            reported_by=first.player_a,
            winner=None,
            games_won=2,
            games_lost=1,
        )


def test_history_records_who_did_what_in_order() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)

    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)
    engine.assign_result(
        tournament_id,
        first.match_id,
        assigned_by="georges-to",
        winner=first.player_b,
        games_won=2,
        games_lost=1,
    )
    engine.report_result(
        tournament_id,
        second.match_id,
        reported_by=second.player_b,
        winner=second.player_a,
        games_won=2,
        games_lost=1,
    )
    engine.confirm_result(tournament_id, second.match_id, confirmed_by=second.player_a)

    result_actions = [
        action
        for action in engine.history
        if isinstance(
            action, ResultReported | ResultDisputed | ResultAssigned | ResultConfirmed
        )
    ]
    assert [
        (type(action).__name__, getattr(action, "match_id"))
        for action in result_actions
    ] == [
        ("ResultReported", first.match_id),
        ("ResultDisputed", first.match_id),
        ("ResultAssigned", first.match_id),
        ("ResultReported", second.match_id),
        ("ResultConfirmed", second.match_id),
    ]
    assert result_actions[0].reported_by == first.player_a
    assert result_actions[1].disputed_by == first.player_b
    assert result_actions[2].assigned_by == "georges-to"
    assert result_actions[4].confirmed_by == second.player_a


def test_replaying_a_full_result_flow_history_reproduces_identical_state() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    engine.report_result(
        tournament_id,
        first.match_id,
        reported_by=first.player_a,
        winner=first.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.dispute_result(tournament_id, first.match_id, disputed_by=first.player_b)
    engine.assign_result(
        tournament_id,
        first.match_id,
        assigned_by="georges-to",
        winner=None,
        games_won=1,
        games_lost=1,
        games_drawn=1,
    )
    engine.report_result(
        tournament_id,
        second.match_id,
        reported_by=second.player_a,
        winner=second.player_a,
        games_won=2,
        games_lost=1,
    )

    replayed = TournamentEngine.replay(engine.history)

    assert replayed.history == engine.history
    assert replayed.tournament(tournament_id) == engine.tournament(tournament_id)
    assert replayed.standings(tournament_id) == engine.standings(tournament_id)
    assert replayed.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
