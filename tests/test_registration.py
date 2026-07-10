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


def test_unregistering_removes_the_player_and_their_deck_entirely() -> None:
    """An unregister is not a Drop (issue #20): the player simply leaves the
    sign-up list, Deck and all, as if they never signed up."""
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    register_with_deck(engine, tournament_id, "alice")
    register_with_deck(engine, tournament_id, "bob")

    engine.unregister_player(tournament_id, "bob", unregistered_by="bob")

    tournament = engine.tournament(tournament_id)
    assert tournament.players == ("alice",)
    assert tournament.dropped == ()
    with pytest.raises(EngineError, match="no Deck"):
        engine.deck(tournament_id, "bob", requested_by="bob")


def test_unregistering_a_deckless_straggler_unblocks_the_start() -> None:
    """Issue #20's scenario: registration closed, one registrant vanished
    without a Deck. Unregistering them is the drop half of "chase or drop" —
    the start gate stops counting them and the Tournament can begin."""
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    register_with_deck(engine, tournament_id, "alice")
    register_with_deck(engine, tournament_id, "bob")
    engine.register_player(tournament_id, "ghost")
    engine.close_registration(tournament_id)
    assert engine.players_missing_decks(tournament_id) == ("ghost",)
    with pytest.raises(EngineError, match="ghost"):
        engine.start_tournament(tournament_id, seed=42)

    engine.unregister_player(tournament_id, "ghost", unregistered_by="the-to")

    assert engine.players_missing_decks(tournament_id) == ()
    engine.start_tournament(tournament_id, seed=42)
    assert engine.tournament(tournament_id).players == ("alice", "bob")


def test_unregistering_is_refused_once_the_tournament_has_started() -> None:
    """Leaving a started Tournament is a Drop (permanent, stays in Standings),
    never an unregister — the refusal points at the difference."""
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player in ("alice", "bob"):
        register_with_deck(engine, tournament_id, player)
    engine.start_tournament(tournament_id, seed=42)

    with pytest.raises(EngineError, match="Drop"):
        engine.unregister_player(tournament_id, "alice", unregistered_by="alice")


def test_unregistering_an_unknown_player_is_refused() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    with pytest.raises(EngineError, match="not registered"):
        engine.unregister_player(tournament_id, "bob", unregistered_by="bob")


def test_reregistering_after_an_unregister_starts_fresh() -> None:
    """A change of heart is welcome while signups are open, but nothing
    carries over: the earlier Deck is gone, so the start gate chases them
    again until they resubmit."""
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    register_with_deck(engine, tournament_id, "alice")
    register_with_deck(engine, tournament_id, "bob")
    engine.unregister_player(tournament_id, "bob", unregistered_by="bob")

    engine.register_player(tournament_id, "bob")

    assert engine.tournament(tournament_id).players == ("alice", "bob")
    assert engine.players_missing_decks(tournament_id) == ("bob",)
    with pytest.raises(EngineError, match="no Deck"):
        engine.deck(tournament_id, "bob", requested_by="bob")


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
