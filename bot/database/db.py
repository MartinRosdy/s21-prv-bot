"""Async SQLite repository (raw SQL via aiosqlite)."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import aiosqlite

from bot.database.models import (
    DEFAULT_LANGUAGE,
    REVIEW_EVENT_TYPES,
    ROLE_EVALUATED,
    ROLE_EVALUATOR,
    TrackedEvent,
    User,
)

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    telegram_chat_id INTEGER PRIMARY KEY,
    s21_login        TEXT,
    encrypted_password TEXT,
    language         TEXT    NOT NULL DEFAULT 'ru',
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    s21_event_id  TEXT    NOT NULL,
    type          TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    start_time    TEXT    NOT NULL,
    end_time      TEXT,
    role          TEXT,
    is_notified   INTEGER NOT NULL DEFAULT 0,
    notified_booking_id TEXT,
    data          TEXT    NOT NULL DEFAULT '{}',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(telegram_chat_id)
        ON DELETE CASCADE,
    UNIQUE (user_id, s21_event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_user
    ON events(user_id);

CREATE INDEX IF NOT EXISTS idx_events_status
    ON events(status);

CREATE INDEX IF NOT EXISTS idx_events_user_status
    ON events(user_id, status);
"""

_EVENT_COLUMNS = (
    "id, user_id, s21_event_id, type, status, start_time, end_time, role, "
    "is_notified, notified_booking_id, data"
)


