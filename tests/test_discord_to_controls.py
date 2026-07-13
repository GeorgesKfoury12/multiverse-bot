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
    mention_names,
    open_match_by_reference,
    reopen_preview,
    require_between_rounds,
    require_previewed_reopen,
    require_previewed_unregister,
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


# -- resolving the Match a TO ruling targets ------------------------------------


def test_an_explicit_match_id_resolves_without_a_thread(tmp_path) -> None:
    """A Match whose thread is missing (never created, or the binding lost)
    must still be rulable, or a force-close could never finish: the TO passes
    the Match ID the walk-through shows instead."""
    from pathlib import Path

    from multiverse_bot.store import BindingsStore

    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    (playable,) = [m for m in engine.pairings(tournament_id, 1) if not m.is_bye]
    store = BindingsStore(Path(tmp_path) / "tournaments.db")  # no threads on file

    tournament, match = open_match_by_reference(
        engine, store, playable.match_id, thread_id=None
    )

    assert tournament.tournament_id == tournament_id
    assert match.match_id == playable.match_id


def test_without_a_match_id_the_thread_resolves_as_before(tmp_path) -> None:
    from pathlib import Path

    from multiverse_bot.store import BindingsStore

    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    (playable,) = [m for m in engine.pairings(tournament_id, 1) if not m.is_bye]
    store = BindingsStore(Path(tmp_path) / "tournaments.db")
    store.save_match_thread(playable.match_id, 555)

    _, match = open_match_by_reference(engine, store, None, thread_id=555)

    assert match.match_id == playable.match_id


def test_neither_match_id_nor_thread_is_refused_with_both_ways_named(
    tmp_path,
) -> None:
    from pathlib import Path

    from multiverse_bot.store import BindingsStore

    engine = TournamentEngine()
    start_tournament(engine)
    store = BindingsStore(Path(tmp_path) / "tournaments.db")

    with pytest.raises(CommandError, match="Match thread.*match"):
        open_match_by_reference(engine, store, None, thread_id=None)


# -- rendering results and the force-close walk-through ------------------------


def _match(**overrides: object) -> Match:
    defaults: dict = {
        "match_id": "T1-R1-M1",
        "round_number": 1,
        "player_a": "alice",
        "player_b": "bob",
    }
    return Match(**{**defaults, **overrides})


NAMES = {"alice": "Alice", "bob": "Bob", "carol": "Carol", "dave": "Dave"}


def test_a_won_result_reads_as_winner_beat_loser() -> None:
    match = _match(winner="bob", games_won=2, games_lost=1, games_drawn=0)

    assert result_phrase(match, NAMES) == "Bob beat Alice 2-1"


def test_a_drawn_result_names_both_players() -> None:
    match = _match(winner=None, games_won=1, games_lost=1, games_drawn=1)

    assert result_phrase(match, NAMES) == "Alice and Bob drew 1-1-1"


def test_a_ping_context_renders_the_result_in_mention_form() -> None:
    """Where the message pings the players (a TO ruling), the mention is in
    the mention data and always renders — so it stays the display form."""
    match = _match(winner="bob", games_won=2, games_lost=1, games_drawn=0)

    phrase = result_phrase(match, mention_names(match.player_a, match.player_b))

    assert phrase == "<@bob> beat <@alice> 2-1"


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

    lines = unfinished_match_lines([unreported, pending], threads.get, NAMES)

    assert lines[0] == "- Alice vs Bob in <#555> — no report yet"
    # No thread on file: the Match ID stands in so the line still says where.
    assert "T1-R1-M2" in lines[1]
    assert "Pending — Carol beat Dave 2-0, per Dave" in lines[1]


def test_a_disputed_match_is_flagged_in_the_walk_through() -> None:
    disputed = _match(
        status="disputed",
        winner="alice",
        games_won=2,
        games_lost=1,
        games_drawn=0,
        reported_by="alice",
    )

    (line,) = unfinished_match_lines([disputed], {"T1-R1-M1": 9}.get, NAMES)

    assert "Disputed — Alice beat Bob 2-1, per Alice" in line


# -- previewing a reopen ---------------------------------------------------------


def test_reopen_preview_offers_the_last_closed_round() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    confirm_round(engine, tournament_id, round_number=1)

    reopened, lines = reopen_preview(engine, engine.tournament(tournament_id))

    assert reopened == 1
    text = "\n".join(lines)
    # The preview says what reverts (Round 2's Pairings) and what comes next
    # (correct, then the Round re-closes itself).
    assert "Round 1" in text and "Round 2" in text
    assert "assign-result" in text


def test_reopen_preview_counts_the_reports_a_revert_discards() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
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

    _, lines = reopen_preview(engine, engine.tournament(tournament_id))

    assert "1 report" in "\n".join(lines)


