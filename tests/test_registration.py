"""Tests for the explicit registration window (spec #1 story 8, ticket #9).

The TO opens and closes registration explicitly: a Tournament is created in
``setup``, signups happen only while registration is open, and closing it
freezes the roster while Decks are chased — reopenable until the start.
"""

import pytest
from conftest import register_with_deck

from multiverse_bot.engine import EngineError, TournamentEngine


def test_a_created_tournament_refuses_signups_until_registration_opens() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")

    assert engine.tournament(tournament_id).phase == "setup"
    with pytest.raises(EngineError, match="not open"):
        engine.register_player(tournament_id, "alice")


def test_opening_registration_lets_players_sign_up() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")

    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "registration"
    assert tournament.players == ("alice",)


def test_closing_registration_freezes_the_roster_but_not_the_deck_window() -> None:
    """Close finalizes the player count; stragglers still submit Decks and the
    TO can then start (spec #1 stories 7 and 8)."""
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    register_with_deck(engine, tournament_id, "alice")
    engine.register_player(tournament_id, "bob")

    engine.close_registration(tournament_id)

    assert engine.tournament(tournament_id).phase == "registration_closed"
    with pytest.raises(EngineError, match="not open"):
        engine.register_player(tournament_id, "carol")
    engine.submit_deck(tournament_id, "bob", "bob's late decklist")
    # The straggler window is still pre-Reveal: Decks stay Sealed.
    with pytest.raises(EngineError, match="Sealed"):
        engine.deck(tournament_id, "alice", requested_by="bob")
    engine.start_tournament(tournament_id, seed=42)
    assert engine.tournament(tournament_id).phase == "in_progress"


def test_closed_registration_can_reopen_for_late_signups() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")
    engine.close_registration(tournament_id)

    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "bob")

    assert engine.tournament(tournament_id).players == ("alice", "bob")


def test_registration_window_rejects_out_of_phase_transitions() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")

    with pytest.raises(EngineError, match="not open"):
        engine.close_registration(tournament_id)

    engine.open_registration(tournament_id)
    with pytest.raises(EngineError, match="cannot open"):
        engine.open_registration(tournament_id)

    for player in ("alice", "bob"):
        register_with_deck(engine, tournament_id, player)
    engine.start_tournament(tournament_id, seed=42)
    with pytest.raises(EngineError, match="cannot open"):
        engine.open_registration(tournament_id)
    with pytest.raises(EngineError, match="not open"):
        engine.close_registration(tournament_id)


def test_tournaments_query_lists_every_tournament_in_creation_order() -> None:
    """The adapter defaults commands to "the only active Tournament", so the
    facade must expose the full list (spec #1 story 32)."""
    engine = TournamentEngine()
    assert engine.tournaments() == ()

    this_week = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(this_week)
    for player in ("alice", "bob"):
        register_with_deck(engine, this_week, player)
    engine.start_tournament(this_week, seed=42)
    next_week = engine.create_tournament(name="Weekly Riftbound #2")

    listed = engine.tournaments()
    assert [t.tournament_id for t in listed] == [this_week, next_week]
    assert [t.phase for t in listed] == ["in_progress", "setup"]
    assert listed[0] == engine.tournament(this_week)
