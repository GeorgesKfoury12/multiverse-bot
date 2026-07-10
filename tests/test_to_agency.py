"""Tests for TO agency (ADR-0001): force-close, end early, Drops, round-count
override.

Per the spec, tests exercise only the facade: commands in, Pairings/Standings/
state/history out. If a test needs to reach past the facade, the facade is
missing a query.
"""

from fractions import Fraction

import pytest
from conftest import (
    PLAYERS,
    confirm_round,
    create_tournament_with_players,
    report_and_confirm,
    start_four_player_tournament,
)

from multiverse_bot.engine import EngineError, TournamentEngine


FIVE_PLAYERS = (*PLAYERS, "erin")


def start_five_player_tournament(engine: TournamentEngine, seed: int = 42) -> str:
    tournament_id = create_tournament_with_players(engine, FIVE_PLAYERS)
    engine.start_tournament(tournament_id, seed=seed)
    return tournament_id


def test_assigning_results_to_every_unfinished_match_force_closes_the_round() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    finished, stalled = engine.pairings(tournament_id, round_number=1)
    report_and_confirm(
        engine,
        tournament_id,
        finished,
        winner=finished.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.report_result(
        tournament_id,
        stalled.match_id,
        reported_by=stalled.player_a,
        winner=stalled.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.dispute_result(tournament_id, stalled.match_id, disputed_by=stalled.player_b)
    assert engine.tournament(tournament_id).current_round == 1

    # The one unfinished Match gets an Assigned Result; the Round closes on it.
    engine.assign_result(
        tournament_id,
        stalled.match_id,
        assigned_by="georges-to",
        winner=stalled.player_b,
        games_won=2,
        games_lost=1,
    )

    assert engine.tournament(tournament_id).current_round == 2
    # The Assigned Result counts identically to the reported one.
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[finished.player_a] == points[stalled.player_b] == 3
    assert points[finished.player_b] == points[stalled.player_a] == 0


def test_ending_early_between_rounds_voids_the_untouched_round() -> None:
    engine = TournamentEngine()
    tournament_id = start_five_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    assert engine.tournament(tournament_id).current_round == 2

    # Round 2 is freshly paired and untouched (its Bye comes pre-confirmed,
    # which does not count as the Round being underway).
    engine.end_tournament(tournament_id)

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"
    assert tournament.current_round == 1
    # Round 1: two wins and a Bye scored 3 points each; the voided Round 2
    # contributes nothing — not even its pre-confirmed Bye.
    standings = engine.standings(tournament_id)
    assert sum(row.match_points for row in standings) == 9
    assert len(standings) == 5
    with pytest.raises(EngineError):
        engine.pairings(tournament_id, round_number=2)


def test_ending_early_is_illegal_while_the_round_is_in_progress() -> None:
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

    # A Pending result already counts as the Round being underway; so does a
    # confirmed one while other Matches are unfinished.
    with pytest.raises(EngineError):
        engine.end_tournament(tournament_id)
    engine.confirm_result(tournament_id, first.match_id, confirmed_by=first.player_b)
    with pytest.raises(EngineError):
        engine.end_tournament(tournament_id)

    # Force-closing the Round makes the end legal at the top of Round 2.
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

    # And only a running Tournament can end.
    with pytest.raises(EngineError):
        engine.end_tournament(tournament_id)


def test_a_dropped_player_finishes_their_match_and_is_never_paired_again() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    first, second = engine.pairings(tournament_id, round_number=1)
    quitter = first.player_b
    assert quitter is not None
    before = engine.standings(tournament_id)

    # Dropping mid-Round leaves the current Match to the normal result flow.
    engine.drop_player(tournament_id, quitter, dropped_by=quitter)

    # A Drop is never retroactive: it changes no result, standing, or pairing.
    assert engine.standings(tournament_id) == before
    assert engine.pairings(tournament_id, round_number=1) == (first, second)

    report_and_confirm(
        engine, tournament_id, first, winner=quitter, games_won=2, games_lost=1
    )
    report_and_confirm(
        engine, tournament_id, second, winner=second.player_a, games_won=2, games_lost=0
    )

    # Round 2 pairs the three remaining players (one Bye); the dropped player
    # sits in no Match.
    assert engine.tournament(tournament_id).current_round == 2
    assert engine.tournament(tournament_id).dropped == (quitter,)
    round_two = engine.pairings(tournament_id, round_number=2)
    seated = {m.player_a for m in round_two} | {m.player_b for m in round_two}
    assert quitter not in seated
    assert len([m for m in round_two if m.is_bye]) == 1

    # Their played Match still counts — for them and for their opponent — and
    # they keep their Standings place.
    rows = {row.player_id: row for row in engine.standings(tournament_id)}
    assert rows[quitter].match_points == 3
    assert len(rows) == 4
    # The opponent's OMW% still sees the dropped player's 100% match-win rate.
    assert rows[first.player_a].omw == Fraction(1)


def test_drop_guard_rails() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)

    # No Drops before the Tournament starts (leaving registration is not a Drop).
    with pytest.raises(EngineError):
        engine.drop_player(tournament_id, "alice", dropped_by="alice")

    engine.start_tournament(tournament_id, seed=42)
    engine.drop_player(tournament_id, "alice", dropped_by="alice")

    # Unknown players can't drop; a Drop is irreversible so it can't repeat.
    with pytest.raises(EngineError):
        engine.drop_player(tournament_id, "mallory", dropped_by="georges-to")
    with pytest.raises(EngineError):
        engine.drop_player(tournament_id, "alice", dropped_by="georges-to")

    # The TO can drop an unresponsive player, but never below 2 active ones —
    # that situation calls for ending the Tournament early instead.
    engine.drop_player(tournament_id, "bob", dropped_by="georges-to")
    with pytest.raises(EngineError):
        engine.drop_player(tournament_id, "carol", dropped_by="georges-to")
    assert engine.tournament(tournament_id).dropped == ("alice", "bob")


def test_round_count_override_at_start_sets_the_tournament_length() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)

    # 4 players default to 2 rounds; the TO stretches the week to 3.
    warning = engine.start_tournament(tournament_id, seed=42, round_count=3)

    assert warning is None
    assert engine.tournament(tournament_id).round_count == 3
    for round_number in (1, 2, 3):
        confirm_round(engine, tournament_id, round_number)
    assert engine.tournament(tournament_id).phase == "completed"


