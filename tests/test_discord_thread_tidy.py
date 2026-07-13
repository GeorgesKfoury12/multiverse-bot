"""Tests for the end-of-Tournament thread tidy's Discord-side seam (issue #35).

Same shape as the other adapter tests: the archive/lock round-trips stay in
the async handlers, so what is under test is the pure helper that decides
*which* threads a tidy (or a reopened final Round's un-tidy) touches.
"""

from pathlib import Path

from conftest import confirm_round, create_tournament_with_players

from multiverse_bot.bot import round_thread_ids, tournament_thread_ids
from multiverse_bot.engine import TournamentEngine
from multiverse_bot.store import BindingsStore


def thread_store(tmp_path: Path) -> BindingsStore:
    return BindingsStore(tmp_path / "tournaments.db")


def save_round_threads(
    engine: TournamentEngine,
    store: BindingsStore,
    tournament_id: str,
    round_number: int,
    first_thread_id: int,
) -> list[int]:
    """Give every playable Match of the Round a thread, as announcing does."""
    thread_ids = []
    for match in engine.pairings(tournament_id, round_number):
        if match.is_bye:
            continue
        store.save_match_thread(match.match_id, first_thread_id + len(thread_ids))
        thread_ids.append(first_thread_id + len(thread_ids))
    return thread_ids


def test_a_tournaments_threads_collect_across_all_rounds(tmp_path: Path) -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=42)
    store = thread_store(tmp_path)
    round_one = save_round_threads(engine, store, tournament_id, 1, 100)
    confirm_round(engine, tournament_id, round_number=1)
    round_two = save_round_threads(engine, store, tournament_id, 2, 200)

    collected = tournament_thread_ids(
        engine, store.match_thread, engine.tournament(tournament_id)
    )

    assert collected == round_one + round_two


def test_a_completed_tournament_still_collects_every_round(tmp_path: Path) -> None:
    """The natural-completion tidy runs after the final confirmation lands, so
    the collection must work on the completed snapshot."""
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=42)
    store = thread_store(tmp_path)
    expected = save_round_threads(engine, store, tournament_id, 1, 100)
    confirm_round(engine, tournament_id, round_number=1)
    expected += save_round_threads(engine, store, tournament_id, 2, 200)
    confirm_round(engine, tournament_id, round_number=2)

    tournament = engine.tournament(tournament_id)
    assert tournament.phase == "completed"
    assert tournament_thread_ids(engine, store.match_thread, tournament) == expected


def test_byes_and_threadless_matches_are_simply_absent(tmp_path: Path) -> None:
    """A Bye never gets a thread, and a crash can leave a playable Match
    without one; neither blocks tidying the threads that do exist."""
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(
        engine, players=("alice", "bob", "carol")
    )
    engine.start_tournament(tournament_id, seed=42)
    store = thread_store(tmp_path)

    tournament = engine.tournament(tournament_id)
    assert tournament_thread_ids(engine, store.match_thread, tournament) == []

    (threaded,) = save_round_threads(engine, store, tournament_id, 1, 100)
    assert tournament_thread_ids(engine, store.match_thread, tournament) == [threaded]


def test_a_tournament_with_no_rounds_yet_has_no_threads(tmp_path: Path) -> None:
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)
    store = thread_store(tmp_path)

    tournament = engine.tournament(tournament_id)
    assert tournament_thread_ids(engine, store.match_thread, tournament) == []


def test_the_pre_end_snapshot_still_sees_the_voided_rounds_threads(
    tmp_path: Path,
) -> None:
    """An early end voids the current Round — its Pairings leave the engine —
    but its threads are already open on Discord and must be tidied too. The
    end-early handler therefore runs the collection *before*
    ``end_tournament``; this pins that the pre-end collection covers the
    Round the end is about to void."""
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=42)
    store = thread_store(tmp_path)
    expected = save_round_threads(engine, store, tournament_id, 1, 100)
    confirm_round(engine, tournament_id, round_number=1)
    expected += save_round_threads(engine, store, tournament_id, 2, 200)

    collected = tournament_thread_ids(
        engine, store.match_thread, engine.tournament(tournament_id)
    )
    engine.end_tournament(tournament_id)

    assert collected == expected


def test_one_rounds_threads_collect_for_a_reopened_final_round(
    tmp_path: Path,
) -> None:
    """Reopening a completed Tournament directs the TO to correct results in
    the final Round's Match threads, so exactly that Round's threads get
    un-tidied — earlier Rounds stay archived."""
    engine = TournamentEngine()
    tournament_id = create_tournament_with_players(engine)
    engine.start_tournament(tournament_id, seed=42)
    store = thread_store(tmp_path)
    save_round_threads(engine, store, tournament_id, 1, 100)
    confirm_round(engine, tournament_id, round_number=1)
    final_round = save_round_threads(engine, store, tournament_id, 2, 200)
    confirm_round(engine, tournament_id, round_number=2)

    collected = round_thread_ids(engine, store.match_thread, tournament_id, 2)

    assert collected == final_round
