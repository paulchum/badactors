"""Transactional SQLite storage for games, world snapshots and durable workers."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path

from badactors.schemas import Event, GameState


class ConflictError(RuntimeError):
    """Another writer changed the record or owns the simulation lease."""


class SQLiteEventStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(Path(path).expanduser()) if str(path) != ":memory:" else str(path),
            check_same_thread=False,
            timeout=10,
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.execute("PRAGMA synchronous = FULL")
        self._create_tables()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def append(self, event: Event) -> None:
        with self.transaction() as connection:
            self._insert_events(connection, [event])
        self.events_committed([event])

    def events_committed(self, events: Sequence[Event]) -> None:
        """Notification hook; called only after a successful commit."""

    def load(self, game_id: str) -> list[Event]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event_json FROM events WHERE game_id = ? ORDER BY event_id",
                (game_id,),
            ).fetchall()
        return [Event.model_validate_json(row[0]) for row in rows]

    def game_ids(self) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT game_id FROM events GROUP BY game_id ORDER BY MAX(rowid)"
            ).fetchall()
        return [row[0] for row in rows]

    def document(
        self, namespace: str, key: str
    ) -> tuple[int, dict[str, object]] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, body FROM documents WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        if row is None:
            return None
        return row[0], dict(json.loads(row[1]))

    def documents(self, namespace: str) -> list[tuple[str, int, dict[str, object]]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT key, revision, body FROM documents "
                "WHERE namespace = ? ORDER BY key",
                (namespace,),
            ).fetchall()
        return [(row[0], row[1], dict(json.loads(row[2]))) for row in rows]

    def save_document(
        self,
        namespace: str,
        key: str,
        body: dict[str, object],
        *,
        expected_revision: int = 0,
    ) -> int:
        with self.transaction() as connection:
            return self.put_document(
                connection, namespace, key, body, expected_revision
            )

    @staticmethod
    def put_document(
        connection: sqlite3.Connection,
        namespace: str,
        key: str,
        body: dict[str, object],
        expected_revision: int,
    ) -> int:
        encoded = json.dumps(body, sort_keys=True, allow_nan=False)
        if expected_revision == 0:
            try:
                connection.execute(
                    "INSERT INTO documents(namespace, key, revision, body) "
                    "VALUES (?, ?, 1, ?)",
                    (namespace, key, encoded),
                )
            except sqlite3.IntegrityError as exc:
                raise ConflictError("Record already exists.") from exc
        else:
            changed = connection.execute(
                "UPDATE documents SET revision = revision + 1, body = ? "
                "WHERE namespace = ? AND key = ? AND revision = ?",
                (encoded, namespace, key, expected_revision),
            ).rowcount
            if changed != 1:
                raise ConflictError("Record changed; reload before retrying.")
        return expected_revision + 1

    def checkpoint_game(
        self,
        state: GameState,
        transcript: list[str],
        events: Sequence[Event],
        *,
        expected_revision: int,
        cast: dict[str, object],
        owner: str,
    ) -> int:
        with self.transaction() as connection:
            self.require_lease(connection, f"game:{state.game_id}", owner)
            self._insert_events(connection, events)
            revision = self.put_document(
                connection,
                "game",
                state.game_id,
                {
                    "state": state.model_dump(mode="json"),
                    "transcript": transcript,
                    "cast": cast,
                },
                expected_revision,
            )
        self.events_committed(events)
        return revision

    def claim(self, resource: str, owner: str, *, seconds: float = 120) -> None:
        with self.transaction() as connection:
            now = time.time()
            connection.execute(
                "INSERT INTO leases(resource, owner, expires) VALUES (?, ?, ?) "
                "ON CONFLICT(resource) DO UPDATE SET owner=excluded.owner, "
                "expires=excluded.expires WHERE leases.expires <= ? "
                "OR leases.owner = excluded.owner",
                (resource, owner, now + seconds, now),
            )
            self.require_lease(connection, resource, owner)

    @staticmethod
    def require_lease(
        connection: sqlite3.Connection, resource: str, owner: str
    ) -> None:
        row = connection.execute(
            "SELECT owner, expires FROM leases WHERE resource = ?", (resource,)
        ).fetchone()
        if row is None or row[0] != owner or row[1] <= time.time():
            raise ConflictError("Simulation is busy or the worker lease expired.")

    def release(self, resource: str, owner: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM leases WHERE resource = ? AND owner = ?", (resource, owner)
            )

    @asynccontextmanager
    async def lease(self, resource: str) -> AsyncIterator[str]:
        owner = uuid.uuid4().hex
        self.claim(resource, owner)
        heartbeat = asyncio.create_task(self._renew(resource, owner))
        try:
            yield owner
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, ConflictError):
                await heartbeat
            self.release(resource, owner)

    async def _renew(self, resource: str, owner: str) -> None:
        while True:
            await asyncio.sleep(20)
            with self.transaction() as connection:
                self.require_lease(connection, resource, owner)
                connection.execute(
                    "UPDATE leases SET expires = ? WHERE resource = ? AND owner = ?",
                    (time.time() + 120, resource, owner),
                )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def backup(self, destination: str | Path) -> None:
        with self._lock, sqlite3.connect(str(destination)) as target:
            self._connection.backup(target)

    @staticmethod
    def _insert_events(connection: sqlite3.Connection, events: Sequence[Event]) -> None:
        for event in events:
            connection.execute(
                "INSERT OR IGNORE INTO games (game_id) VALUES (?)", (event.game_id,)
            )
            connection.execute(
                "INSERT INTO events (game_id, event_id, event_json) VALUES (?, ?, ?)",
                (event.game_id, event.id, event.model_dump_json()),
            )

    def _create_tables(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS games (
                game_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS events (
                game_id TEXT NOT NULL, event_id INTEGER NOT NULL,
                event_json TEXT NOT NULL, PRIMARY KEY (game_id, event_id),
                FOREIGN KEY (game_id) REFERENCES games(game_id)
            );
            CREATE TABLE IF NOT EXISTS documents (
                namespace TEXT NOT NULL, key TEXT NOT NULL,
                revision INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            CREATE TABLE IF NOT EXISTS leases (
                resource TEXT PRIMARY KEY, owner TEXT NOT NULL, expires REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS world_events (
                world_id TEXT NOT NULL, sequence INTEGER NOT NULL,
                tick INTEGER NOT NULL, body TEXT NOT NULL,
                PRIMARY KEY (world_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS world_memories (
                world_id TEXT NOT NULL, resident_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, tick INTEGER NOT NULL,
                body TEXT NOT NULL,
                PRIMARY KEY (world_id, resident_id, sequence)
            );
            """
        )