def test_a_short_override_warns_that_an_undefeated_winner_is_impossible() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)

    warning = engine.start_tournament(tournament_id, seed=42, round_count=1)

    # 1 round among 4 players leaves two unbeaten players: warned, not blocked
    # (ADR-0001 — the schedule is the TO's call).
    assert warning is not None and "undefeated" in warning
    assert engine.tournament(tournament_id).round_count == 1
    confirm_round(engine, tournament_id, round_number=1)
    assert engine.tournament(tournament_id).phase == "completed"


def test_round_count_override_must_fit_the_player_count() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)

    # 4 players run out of fresh opponents after 3 rounds; 0 rounds is no
    # Tournament at all.
    for round_count in (0, 4):
        with pytest.raises(EngineError):
            engine.start_tournament(tournament_id, seed=42, round_count=round_count)
    assert engine.tournament(tournament_id).phase == "registration"


def test_reopening_reverts_the_next_rounds_pairings() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    assert engine.tournament(tournament_id).current_round == 2

    engine.reopen_round(tournament_id, reopened_by="georges-to")

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "in_progress"
    assert tournament.current_round == 1
    with pytest.raises(EngineError):
        engine.pairings(tournament_id, round_number=2)
    # Round 1's confirmed results are intact — and correctable again.
    statuses = {m.status for m in engine.pairings(tournament_id, round_number=1)}
    assert statuses == {"confirmed"}


def test_a_corrected_result_recloses_the_round_with_fresh_pairings() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    first, second = engine.pairings(tournament_id, round_number=1)
    engine.reopen_round(tournament_id, reopened_by="georges-to")

    # The mistaken confirm flips: player_b actually won the first Match.
    engine.assign_result(
        tournament_id,
        first.match_id,
        assigned_by="georges-to",
        winner=first.player_b,
        games_won=2,
        games_lost=1,
    )

    # The correction re-closes the Round, and Round 2's regenerated Pairings
    # seat the corrected winners together.
    assert engine.tournament(tournament_id).current_round == 2
    points = {
        row.player_id: row.match_points for row in engine.standings(tournament_id)
    }
    assert points[first.player_b] == 3
    assert points[first.player_a] == 0
    (winners_match,) = [
        m
        for m in engine.pairings(tournament_id, round_number=2)
        if first.player_b in (m.player_a, m.player_b)
    ]
    assert {winners_match.player_a, winners_match.player_b} == {
        first.player_b,
        second.player_a,
    }


def test_reopening_the_final_round_uncompletes_the_tournament() -> None:
    """Issue #17's core case: the deciding Match's mistaken confirm completed
    the Tournament in the same instant, leaving no correction window at all."""
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=2)
    assert engine.tournament(tournament_id).phase == "completed"

    engine.reopen_round(tournament_id, reopened_by="georges-to")

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "in_progress"
    assert tournament.current_round == 2
    # The deciding result is correctable again; the correction re-completes.
    leader = engine.standings(tournament_id)[0].player_id
    (decider,) = [
        m
        for m in engine.pairings(tournament_id, round_number=2)
        if leader in (m.player_a, m.player_b)
    ]
    assert decider.player_b is not None
    corrected_winner = (
        decider.player_b if leader == decider.player_a else decider.player_a
    )
    engine.assign_result(
        tournament_id,
        decider.match_id,
        assigned_by="georges-to",
        winner=corrected_winner,
        games_won=2,
        games_lost=1,
    )
    assert engine.tournament(tournament_id).phase == "completed"
    assert engine.standings(tournament_id)[0].player_id == corrected_winner


