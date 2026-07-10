"""Tests for the Discord-side persistence: channel bindings and Match threads.

The engine knows nothing of Discord; the adapter's wiring — which channel each
Tournament artifact posts to, which thread hosts each Match — must survive a
bot restart on its own (ticket #9).
"""

from pathlib import Path

from multiverse_bot.store import BindingsStore, ChannelBindings

BINDINGS = ChannelBindings(
    pairings_channel_id=111,
    scores_channel_id=222,
    decklists_channel_id=333,
    standings_channel_id=444,
)


def test_channel_bindings_survive_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "tournaments.db"
    BindingsStore(db).save_bindings("T1", BINDINGS)

    assert BindingsStore(db).bindings("T1") == BINDINGS


def test_a_tournament_without_bindings_reads_back_none(tmp_path: Path) -> None:
    store = BindingsStore(tmp_path / "tournaments.db")
    assert store.bindings("T99") is None


def test_match_threads_survive_a_restart(tmp_path: Path) -> None:
    db = tmp_path / "tournaments.db"
    store = BindingsStore(db)
    store.save_match_thread("T1-R1-M1", 555)
    store.save_match_thread("T1-R1-M2", 666)

    reloaded = BindingsStore(db)
    assert reloaded.match_thread("T1-R1-M1") == 555
    assert reloaded.match_thread("T1-R1-M2") == 666
    assert reloaded.match_thread("T1-R1-M3") is None


def test_a_thread_resolves_back_to_its_match(tmp_path: Path) -> None:
    """The result flow starts from a Discord thread (ticket #10): the store
    must answer "which Match lives here?", not just the forward direction."""
    store = BindingsStore(tmp_path / "tournaments.db")
    store.save_match_thread("T1-R1-M1", 555)
    store.save_match_thread("T1-R1-M2", 666)

    assert store.match_for_thread(555) == "T1-R1-M1"
    assert store.match_for_thread(666) == "T1-R1-M2"
    assert store.match_for_thread(777) is None


def test_a_deleted_match_thread_is_forgotten(tmp_path: Path) -> None:
    """Reopening a Round reverts its just-paired next Round (issue #17); the
    reverted Matches' threads must be forgotten so the re-close opens fresh
    ones instead of reusing threads whose Pairings may have changed."""
    db = tmp_path / "tournaments.db"
    store = BindingsStore(db)
    store.save_match_thread("T1-R2-M1", 555)
    store.save_match_thread("T1-R2-M2", 666)

    store.delete_match_thread("T1-R2-M1")

    assert store.match_thread("T1-R2-M1") is None
    assert store.match_for_thread(555) is None
    # Other threads — and the deletion itself — survive a restart.
    reloaded = BindingsStore(db)
    assert reloaded.match_thread("T1-R2-M1") is None
    assert reloaded.match_thread("T1-R2-M2") == 666


def test_bindings_share_the_database_file_with_the_action_log(
    tmp_path: Path,
) -> None:
    from multiverse_bot.store import open_engine

    db = tmp_path / "tournaments.db"
    engine = open_engine(db)
    tournament_id = engine.create_tournament(name="Weekly Riftbound #1")
    BindingsStore(db).save_bindings(tournament_id, BINDINGS)

    assert open_engine(db).history == engine.history
    assert BindingsStore(db).bindings(tournament_id) == BINDINGS
