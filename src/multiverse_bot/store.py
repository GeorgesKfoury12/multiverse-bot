"""SQLite persistence for the tournament engine: an append-only action log.

The engine stays pure (spec #1) — durability lives here, behind one call:
``open_engine`` replays the stored history through a fresh engine (identical
state by construction) and hooks the log in as the engine's action sink, so
every action is committed the moment it is recorded. There is no batch or
flush window: a crash after any confirmed command loses nothing.
"""

import json
import sqlite3
from dataclasses import asdict
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


def open_engine(path: str | Path) -> TournamentEngine:
    """Open (creating if needed) the action log at ``path`` and return an
    engine rebuilt from it; every action taken through the returned engine is
    persisted as it happens."""
    store = SqliteActionStore(path)
    return TournamentEngine.replay(store.load(), sink=store.append)
