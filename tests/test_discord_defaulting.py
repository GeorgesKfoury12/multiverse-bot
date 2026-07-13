"""Tests for defaulting to "the only Tournament" without naming it (issue #31).

Deck viewing works on completed Tournaments too, so its phase set matches
every Tournament ever finished — left unchecked, one archived Tournament
poisons the no-argument default forever. Defaulting must prefer the unique
live Tournament and fall back to the archive only when nothing is live.
"""

import pytest

from multiverse_bot.bot import _HOLDING_DECKS, CommandError, resolve_tournament
from multiverse_bot.engine import TournamentEngine

from conftest import start_four_player_tournament


def completed_tournament(engine: TournamentEngine) -> str:
    """Start a Tournament and end it early: the shortest path to "completed"."""
    tournament_id = start_four_player_tournament(engine)
    engine.end_tournament(tournament_id)
    return tournament_id


def test_a_finished_tournament_does_not_block_the_live_default() -> None:
    engine = TournamentEngine()
    completed_tournament(engine)
    live = engine.create_tournament(name="Weekly Riftbound #2")
    engine.open_registration(live)

    target = resolve_tournament(engine, None, _HOLDING_DECKS, "holding Decks")

    assert target.tournament_id == live


def test_with_only_archives_a_unique_one_still_defaults() -> None:
    engine = TournamentEngine()
    finished = completed_tournament(engine)

    target = resolve_tournament(engine, None, _HOLDING_DECKS, "holding Decks")

    assert target.tournament_id == finished


def test_several_archives_and_nothing_live_needs_an_explicit_reference() -> None:
    engine = TournamentEngine()
    first = completed_tournament(engine)
    second = completed_tournament(engine)

    with pytest.raises(CommandError) as excinfo:
        resolve_tournament(engine, None, _HOLDING_DECKS, "holding Decks")

    assert first in str(excinfo.value)
    assert second in str(excinfo.value)


def test_two_live_tournaments_are_ambiguous_and_the_error_lists_them() -> None:
    engine = TournamentEngine()
    archived = completed_tournament(engine)
    first_live = start_four_player_tournament(engine)
    second_live = engine.create_tournament(name="Weekly Riftbound #3")
    engine.open_registration(second_live)

    with pytest.raises(CommandError) as excinfo:
        resolve_tournament(engine, None, _HOLDING_DECKS, "holding Decks")

    assert first_live in str(excinfo.value)
    assert second_live in str(excinfo.value)
    assert archived not in str(excinfo.value)


def test_an_explicit_reference_still_reaches_an_archive() -> None:
    engine = TournamentEngine()
    finished = completed_tournament(engine)
    live = engine.create_tournament(name="Weekly Riftbound #2")
    engine.open_registration(live)

    target = resolve_tournament(engine, finished, _HOLDING_DECKS, "holding Decks")

    assert target.tournament_id == finished
