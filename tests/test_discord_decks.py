"""Tests for Sealed Deck submission's Discord-side seams (ticket #11).

The engine stores every Deck as an opaque string; an image Deck's bytes are
adapter state (like Match threads), so they live in the ``DeckImageStore``
next to the action log. The helpers here are pure: what they return is exactly
what the handlers send.
"""

from pathlib import Path

import pytest

from multiverse_bot.bot import (
    CommandError,
    deck_image_marker,
    require_decks,
    resolve_deck_image,
    validate_deck_attachment,
)
from multiverse_bot.engine import TournamentEngine
from multiverse_bot.store import DeckImage, DeckImageStore

PNG_BYTES = b"\x89PNG fake image bytes"


def test_a_deck_image_round_trips_through_the_store(tmp_path: Path) -> None:
    store = DeckImageStore(tmp_path / "tournaments.db")

    store.save_image(
        "T1", "alice", DeckImage(filename="alice-deck.png", content=PNG_BYTES)
    )

    assert store.image("T1", "alice") == DeckImage(
        filename="alice-deck.png", content=PNG_BYTES
    )
    assert store.image("T1", "bob") is None
    assert store.image("T2", "alice") is None


def test_resubmitting_an_image_replaces_the_stored_one(tmp_path: Path) -> None:
    store = DeckImageStore(tmp_path / "tournaments.db")
    store.save_image("T1", "alice", DeckImage("draft.png", b"draft bytes"))

    store.save_image("T1", "alice", DeckImage("final.png", b"final bytes"))

    assert store.image("T1", "alice") == DeckImage("final.png", b"final bytes")


def test_deleting_an_image_clears_it_for_a_text_resubmission(tmp_path: Path) -> None:
    """Resubmitting as text replaces an image Deck, so the stored bytes must
    go too — only the latest submission counts."""
    store = DeckImageStore(tmp_path / "tournaments.db")
    store.save_image("T1", "alice", DeckImage("draft.png", b"draft bytes"))

    store.delete_image("T1", "alice")

    assert store.image("T1", "alice") is None
    store.delete_image("T1", "alice")  # deleting nothing is fine


# -- connecting a Deck string back to its image --------------------------------


def test_an_image_deck_resolves_to_its_stored_image() -> None:
    image = DeckImage("alice-deck.png", PNG_BYTES)

    deck = deck_image_marker(image.filename)

    assert resolve_deck_image(deck, image) == image


def test_a_text_deck_ignores_any_stored_image() -> None:
    """A text resubmission's delete could fail after the engine already
    recorded the new Deck; the leftover bytes must never show as the Deck."""
    stale = DeckImage("old-draft.png", PNG_BYTES)

    assert resolve_deck_image("40 Swamps and a prayer", stale) is None
    assert resolve_deck_image(deck_image_marker("other.png"), stale) is None


def test_an_image_deck_with_no_stored_bytes_resolves_to_nothing() -> None:
    assert resolve_deck_image(deck_image_marker("alice-deck.png"), None) is None


# -- vetting a submitted attachment --------------------------------------------


def test_a_reasonable_screenshot_passes_validation() -> None:
    validate_deck_attachment(content_type="image/png", size=2 * 1024 * 1024)
    validate_deck_attachment(content_type="image/jpeg", size=1)


@pytest.mark.parametrize("content_type", [None, "application/pdf", "text/plain"])
def test_a_non_image_attachment_is_refused(content_type: str | None) -> None:
    with pytest.raises(CommandError, match="image"):
        validate_deck_attachment(content_type=content_type, size=1024)


def test_an_image_too_big_to_repost_is_refused() -> None:
    """The bot re-uploads the image at the Reveal, so anything past Discord's
    default upload limit would seal a Deck that can never be Revealed."""
    with pytest.raises(CommandError, match="8"):
        validate_deck_attachment(content_type="image/png", size=9 * 1024 * 1024)


# -- the start gate, in mentions ------------------------------------------------


def test_start_gate_names_exactly_the_deckless_as_mentions() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("111", "222", "333"):
        engine.register_player(tournament_id, player_id)
    engine.submit_deck(tournament_id, "222", "a fine list")

    with pytest.raises(CommandError) as excinfo:
        require_decks(engine, engine.tournament(tournament_id))

    message = str(excinfo.value)
    assert "<@111>" in message and "<@333>" in message
    assert "222" not in message
    assert "/submit-deck" in message  # the chase message tells them what to do


def test_start_gate_opens_once_every_deck_is_in() -> None:
    engine = TournamentEngine()
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    engine.open_registration(tournament_id)
    for player_id in ("111", "222"):
        engine.register_player(tournament_id, player_id)
        engine.submit_deck(tournament_id, player_id, "a fine list")

    require_decks(engine, engine.tournament(tournament_id))  # no complaint