class Database:
    """Thin async wrapper around a single aiosqlite connection."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected. Call connect() first.")
        return self._conn

    async def connect(self) -> None:
        """Open the connection and ensure schema exists."""
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        try:
            self._conn.row_factory = aiosqlite.Row
            # WAL + busy timeout make handler/poller write contention wait
            # instead of failing immediately with "database is locked".
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA synchronous=NORMAL;")
            await self._conn.execute("PRAGMA busy_timeout=5000;")
            await self._conn.execute("PRAGMA foreign_keys = ON;")
            await self._conn.execute("DROP TABLE IF EXISTS known_events_state")
            await self._conn.executescript(_SCHEMA_SQL)
            await self._migrate_schema()
            await self._conn.commit()
        except Exception:
            await self._conn.close()
            self._conn = None
            raise
        logger.info("SQLite ready at %s", self._db_path)

    async def _migrate_schema(self) -> None:
        """Add columns / relax constraints introduced after the initial schema."""
        assert self._conn is not None
        async with self._conn.execute("PRAGMA table_info(events)") as cursor:
            event_columns = {row[1] for row in await cursor.fetchall()}
        if "end_time" not in event_columns:
            await self._conn.execute(
                "ALTER TABLE events ADD COLUMN end_time TEXT"
            )
            logger.info("Migrated events table: added end_time column")
        if "role" not in event_columns:
            await self._conn.execute(
                "ALTER TABLE events ADD COLUMN role TEXT"
            )
            logger.info("Migrated events table: added role column")
        if "is_notified" not in event_columns:
            await self._conn.execute(
                "ALTER TABLE events ADD COLUMN is_notified "
                "INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migrated events table: added is_notified column")
        if "notified_booking_id" not in event_columns:
            await self._conn.execute(
                "ALTER TABLE events ADD COLUMN notified_booking_id TEXT"
            )
            logger.info("Migrated events table: added notified_booking_id column")

        async with self._conn.execute("PRAGMA table_info(users)") as cursor:
            user_info = await cursor.fetchall()
        user_columns = {row[1] for row in user_info}
        if "language" not in user_columns:
            await self._conn.execute(
                "ALTER TABLE users ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"
            )
            logger.info("Migrated users table: added language column")
            async with self._conn.execute("PRAGMA table_info(users)") as cursor:
                user_info = await cursor.fetchall()

        # Allow NULL credentials so /logout can keep language preference.
        # SQLite cannot ALTER NOT NULL → NULL; rebuild the table when needed.
        col_notnull = {row[1]: bool(row[3]) for row in user_info}
        if col_notnull.get("s21_login") or col_notnull.get("encrypted_password"):
            await self._conn.commit()
            await self._conn.execute("PRAGMA foreign_keys = OFF")
            try:
                await self._conn.executescript(
                    """
                DROP TABLE IF EXISTS users_mig;
                CREATE TABLE users_mig (
                    telegram_chat_id INTEGER PRIMARY KEY,
                    s21_login        TEXT,
                    encrypted_password TEXT,
                    language         TEXT    NOT NULL DEFAULT 'ru',
                    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                    updated_at       TEXT    NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO users_mig (
                    telegram_chat_id, s21_login, encrypted_password,
                    language, created_at, updated_at
                )
                SELECT
                    telegram_chat_id, s21_login, encrypted_password,
                    COALESCE(language, 'ru'), created_at, updated_at
                FROM users;
                DROP TABLE users;
                ALTER TABLE users_mig RENAME TO users;
                """
                )
            finally:
                await self._conn.execute("PRAGMA foreign_keys = ON")
            logger.info(
                "Migrated users table: credentials columns are now nullable"
            )

        # School 21 logins are case-insensitive. Normalize legacy rows too so
        # API authentication, storage, and every UI surface use one form.
        cursor = await self._conn.execute(
            """
            UPDATE users
            SET s21_login = LOWER(TRIM(s21_login)),
                updated_at = datetime('now')
            WHERE s21_login IS NOT NULL
              AND s21_login != LOWER(TRIM(s21_login))
            """
        )
        await cursor.close()

    async def __aenter__(self) -> "Database":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("SQLite connection closed")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialize writes and commit or roll back them as one unit."""
        async with self._write_lock:
            connection = self.connection
            try:
                yield connection
            except BaseException:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    @staticmethod
    async def _execute_write(
        connection: aiosqlite.Connection,
        query: str,
        parameters: tuple[Any, ...] = (),
    ) -> int:
        """Execute a write, close its cursor, and return affected row count."""
        cursor = await connection.execute(query, parameters)
        try:
            return cursor.rowcount
        finally:
            await cursor.close()

    # ------------------------------------------------------------------ users

    async def upsert_user(
        self,
        telegram_chat_id: int,
        s21_login: str,
        encrypted_password: str,
    ) -> None:
        """
        Insert or update credentials for a Telegram user.

        New rows get ``language='ru'`` by default. On conflict only login /
        password are updated — language preference is preserved.
        """
        normalized_login = s21_login.strip().lower()
        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                """
                INSERT INTO users (
                    telegram_chat_id, s21_login, encrypted_password, language
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    s21_login = excluded.s21_login,
                    encrypted_password = excluded.encrypted_password,
                    updated_at = datetime('now')
                """,
                (
                    telegram_chat_id,
                    normalized_login,
                    encrypted_password,
                    DEFAULT_LANGUAGE,
                ),
            )

    async def get_user(self, telegram_chat_id: int) -> Optional[User]:
        async with self.connection.execute(
            """
            SELECT telegram_chat_id, s21_login, encrypted_password,
                   language, created_at, updated_at
            FROM users
            WHERE telegram_chat_id = ?
            """,
            (telegram_chat_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_user(row) if row else None

    async def get_all_users(self) -> list[User]:
        """Return only users with active School 21 credentials (for polling)."""
        async with self.connection.execute(
            """
            SELECT telegram_chat_id, s21_login, encrypted_password,
                   language, created_at, updated_at
            FROM users
            WHERE s21_login IS NOT NULL
              AND s21_login != ''
              AND encrypted_password IS NOT NULL
              AND encrypted_password != ''
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_user(r) for r in rows]

    async def update_user_language(
        self,
        telegram_chat_id: int,
        language: str,
    ) -> None:
        """Persist the user's UI language preference."""
        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                """
                UPDATE users
                SET language = ?, updated_at = datetime('now')
                WHERE telegram_chat_id = ?
                """,
                (language, telegram_chat_id),
            )

    async def upsert_user_language(
        self,
        telegram_chat_id: int,
        language: str,
    ) -> None:
        """
        Ensure a users row exists and store the UI language.

        Creates a soft row (NULL credentials) for first-time /start guests.
        """
        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                """
                INSERT INTO users (
                    telegram_chat_id, s21_login, encrypted_password, language
                )
                VALUES (?, NULL, NULL, ?)
                ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    language = excluded.language,
                    updated_at = datetime('now')
                """,
                (telegram_chat_id, language),
            )

    async def clear_user_credentials(self, telegram_chat_id: int) -> bool:
        """
        Soft-logout: clear School 21 credentials, keep the row (and language).

        Also removes tracked events so monitoring stops. Returns True when the
        user had credentials to clear.
        """
        user = await self.get_user(telegram_chat_id)
        if user is None or not user.is_linked:
            return False

        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                "DELETE FROM events WHERE user_id = ?",
                (telegram_chat_id,),
            )
            changed = await self._execute_write(
                connection,
                """
                UPDATE users
                SET s21_login = NULL,
                    encrypted_password = NULL,
                    updated_at = datetime('now')
                WHERE telegram_chat_id = ?
                """,
                (telegram_chat_id,),
            )
        return changed > 0

    async def delete_user(self, telegram_chat_id: int) -> bool:
        """Hard-delete user row (cascade events). Prefer clear_user_credentials."""
        async with self.transaction() as connection:
            changed = await self._execute_write(
                connection,
                "DELETE FROM users WHERE telegram_chat_id = ?",
                (telegram_chat_id,),
            )
        return changed > 0

    # ------------------------------------------------------------------ events

    async def get_events(
        self,
        user_id: int,
        *,
        active_only: bool = False,
    ) -> list[TrackedEvent]:
        """Return all (or only non-terminal) events for a user."""
        if active_only:
            query = (
                f"""
                SELECT {_EVENT_COLUMNS}
                FROM events
                WHERE user_id = ? AND status IN ('OPEN', 'BOOKED')
                """
            )
        else:
            query = (
                f"""
                SELECT {_EVENT_COLUMNS}
                FROM events
                WHERE user_id = ?
                """
            )
        async with self.connection.execute(query, (user_id,)) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_event(r) for r in rows]

    async def get_active_slot_counts(self, user_id: int) -> tuple[int, int]:
        """Return evaluator/evaluated counts shown in the main menu."""
        events = await self.get_events(user_id, active_only=True)
        evaluator_count = 0
        evaluated_count = 0
        for event in events:
            if event.type not in REVIEW_EVENT_TYPES:
                continue
            if event.effective_role == ROLE_EVALUATED:
                evaluated_count += 1
            elif event.effective_role == ROLE_EVALUATOR:
                evaluator_count += 1
        return evaluator_count, evaluated_count

    async def get_event_by_id(self, event_id: int) -> Optional[TrackedEvent]:
        """Fetch a single row by primary key (used by reminder jobs)."""
        async with self.connection.execute(
            f"""
            SELECT {_EVENT_COLUMNS}
            FROM events
            WHERE id = ?
            """,
            (event_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_event(row) if row else None

    async def get_event_by_s21_id(
        self,
        user_id: int,
        s21_event_id: str,
    ) -> Optional[TrackedEvent]:
        async with self.connection.execute(
            f"""
            SELECT {_EVENT_COLUMNS}
            FROM events
            WHERE user_id = ? AND s21_event_id = ?
            """,
            (user_id, s21_event_id),
        ) as cursor:
            row = await cursor.fetchone()
        return self._row_to_event(row) if row else None

    async def upsert_event(self, event: TrackedEvent) -> TrackedEvent:
        """
        Insert or update an event row.

        Returns the row with ``id`` populated (fetched after write).
        """
        data_json = json.dumps(event.data or {}, ensure_ascii=False)
        role = event.role or (event.data or {}).get("role")
        async with self.transaction() as connection:
            async with connection.execute(
                """
                SELECT 1
                FROM users
                WHERE telegram_chat_id = ?
                  AND s21_login IS NOT NULL
                  AND s21_login != ''
                  AND encrypted_password IS NOT NULL
                  AND encrypted_password != ''
                """,
                (event.user_id,),
            ) as cursor:
                linked = await cursor.fetchone()
            if linked is None:
                raise RuntimeError(
                    f"Cannot store event for unlinked user {event.user_id}"
                )
            await self._execute_write(
                connection,
                """
                INSERT INTO events (
                    user_id, s21_event_id, type, status, start_time, end_time,
                    role, is_notified, notified_booking_id, data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, s21_event_id) DO UPDATE SET
                    type = excluded.type,
                    status = excluded.status,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    role = excluded.role,
                    is_notified = MAX(events.is_notified, excluded.is_notified),
                    notified_booking_id = COALESCE(
                        events.notified_booking_id,
                        excluded.notified_booking_id
                    ),
                    data = excluded.data,
                    updated_at = datetime('now')
                """,
                (
                    event.user_id,
                    event.s21_event_id,
                    event.type,
                    event.status,
                    event.start_time,
                    event.end_time,
                    role,
                    int(event.is_notified),
                    event.notified_booking_id,
                    data_json,
                ),
            )

        saved = await self.get_event_by_s21_id(event.user_id, event.s21_event_id)
        if saved is None:
            raise RuntimeError(
                f"Failed to read back event user={event.user_id} "
                f"s21_id={event.s21_event_id}"
            )
        return saved

    async def claim_event_notification(
        self,
        event_id: int,
        booking_id: str,
    ) -> bool:
        """Atomically reserve one notification for a specific booking."""
        async with self.transaction() as connection:
            changed = await self._execute_write(
                connection,
                """
                UPDATE events
                SET is_notified = 1,
                    notified_booking_id = ?,
                    updated_at = datetime('now')
                WHERE id = ?
                  AND (
                      is_notified = 0
                      OR notified_booking_id IS NULL
                      OR notified_booking_id != ?
                  )
                """,
                (booking_id, event_id, booking_id),
            )
        return changed == 1

    async def update_event_status(
        self,
        event_id: int,
        status: str,
        *,
        data: Optional[dict[str, Any]] = None,
        event_type: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
    ) -> None:
        """Patch status (and optionally data / type / start/end time)."""
        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                """
                UPDATE events
                SET status = ?,
                    type = COALESCE(?, type),
                    start_time = COALESCE(?, start_time),
                    end_time = COALESCE(?, end_time),
                    data = COALESCE(?, data),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (
                    status,
                    event_type,
                    start_time,
                    end_time,
                    (
                        json.dumps(data, ensure_ascii=False)
                        if data is not None
                        else None
                    ),
                    event_id,
                ),
            )

    async def delete_event(self, event_id: int) -> None:
        async with self.transaction() as connection:
            await self._execute_write(
                connection,
                "DELETE FROM events WHERE id = ?",
                (event_id,),
            )

    # -------------------------------------------------------------- converters

    @staticmethod
    def _row_to_user(row: aiosqlite.Row) -> User:
        try:
            language = row["language"] or DEFAULT_LANGUAGE
        except (IndexError, KeyError):
            language = DEFAULT_LANGUAGE
        return User(
            telegram_chat_id=row["telegram_chat_id"],
            s21_login=(
                str(row["s21_login"]).strip().lower()
                if row["s21_login"] is not None
                else None
            ),
            encrypted_password=row["encrypted_password"],
            language=str(language),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> TrackedEvent:
        raw_data = row["data"] or "{}"
        try:
            data = json.loads(raw_data) if isinstance(raw_data, str) else dict(raw_data)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        # ``end_time`` / ``role`` may be missing on very old rows before migration.
        try:
            end_time = row["end_time"]
        except (IndexError, KeyError):
            end_time = None
        try:
            role = row["role"]
        except (IndexError, KeyError):
            role = data.get("role")

        return TrackedEvent(
            id=row["id"],
            user_id=row["user_id"],
            s21_event_id=row["s21_event_id"],
            type=row["type"],
            status=row["status"],
            start_time=row["start_time"],
            end_time=end_time,
            data=data,
            role=role,
            is_notified=bool(row["is_notified"]),
            notified_booking_id=row["notified_booking_id"],
        )
