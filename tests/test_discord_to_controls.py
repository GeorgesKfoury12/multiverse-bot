"""Tests for the TO control surface's Discord-side seams (ticket #12).

Same shape as the result-flow tests: the handlers stay thin, so everything
here is a pure helper — which Matches a force-close still needs, whether
end-early is on offer, guarding a stale confirmation click — with the Discord
objects themselves kept out of reach.
"""

import re

import pytest
from conftest import (
    confirm_round,
    create_tournament_with_players,
    report_and_confirm,
)

from multiverse_bot.bot import (
    _TO_CONFIRM_TEMPLATE,
    CommandError,
    TOConfirmButton,
    require_between_rounds,
    require_current_round,
    result_phrase,
    unfinished_match_lines,
    unfinished_matches,
)
from multiverse_bot.engine import Match, TournamentEngine


def start_tournament(
    engine: TournamentEngine, players: tuple[str, ...] = ("alice", "bob", "carol")
) -> str:
    """Three players by default: one playable Match plus a Bye per Round,
    so the Bye's special-casing stays under test."""
    tournament_id = create_tournament_with_players(engine, players=players)
    engine.start_tournament(tournament_id, seed=42)
    return tournament_id


# -- what a force-close still needs -------------------------------------------


def test_unfinished_matches_lists_playable_matches_but_never_the_bye() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)

    unfinished = unfinished_matches(engine, engine.tournament(tournament_id))

    # One playable Match; the Bye comes pre-confirmed and needs nothing.
    assert len(unfinished) == 1
    assert not unfinished[0].is_bye


def test_a_confirmed_result_leaves_the_force_close_list() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    first, second = engine.pairings(tournament_id, round_number=1)
    report_and_confirm(
        engine, tournament_id, first, winner=first.player_a, games_won=2, games_lost=0
    )

    unfinished = unfinished_matches(engine, engine.tournament(tournament_id))

    assert [match.match_id for match in unfinished] == [second.match_id]


def test_a_pending_report_still_counts_as_unfinished() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    (match,) = [m for m in engine.pairings(tournament_id, 1) if not m.is_bye]
    engine.report_result(
        tournament_id,
        match.match_id,
        reported_by=match.player_a,
        winner=match.player_a,
        games_won=2,
        games_lost=0,
    )

    unfinished = unfinished_matches(engine, engine.tournament(tournament_id))

    assert [m.match_id for m in unfinished] == [match.match_id]


# -- when end-early is on offer ------------------------------------------------


def test_an_untouched_round_offers_end_early_despite_its_bye() -> None:
    """The Bye is pre-confirmed the moment a Round begins; that alone must not
    read as the Round being 'in progress' (spec #1: end-early is offered
    between Rounds)."""
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)

    require_between_rounds(engine, engine.tournament(tournament_id))


def test_a_touched_round_directs_the_to_to_force_close_first() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    (match,) = [m for m in engine.pairings(tournament_id, 1) if not m.is_bye]
    engine.report_result(
        tournament_id,
        match.match_id,
        reported_by=match.player_a,
        winner=match.player_a,
        games_won=2,
        games_lost=0,
    )

    with pytest.raises(CommandError, match="force-close"):
        require_between_rounds(engine, engine.tournament(tournament_id))


# -- rendering results and the force-close walk-through ------------------------


def _match(**overrides: object) -> Match:
    defaults: dict = {
        "match_id": "T1-R1-M1",
        "round_number": 1,
        "player_a": "alice",
        "player_b": "bob",
    }
    return Match(**{**defaults, **overrides})


def test_a_won_result_reads_as_winner_beat_loser() -> None:
    match = _match(winner="bob", games_won=2, games_lost=1, games_drawn=0)

    assert result_phrase(match) == "<@bob> beat <@alice> 2-1"


def test_a_drawn_result_names_both_players() -> None:
    match = _match(winner=None, games_won=1, games_lost=1, games_drawn=1)

    assert result_phrase(match) == "<@alice> and <@bob> drew 1-1-1"


def test_walk_through_lines_show_each_matchs_thread_and_where_it_stands() -> None:
    threads = {"T1-R1-M1": 555}
    unreported = _match()
    pending = _match(
        match_id="T1-R1-M2",
        player_a="carol",
        player_b="dave",
        status="pending",
        winner="carol",
        games_won=2,
        games_lost=0,
        games_drawn=0,
        reported_by="dave",
    )

    lines = unfinished_match_lines([unreported, pending], threads.get)

    assert lines[0] == "- <@alice> vs <@bob> in <#555> — no report yet"
    # No thread on file: the Match ID stands in so the line still says where.
    assert "T1-R1-M2" in lines[1]
    assert "Pending — <@carol> beat <@dave> 2-0, per <@dave>" in lines[1]


def test_a_disputed_match_is_flagged_in_the_walk_through() -> None:
    disputed = _match(
        status="disputed",
        winner="alice",
        games_won=2,
        games_lost=1,
        games_drawn=0,
        reported_by="alice",
    )

    (line,) = unfinished_match_lines([disputed], {"T1-R1-M1": 9}.get)

    assert "Disputed — <@alice> beat <@bob> 2-1, per <@alice>" in line


# -- the confirmation button surviving restarts ---------------------------------


@pytest.mark.parametrize(
    ("operation", "argument"),
    [
        ("start", "2"),
        ("drop", "123456789"),
        ("forceclose", "3"),
        ("end", "3"),
    ],
)
def test_a_confirm_button_round_trips_through_its_custom_id(
    operation: str, argument: str
) -> None:
    """Everything the click needs lives in the custom_id, so a confirmation
    posted before a restart still fires — same contract as the confirm/Dispute
    buttons."""
    button = TOConfirmButton(operation, "T1", argument, label="Do it")

    parsed = re.fullmatch(_TO_CONFIRM_TEMPLATE, button.custom_id)

    assert parsed is not None
    assert parsed["operation"] == operation
    assert parsed["tournament_id"] == "T1"
    assert parsed["argument"] == argument


# -- guarding a stale confirmation click ---------------------------------------


def test_a_confirmation_for_the_current_round_passes() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)

    require_current_round(engine.tournament(tournament_id), round_number=1)


def test_a_confirmation_from_a_closed_round_is_refused() -> None:
    """The preview described Round 1; by the click the Round has moved on, so
    the button must not act on a situation the TO never saw."""
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    confirm_round(engine, tournament_id, round_number=1)

    with pytest.raises(CommandError, match="moved on"):
        require_current_round(engine.tournament(tournament_id), round_number=1)
