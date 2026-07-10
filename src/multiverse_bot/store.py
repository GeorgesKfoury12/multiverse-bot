"""SQLite persistence: the engine's action log, plus the Discord wiring.

The engine stays pure (spec #1) — durability lives here, behind one call:
``open_engine`` replays the stored history through a fresh engine (identical
state by construction) and hooks the log in as the engine's action sink, so
every action is committed the moment it is recorded. There is no batch or
flush window: a crash after any confirmed command loses nothing.

The Discord adapter's wiring — per-Tournament channel bindings and per-Match
threads — is not engine state, so it lives in its own tables
(``BindingsStore``) in the same file, and survives restarts the same way.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import get_args

from multiverse_bot.engine import TournamentEngine
from multiverse_bot.engine.actions import Action

# Actions are flat frozen dataclasses of str/int/None fields, so a JSON dict
# keyed by the class name round-trips them exactly.
_ACTION_TYPES: dict[str, type[Action]] = {cls.__name__: cls for cls in get_args(Action)}


class SqliteActionStore:
    """The append-only action log in one SQLite file.

    ``sequence`` preserves the engine's recording order across all
    Tournaments — replay depends on the interleaving, not just per-Tournament
    order (e.g. Tournament IDs are allocated from a shared counter).
    """

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS actions ("
            "  sequence INTEGER PRIMARY KEY,"
            "  type TEXT NOT NULL,"
            "  payload TEXT NOT NULL"
            ")"
        )
        self._connection.commit()

    def append(self, action: Action) -> None:
        """Persist one action, committed before this returns."""
        self._connection.execute(
            "INSERT INTO actions (type, payload) VALUES (?, ?)",
            (type(action).__name__, json.dumps(asdict(action))),
        )
        self._connection.commit()

    def load(self) -> tuple[Action, ...]:
        rows = self._connection.execute(
            "SELECT type, payload FROM actions ORDER BY sequence"
        )
        return tuple(
            _ACTION_TYPES[type_name](**json.loads(payload))
            for type_name, payload in rows
        )


@dataclass(frozen=True)
class ChannelBindings:
    """Where one Tournament's artifacts post: the community's existing purpose
    channels, chosen by the TO at creation (spec #1 story 29)."""

    pairings_channel_id: int
    scores_channel_id: int
    decklists_channel_id: int
    standings_channel_id: int


class BindingsStore:
    """The Discord adapter's durable wiring, next to the action log."""

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS channel_bindings ("
            "  tournament_id TEXT PRIMARY KEY,"
            "  pairings_channel_id INTEGER NOT NULL,"
            "  scores_channel_id INTEGER NOT NULL,"
            "  decklists_channel_id INTEGER NOT NULL,"
            "  standings_channel_id INTEGER NOT NULL"
            ")"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS match_threads ("
            "  match_id TEXT PRIMARY KEY,"
            "  thread_id INTEGER NOT NULL"
            ")"
        )
        self._connection.commit()

    def save_bindings(self, tournament_id: str, bindings: ChannelBindings) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO channel_bindings VALUES (?, ?, ?, ?, ?)",
            (
                tournament_id,
                bindings.pairings_channel_id,
                bindings.scores_channel_id,
                bindings.decklists_channel_id,
                bindings.standings_channel_id,
            ),
        )
        self._connection.commit()

    def bindings(self, tournament_id: str) -> ChannelBindings | None:
        row = self._connection.execute(
            "SELECT pairings_channel_id, scores_channel_id, decklists_channel_id,"
            "  standings_channel_id"
            "  FROM channel_bindings WHERE tournament_id = ?",
            (tournament_id,),
        ).fetchone()
        return ChannelBindings(*row) if row else None

    def save_match_thread(self, match_id: str, thread_id: int) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO match_threads VALUES (?, ?)",
            (match_id, thread_id),
        )
        self._connection.commit()

    def delete_match_thread(self, match_id: str) -> None:
        """Forget a Match's thread — its Round was reopened and the Pairings
        reverted (issue #17), so re-closing must open fresh threads rather
        than reuse ones whose Pairings may have changed."""
        self._connection.execute(
            "DELETE FROM match_threads WHERE match_id = ?",
            (match_id,),
        )
        self._connection.commit()

    def match_thread(self, match_id: str) -> int | None:
        row = self._connection.execute(
            "SELECT thread_id FROM match_threads WHERE match_id = ?",
            (match_id,),
        ).fetchone()
        return row[0] if row else None

    def match_for_thread(self, thread_id: int) -> str | None:
        """The Match hosted in the given thread — how the result flow knows
        what a ``/report`` or button click in a thread is about."""
        row = self._connection.execute(
            "SELECT match_id FROM match_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row[0] if row else None


@dataclass(frozen=True)
class DeckImage:
    """A screenshot Deck's payload, kept verbatim for the Reveal."""

    filename: str
    content: bytes


class DeckImageStore:
    """The bytes behind image Decks, next to the action log.

    The engine's Deck string for an image submission is just a marker; the
    attachment itself is adapter state (Discord CDN links expire, so the bytes
    must survive until the Reveal). One image per (Tournament, player) —
    latest wins, like the Deck itself.
    """

    def __init__(self, path: str | Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS deck_images ("
            "  tournament_id TEXT NOT NULL,"
            "  player_id TEXT NOT NULL,"
            "  filename TEXT NOT NULL,"
            "  content BLOB NOT NULL,"
            "  PRIMARY KEY (tournament_id, player_id)"
            ")"
        )
        self._connection.commit()

    def save_image(self, tournament_id: str, player_id: str, image: DeckImage) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO deck_images VALUES (?, ?, ?, ?)",
            (tournament_id, player_id, image.filename, image.content),
        )
        self._connection.commit()

    def delete_image(self, tournament_id: str, player_id: str) -> None:
        """Forget the stored image — a text resubmission replaced it."""
        self._connection.execute(
            "DELETE FROM deck_images WHERE tournament_id = ? AND player_id = ?",
            (tournament_id, player_id),
        )
        self._connection.commit()

    def image(self, tournament_id: str, player_id: str) -> DeckImage | None:
        row = self._connection.execute(
            "SELECT filename, content FROM deck_images"
            "  WHERE tournament_id = ? AND player_id = ?",
            (tournament_id, player_id),
        ).fetchone()
        return DeckImage(*row) if row else None


def open_engine(path: str | Path) -> TournamentEngine:
    """Open (creating if needed) the action log at ``path`` and return an
    engine rebuilt from it; every action taken through the returned engine is
    persisted as it happens."""
    store = SqliteActionStore(path)
    return TournamentEngine.replay(store.load(), sink=store.append)
