"""Persistence boundary for game state storage."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from badactors.schemas import Event


class SQLiteEventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(path))
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._create_tables()

    def append(self, event: Event) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT OR IGNORE INTO games (game_id) VALUES (?)",
                (event.game_id,),
            )
            self._connection.execute(
                """
                INSERT INTO events (game_id, event_id, event_json)
                VALUES (?, ?, ?)
                """,
                (event.game_id, event.id, event.model_dump_json()),
            )

    def load(self, game_id: str) -> list[Event]:
        rows = self._connection.execute(
            """
            SELECT event_json
            FROM events
            WHERE game_id = ?
            ORDER BY event_id
            """,
            (game_id,),
        ).fetchall()
        return [Event.model_validate_json(row[0]) for row in rows]

    def close(self) -> None:
        self._connection.close()

    def _create_tables(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    game_id TEXT NOT NULL,
                    event_id INTEGER NOT NULL,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (game_id, event_id),
                    FOREIGN KEY (game_id) REFERENCES games(game_id)
                )
                """
            )