def test_reopen_preview_refuses_while_no_round_has_closed() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)

    with pytest.raises(CommandError, match="still open"):
        reopen_preview(engine, engine.tournament(tournament_id))


def test_reopen_preview_refuses_once_the_next_round_has_a_confirmed_result() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
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

    with pytest.raises(CommandError, match="confirmed"):
        reopen_preview(engine, engine.tournament(tournament_id))


def test_reopen_preview_uncompletes_a_finished_tournament() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    confirm_round(engine, tournament_id, round_number=1)
    confirm_round(engine, tournament_id, round_number=2)
    assert engine.tournament(tournament_id).phase == "completed"

    reopened, lines = reopen_preview(engine, engine.tournament(tournament_id))

    assert reopened == 2
    assert "final" in "\n".join(lines)


def test_reopen_preview_refuses_an_early_ended_tournament() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine)
    confirm_round(engine, tournament_id, round_number=1)
    engine.end_tournament(tournament_id)

    with pytest.raises(CommandError, match="ended early"):
        reopen_preview(engine, engine.tournament(tournament_id))


def test_a_reopen_click_matching_its_preview_passes() -> None:
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    confirm_round(engine, tournament_id, round_number=1)
    reopened, _ = reopen_preview(engine, engine.tournament(tournament_id))

    require_previewed_reopen(engine, engine.tournament(tournament_id), reopened)


def test_a_reopen_click_is_refused_once_it_would_reopen_a_different_round() -> None:
    """The preview offered 'Reopen Round 1' while the final Round 2 was in
    progress; by the click Round 2's last result confirmed and completed the
    Tournament. ``current_round`` still reads 2, but the same click would now
    un-complete Round 2 — an action the TO never signed off on."""
    engine = TournamentEngine()
    tournament_id = start_tournament(engine, players=("alice", "bob", "carol", "dave"))
    confirm_round(engine, tournament_id, round_number=1)
    reopened, _ = reopen_preview(engine, engine.tournament(tournament_id))
    assert reopened == 1
    confirm_round(engine, tournament_id, round_number=2)
    assert engine.tournament(tournament_id).phase == "completed"

    with pytest.raises(CommandError, match="moved on"):
        require_previewed_reopen(engine, engine.tournament(tournament_id), reopened)


# -- guarding a stale unregister click -------------------------------------------


def _tournament_with_straggler(engine: TournamentEngine) -> str:
    """Two decked players plus 'ghost', registered but deck-less — the
    straggler an unregister preview would target (issue #20)."""
    tournament_id = create_tournament_with_players(engine, players=("alice", "bob"))
    engine.register_player(tournament_id, "ghost")
    return tournament_id


def test_an_unregister_click_matching_its_preview_passes() -> None:
    engine = TournamentEngine()
    tournament_id = _tournament_with_straggler(engine)

    require_previewed_unregister(
        engine, engine.tournament(tournament_id), "ghost", deckless=True
    )


def test_an_unregister_click_is_refused_once_the_deck_situation_changed() -> None:
    """The preview said 'they have no Deck on file'; by the click the
    straggler submitted — the chase succeeded, and firing anyway would
    discard a Deck the TO never signed off on."""
    engine = TournamentEngine()
    tournament_id = _tournament_with_straggler(engine)
    engine.submit_deck(tournament_id, "ghost", "ghost's decklist")

    with pytest.raises(CommandError, match="changed"):
        require_previewed_unregister(
            engine, engine.tournament(tournament_id), "ghost", deckless=True
        )


def test_an_unregister_click_is_refused_once_the_player_already_left() -> None:
    """The player unregistered themselves between the preview and the click;
    there is nobody left to remove."""
    engine = TournamentEngine()
    tournament_id = _tournament_with_straggler(engine)
    engine.unregister_player(tournament_id, "ghost", unregistered_by="ghost")

    with pytest.raises(CommandError, match="no longer"):
        require_previewed_unregister(
            engine, engine.tournament(tournament_id), "ghost", deckless=True
        )


# -- the confirmation button surviving restarts ---------------------------------


@pytest.mark.parametrize(
    ("operation", "argument"),
    [
        ("start", "2"),
        ("drop", "123456789"),
        ("unregister", "123456789"),
        ("forceclose", "3"),
        ("end", "3"),
        ("reopen", "2"),
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
    assert parsed["qualifier"] is None


def test_a_confirm_buttons_qualifier_round_trips_too() -> None:
    """An unregister click must know what Deck situation its preview
    described, so the qualifier rides the custom_id like everything else."""
    button = TOConfirmButton(
        "unregister", "T1", "123456789", label="Do it", qualifier="deckless"
    )

    parsed = re.fullmatch(_TO_CONFIRM_TEMPLATE, button.custom_id)

    assert parsed is not None
    assert parsed["argument"] == "123456789"
    assert parsed["qualifier"] == "deckless"


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
