"""Tests for SQLite persistence: action history saved as it happens, replay on load.

Per ticket #8 / spec #1, the save/reload/replay-equality tests live at the
engine facade seam: commands go through an engine opened with ``open_engine``, a restart is
simulated by reopening the same path, and equality is checked through the
facade's queries and history — never by inspecting the database.
"""

from pathlib import Path

import pytest
from conftest import (
    confirm_round,
    create_tournament_with_players,
    register_with_deck,
    report_and_confirm,
    start_four_player_tournament,
)

from multiverse_bot.engine import EngineError, TournamentEngine
from multiverse_bot.store import open_engine

DECK = "https://cdn.discordapp.com/attachments/123/456/alice-deck.png"


def test_registration_phase_state_survives_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")
    engine.register_player(tournament_id, "bob")
    engine.submit_deck(tournament_id, "alice", DECK)

    reloaded = open_engine(db)

    assert reloaded.history == engine.history
    assert reloaded.tournament(tournament_id) == engine.tournament(tournament_id)
    assert reloaded.deck(tournament_id, "alice", requested_by="alice") == DECK


def test_replaying_the_stored_history_yields_identical_state(tmp_path: Path) -> None:
    """The stored history feeds the engine's own replay and comes out equal."""
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")
    engine.submit_deck(tournament_id, "alice", DECK)

    replayed = TournamentEngine.replay(open_engine(db).history)

    assert replayed.history == engine.history
    assert replayed.tournament(tournament_id) == engine.tournament(tournament_id)


def test_restart_mid_round_loses_nothing(tmp_path: Path) -> None:
    """Ticket #8's crash scenario: a Pending result, an open Dispute, and a
    Sealed Deck in a second Tournament all survive, and play just continues."""
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = start_four_player_tournament(engine)
    pending, disputed = engine.pairings(tournament_id, 1)
    engine.report_result(
        tournament_id,
        pending.match_id,
        reported_by=pending.player_a,
        winner=pending.player_a,
        games_won=2,
        games_lost=0,
    )
    engine.report_result(
        tournament_id,
        disputed.match_id,
        reported_by=disputed.player_a,
        winner=disputed.player_a,
        games_won=2,
        games_lost=1,
    )
    engine.dispute_result(
        tournament_id, disputed.match_id, disputed_by=disputed.player_b
    )
    next_week = engine.create_tournament(name="Weekly Riftbound #2")
    engine.open_registration(next_week)
    erins_deck = register_with_deck(engine, next_week, "erin")

    reloaded = open_engine(db)

    assert reloaded.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
    statuses = {m.match_id: m.status for m in reloaded.pairings(tournament_id, 1)}
    assert statuses[pending.match_id] == "pending"
    assert statuses[disputed.match_id] == "disputed"
    # The other Tournament's Deck is still there — and still Sealed.
    assert reloaded.deck(next_week, "erin", requested_by="erin") == erins_deck
    with pytest.raises(EngineError, match="Sealed"):
        reloaded.deck(next_week, "erin", requested_by="alice")
    # Play continues on the reloaded engine, and its actions persist too:
    # resolving both Matches closes Round 1, and a third open sees Round 2.
    reloaded.confirm_result(
        tournament_id, pending.match_id, confirmed_by=pending.player_b
    )
    reloaded.assign_result(
        tournament_id,
        disputed.match_id,
        assigned_by="the-to",
        winner=disputed.player_b,
        games_won=2,
        games_lost=1,
    )
    assert reloaded.tournament(tournament_id).current_round == 2
    assert open_engine(db).tournament(tournament_id).current_round == 2


def test_concurrent_tournaments_persist_and_reload_independently(
    tmp_path: Path,
) -> None:
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    this_week = start_four_player_tournament(engine)
    next_week = create_tournament_with_players(engine, players=("erin", "frank"))
    confirm_round(engine, this_week, 1)

    reloaded = open_engine(db)

    assert reloaded.tournament(this_week) == engine.tournament(this_week)
    assert reloaded.tournament(next_week) == engine.tournament(next_week)
    # Acting on one Tournament leaves the other untouched.
    before = reloaded.tournament(next_week)
    confirm_round(reloaded, this_week, 2)
    assert reloaded.tournament(this_week).phase == "completed"
    assert reloaded.tournament(next_week) == before
    # And the reloaded ID counter never re-issues a live Tournament's ID.
    third = reloaded.create_tournament(name="Weekly Riftbound #3")
    assert third not in (this_week, next_week)


