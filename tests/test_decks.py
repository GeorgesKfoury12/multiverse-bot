"""Tests for Sealed Decks: private submission, the start gate, and the Reveal.

Per the spec, tests exercise only the facade: commands in, queries out. Decks
are opaque strings (image reference, text, or link) stored and Revealed
verbatim, unparsed.
"""

import pytest
from conftest import register_with_deck

from multiverse_bot.engine import EngineError, TournamentEngine

DECK = "https://cdn.discordapp.com/attachments/123/456/alice-deck.png"


def test_registered_player_submits_a_deck_and_reads_it_back_verbatim() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    engine.submit_deck(tournament_id, "alice", DECK)

    assert engine.deck(tournament_id, "alice", requested_by="alice") == DECK


def test_resubmitting_replaces_the_deck_and_only_the_latest_counts() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    engine.submit_deck(tournament_id, "alice", "first draft")
    engine.submit_deck(tournament_id, "alice", "final list")

    assert engine.deck(tournament_id, "alice", requested_by="alice") == "final list"


def test_only_registered_players_can_submit_a_deck() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    with pytest.raises(EngineError):
        engine.submit_deck(tournament_id, "mallory", DECK)


def test_sealed_deck_is_hidden_from_everyone_but_its_owner_and_the_to() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")
    engine.register_player(tournament_id, "bob")
    engine.submit_deck(tournament_id, "alice", DECK)

    # A rival, a spectator, even the player's own missing-deck opponent: no one
    # but the owner sees a Sealed Deck through the player query.
    for peeker in ("bob", "spectator"):
        with pytest.raises(EngineError):
            engine.deck(tournament_id, "alice", requested_by=peeker)

    # The TO can view Decks at any time; the caller routes only TOs here.
    assert engine.deck_as_to(tournament_id, "alice") == DECK


def test_start_is_refused_naming_exactly_the_players_missing_a_deck() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol", "dave"):
        engine.register_player(tournament_id, player_id)
    engine.submit_deck(tournament_id, "alice", DECK)
    engine.submit_deck(tournament_id, "carol", DECK)

    with pytest.raises(EngineError) as excinfo:
        engine.start_tournament(tournament_id, seed=42)
    message = str(excinfo.value)
    assert "bob" in message and "dave" in message
    assert "alice" not in message and "carol" not in message
    assert engine.tournament(tournament_id).phase == "registration"

    # Once the stragglers submit, the gate opens. (Chasing is the TO's only
    # remedy today: a Drop needs an in-progress Tournament, so there is no
    # engine action yet to shed a deck-less registrant.)
    engine.submit_deck(tournament_id, "bob", DECK)
    engine.submit_deck(tournament_id, "dave", DECK)
    engine.start_tournament(tournament_id, seed=42)
    assert engine.tournament(tournament_id).phase == "in_progress"


def test_players_missing_decks_names_stragglers_in_registration_order() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob", "carol", "dave"):
        engine.register_player(tournament_id, player_id)
    engine.submit_deck(tournament_id, "carol", DECK)

    assert engine.players_missing_decks(tournament_id) == ("alice", "bob", "dave")

    engine.submit_deck(tournament_id, "alice", DECK)
    engine.submit_deck(tournament_id, "bob", DECK)
    engine.submit_deck(tournament_id, "dave", DECK)
    assert engine.players_missing_decks(tournament_id) == ()


def test_start_reveals_every_deck_to_everyone_at_once() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob"):
        register_with_deck(engine, tournament_id, player_id)

    engine.start_tournament(tournament_id, seed=42)

    # Open decklist: opponents and spectators alike read any Deck.
    for viewer in ("alice", "bob", "spectator"):
        for player_id in ("alice", "bob"):
            assert engine.deck(tournament_id, player_id, requested_by=viewer) == (
                f"{player_id}'s decklist"
            )


def test_decks_are_immutable_once_the_tournament_starts() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob"):
        register_with_deck(engine, tournament_id, player_id)
    engine.start_tournament(tournament_id, seed=42)

    with pytest.raises(EngineError):
        engine.submit_deck(tournament_id, "alice", "sneaky post-reveal edit")
    assert engine.deck(tournament_id, "alice", requested_by="bob") == "alice's decklist"


def test_querying_a_player_who_never_submitted_says_so() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    engine.register_player(tournament_id, "alice")

    with pytest.raises(EngineError):
        engine.deck(tournament_id, "alice", requested_by="alice")
    with pytest.raises(EngineError):
        engine.deck_as_to(tournament_id, "alice")


def test_replaying_the_history_reproduces_deck_state() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("alice", "bob"):
        engine.register_player(tournament_id, player_id)
    engine.submit_deck(tournament_id, "alice", "first draft")
    engine.submit_deck(tournament_id, "alice", "final list")
    engine.submit_deck(tournament_id, "bob", "bob's list")
    engine.start_tournament(tournament_id, seed=42)

    replayed = TournamentEngine.replay(engine.history)

    for player_id, deck in (("alice", "final list"), ("bob", "bob's list")):
        assert replayed.deck(tournament_id, player_id, requested_by="anyone") == deck