def test_reopening_is_refused_once_the_next_round_has_a_confirmed_result() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    underway = engine.pairings(tournament_id, round_number=2)[0]
    report_and_confirm(
        engine,
        tournament_id,
        underway,
        winner=underway.player_a,
        games_won=2,
        games_lost=0,
    )

    with pytest.raises(EngineError, match="confirmed"):
        engine.reopen_round(tournament_id, reopened_by="georges-to")


def test_reclosing_with_the_same_results_regenerates_identical_pairings() -> None:
    """The next Round's Bye comes pre-confirmed; it must not block reopening —
    and re-closing on unchanged results reproduces the identical Pairings
    (same seed, same Score Groups), Bye included."""
    engine = TournamentEngine()
    tournament_id = start_five_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    before = engine.pairings(tournament_id, round_number=2)

    engine.reopen_round(tournament_id, reopened_by="georges-to")
    # Re-assign one Round 1 result unchanged: the Round re-closes as it was.
    replayed = [
        m for m in engine.pairings(tournament_id, round_number=1) if not m.is_bye
    ][0]
    engine.assign_result(
        tournament_id,
        replayed.match_id,
        assigned_by="georges-to",
        winner=replayed.winner,
        games_won=2,
        games_lost=0,
    )

    assert engine.tournament(tournament_id).current_round == 2
    assert engine.pairings(tournament_id, round_number=2) == before


def test_reopening_discards_the_next_rounds_pending_reports() -> None:
    """Per the triage decision, only a confirmed result blocks reopening: a
    Pending report in the reverted Round is discarded with its Pairings."""
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    reported = engine.pairings(tournament_id, round_number=2)[0]
    engine.report_result(
        tournament_id,
        reported.match_id,
        reported_by=reported.player_a,
        winner=reported.player_a,
        games_won=2,
        games_lost=0,
    )

    engine.reopen_round(tournament_id, reopened_by="georges-to")
    unchanged = [
        m for m in engine.pairings(tournament_id, round_number=1) if not m.is_bye
    ][0]
    engine.assign_result(
        tournament_id,
        unchanged.match_id,
        assigned_by="georges-to",
        winner=unchanged.winner,
        games_won=2,
        games_lost=0,
    )

    statuses = {m.status for m in engine.pairings(tournament_id, round_number=2)}
    assert statuses == {"awaiting_report"}


def test_reopen_guard_rails() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine, FIVE_PLAYERS)

    # Nothing to reopen before the start.
    with pytest.raises(EngineError):
        engine.reopen_round(tournament_id, reopened_by="georges-to")

    # Round 1 open means no Round has closed yet.
    engine.start_tournament(tournament_id, seed=42)
    with pytest.raises(EngineError, match="closed"):
        engine.reopen_round(tournament_id, reopened_by="georges-to")

    # An early end voids the current Round rather than closing it on results;
    # reopening after one is refused.
    confirm_round(engine, tournament_id, round_number=1)
    engine.end_tournament(tournament_id)
    with pytest.raises(EngineError, match="ended early"):
        engine.reopen_round(tournament_id, reopened_by="georges-to")


def test_replaying_a_history_with_reopens_is_identical() -> None:
    engine = TournamentEngine()
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    engine.reopen_round(tournament_id, reopened_by="georges-to")
    corrected = engine.pairings(tournament_id, round_number=1)[0]
    engine.assign_result(
        tournament_id,
        corrected.match_id,
        assigned_by="georges-to",
        winner=corrected.player_b,
        games_won=2,
        games_lost=0,
    )
    confirm_round(engine, tournament_id, round_number=2)
    engine.reopen_round(tournament_id, reopened_by="georges-to")

    replayed = TournamentEngine.replay(engine.history)

    assert replayed.history == engine.history
    assert replayed.tournament(tournament_id) == engine.tournament(tournament_id)
    assert replayed.standings(tournament_id) == engine.standings(tournament_id)
    assert replayed.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
    assert replayed.pairings(tournament_id, 2) == engine.pairings(tournament_id, 2)


def test_replaying_a_history_with_drop_override_and_early_end_is_identical() -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine, FIVE_PLAYERS)
    engine.start_tournament(tournament_id, seed=7, round_count=3)
    engine.drop_player(tournament_id, "erin", dropped_by="georges-to")
    for match in engine.pairings(tournament_id, round_number=1):
        if not match.is_bye:
            engine.assign_result(
                tournament_id,
                match.match_id,
                assigned_by="georges-to",
                winner=match.player_a,
                games_won=2,
                games_lost=0,
            )
    engine.end_tournament(tournament_id)

    replayed = TournamentEngine.replay(engine.history)

    assert replayed.history == engine.history
    assert replayed.tournament(tournament_id) == engine.tournament(tournament_id)
    assert replayed.standings(tournament_id) == engine.standings(tournament_id)
    assert replayed.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
