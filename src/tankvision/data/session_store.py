"""SQLite persistence for session history, EMA state, and tank data."""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".wot-console-overlay" / "tankvision.db"


@dataclass
class SessionRecord:
    """A completed session (one or more battles on the same tank)."""

    id: int | None
    tank_id: int
    tank_name: str
    start_moe: float
    end_moe: float
    battles: int
    start_ema: float
    end_ema: float
    started_at: float
    ended_at: float

    @property
    def delta(self) -> float:
        return self.end_moe - self.start_moe


@dataclass
class EmaSnapshot:
    """Persisted EMA state for a specific tank."""

    tank_id: int
    ema: float
    moe_percent: float
    updated_at: float


class SessionStore:
    """SQLite database for persisting session data and EMA state.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._ensure_schema()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _ensure_schema(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tank_id INTEGER NOT NULL,
                tank_name TEXT NOT NULL,
                start_moe REAL NOT NULL,
                end_moe REAL NOT NULL,
                battles INTEGER NOT NULL,
                start_ema REAL NOT NULL,
                end_ema REAL NOT NULL,
                started_at REAL NOT NULL,
                ended_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ema_state (
                tank_id INTEGER PRIMARY KEY,
                ema REAL NOT NULL,
                moe_percent REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS battle_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                tank_id INTEGER NOT NULL,
                direct_damage INTEGER NOT NULL,
                assisted_damage INTEGER NOT NULL,
                combined_damage INTEGER NOT NULL,
                ema_before REAL NOT NULL,
                ema_after REAL NOT NULL,
                moe_before REAL NOT NULL,
                moe_after REAL NOT NULL,
                played_at REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_tank ON sessions(tank_id);
            CREATE INDEX IF NOT EXISTS idx_battle_log_tank ON battle_log(tank_id);
            CREATE INDEX IF NOT EXISTS idx_battle_log_session ON battle_log(session_id);
        """)
        conn.commit()

    # --- EMA State ---

    def save_ema(self, tank_id: int, ema: float, moe_percent: float) -> None:
        """Save or update the current EMA state for a tank."""
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO ema_state (tank_id, ema, moe_percent, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(tank_id)
               DO UPDATE SET ema=excluded.ema, moe_percent=excluded.moe_percent,
                            updated_at=excluded.updated_at""",
            (tank_id, ema, moe_percent, time.time()),
        )
        conn.commit()

    def load_ema(self, tank_id: int) -> EmaSnapshot | None:
        """Load the last saved EMA state for a tank."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT tank_id, ema, moe_percent, updated_at FROM ema_state WHERE tank_id = ?",
            (tank_id,),
        ).fetchone()
        if row is None:
            return None
        return EmaSnapshot(
            tank_id=row["tank_id"],
            ema=row["ema"],
            moe_percent=row["moe_percent"],
            updated_at=row["updated_at"],
        )

    # --- Battle Log ---

    def log_battle(
        self,
        session_id: int | None,
        tank_id: int,
        direct_damage: int,
        assisted_damage: int,
        combined_damage: int,
        ema_before: float,
        ema_after: float,
        moe_before: float,
        moe_after: float,
    ) -> int:
        """Log a completed battle. Returns the battle log ID."""
        conn = self._get_conn()
        cursor = conn.execute(
            """INSERT INTO battle_log
               (session_id, tank_id, direct_damage, assisted_damage, combined_damage,
                ema_before, ema_after, moe_before, moe_after, played_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, tank_id, direct_damage, assisted_damage, combined_damage,
                ema_before, ema_after, moe_before, moe_after, time.time(),
            ),
        )
        conn.commit()
        return cursor.lastrowid

    # --- Sessions ---

    def start_session(
        self, tank_id: int, tank_name: str, start_moe: float, start_ema: float
    ) -> int:
        """Create a new session record. Returns the session ID."""
        conn = self._get_conn()
        now = time.time()
        cursor = conn.execute(
            """INSERT INTO sessions
               (tank_id, tank_name, start_moe, end_moe, battles, start_ema, end_ema,
                started_at, ended_at)
               VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)""",
            (tank_id, tank_name, start_moe, start_moe, start_ema, start_ema, now, now),
        )
        conn.commit()
        return cursor.lastrowid

    def update_session(
        self, session_id: int, end_moe: float, end_ema: float, battles: int
    ) -> None:
        """Update a session's end state."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE sessions
               SET end_moe = ?, end_ema = ?, battles = ?, ended_at = ?
               WHERE id = ?""",
            (end_moe, end_ema, battles, time.time(), session_id),
        )
        conn.commit()

    def get_recent_sessions(self, limit: int = 20) -> list[SessionRecord]:
        """Get the most recent sessions."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM sessions ORDER BY ended_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            SessionRecord(
                id=row["id"],
                tank_id=row["tank_id"],
                tank_name=row["tank_name"],
                start_moe=row["start_moe"],
                end_moe=row["end_moe"],
                battles=row["battles"],
                start_ema=row["start_ema"],
                end_ema=row["end_ema"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
            )
            for row in rows
        ]

    def get_tank_sessions(self, tank_id: int, limit: int = 50) -> list[SessionRecord]:
        """Get recent sessions for a specific tank."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM sessions WHERE tank_id = ? ORDER BY ended_at DESC LIMIT ?""",
            (tank_id, limit),
        ).fetchall()
        return [
            SessionRecord(
                id=row["id"],
                tank_id=row["tank_id"],
                tank_name=row["tank_name"],
                start_moe=row["start_moe"],
                end_moe=row["end_moe"],
                battles=row["battles"],
                start_ema=row["start_ema"],
                end_ema=row["end_ema"],
                started_at=row["started_at"],
                ended_at=row["ended_at"],
            )
            for row in rows
        ]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