def test_every_action_is_persisted_the_moment_it_happens(tmp_path: Path) -> None:
    """No batch or flush window: the engine is never closed (there is nothing
    to close), yet after each command a fresh open of the same file already
    sees the action — exactly what a crash right after the command would keep."""
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    assert len(open_engine(db).history) == 1
    engine.open_registration(tournament_id)
    assert len(open_engine(db).history) == 2
    engine.register_player(tournament_id, "alice")
    assert len(open_engine(db).history) == 3
    engine.submit_deck(tournament_id, "alice", DECK)
    assert open_engine(db).history == engine.history


def test_a_failed_persist_rolls_the_engine_back_in_step_with_the_log() -> None:
    """If the log cannot take the action, the command fails and the engine
    unwinds it: memory never runs ahead of disk, and the caller can simply
    retry once the disk recovers."""
    persisted: list = []
    failing = False

    def flaky_sink(action) -> None:
        if failing:
            raise OSError("disk full")
        persisted.append(action)

    engine = TournamentEngine(sink=flaky_sink)
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    failing = True
    with pytest.raises(OSError):
        engine.register_player(tournament_id, "bob")

    assert engine.history == tuple(persisted)
    assert engine.tournament(tournament_id).players == ("alice",)

    failing = False
    engine.register_player(tournament_id, "bob")
    assert engine.tournament(tournament_id).players == ("alice", "bob")
    assert engine.history == tuple(persisted)


def test_a_reopened_round_survives_a_restart(tmp_path: Path) -> None:
    """A reopen recorded just before a crash reloads mid-correction: the
    previous Round is open again and the reverted Pairings stay gone."""
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = start_four_player_tournament(engine)
    confirm_round(engine, tournament_id, 1)
    engine.reopen_round(tournament_id, reopened_by="the-to")

    reloaded = open_engine(db)

    assert reloaded.history == engine.history
    assert reloaded.tournament(tournament_id) == engine.tournament(tournament_id)
    assert reloaded.tournament(tournament_id).current_round == 1
    assert reloaded.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
    with pytest.raises(EngineError):
        reloaded.pairings(tournament_id, 2)


def test_full_lifecycle_with_draw_drop_bye_and_early_end_round_trips(
    tmp_path: Path,
) -> None:
    """One history exercising every remaining action type — a drawn Match, a
    Drop, the resulting Bye, a TO round-count override, and an early end —
    reloads into identical Standings."""
    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=7, round_count=3)
    drawn, won = engine.pairings(tournament_id, 1)
    engine.report_result(
        tournament_id,
        drawn.match_id,
        reported_by=drawn.player_a,
        winner=None,
        games_won=1,
        games_lost=1,
        games_drawn=1,
    )
    engine.confirm_result(tournament_id, drawn.match_id, confirmed_by=drawn.player_b)
    # Dropping before Round 1's last confirmation keeps the drop between
    # Rounds: Round 2 pairs the remaining 3 players, granting a Bye.
    engine.drop_player(tournament_id, won.player_b, dropped_by="the-to")
    report_and_confirm(
        engine, tournament_id, won, winner=won.player_a, games_won=2, games_lost=1
    )
    assert any(m.is_bye for m in engine.pairings(tournament_id, 2))
    # The untouched Round 2 lets the TO end early; it is voided as never played.
    engine.end_tournament(tournament_id)

    reloaded = open_engine(db)

    assert reloaded.history == engine.history
    assert reloaded.tournament(tournament_id) == engine.tournament(tournament_id)
    assert reloaded.tournament(tournament_id).phase == "completed"
    assert reloaded.standings(tournament_id) == engine.standings(tournament_id)
    assert reloaded.pairings(tournament_id, 1) == engine.pairings(tournament_id, 1)
