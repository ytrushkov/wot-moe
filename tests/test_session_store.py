"""Tests for SQLite session store."""

from pathlib import Path

import pytest

from tankvision.data.session_store import SessionStore


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    db_path = tmp_path / "test.db"
    s = SessionStore(db_path=db_path)
    yield s
    s.close()


class TestEmaState:
    def test_save_and_load(self, store: SessionStore):
        store.save_ema(tank_id=42, ema=3200.5, moe_percent=87.3)
        snapshot = store.load_ema(42)
        assert snapshot is not None
        assert snapshot.tank_id == 42
        assert snapshot.ema == 3200.5
        assert snapshot.moe_percent == 87.3
        assert snapshot.updated_at > 0

    def test_load_nonexistent(self, store: SessionStore):
        assert store.load_ema(999) is None

    def test_upsert_updates_existing(self, store: SessionStore):
        store.save_ema(42, ema=3000.0, moe_percent=85.0)
        store.save_ema(42, ema=3200.0, moe_percent=87.0)
        snapshot = store.load_ema(42)
        assert snapshot.ema == 3200.0
        assert snapshot.moe_percent == 87.0

    def test_multiple_tanks(self, store: SessionStore):
        store.save_ema(1, ema=3000.0, moe_percent=85.0)
        store.save_ema(2, ema=4000.0, moe_percent=92.0)
        assert store.load_ema(1).ema == 3000.0
        assert store.load_ema(2).ema == 4000.0


class TestBattleLog:
    def test_log_battle(self, store: SessionStore):
        battle_id = store.log_battle(
            session_id=None,
            tank_id=42,
            direct_damage=3500,
            assisted_damage=800,
            combined_damage=4300,
            ema_before=3000.0,
            ema_after=3025.7,
            moe_before=85.0,
            moe_after=85.5,
        )
        assert battle_id is not None
        assert battle_id > 0

    def test_log_battle_with_session(self, store: SessionStore):
        session_id = store.start_session(42, "T-54", 85.0, 3000.0)
        battle_id = store.log_battle(
            session_id=session_id,
            tank_id=42,
            direct_damage=3500,
            assisted_damage=800,
            combined_damage=4300,
            ema_before=3000.0,
            ema_after=3025.7,
            moe_before=85.0,
            moe_after=85.5,
        )
        assert battle_id > 0


class TestSessions:
    def test_start_session(self, store: SessionStore):
        session_id = store.start_session(42, "T-54", 85.0, 3000.0)
        assert session_id is not None
        assert session_id > 0

    def test_update_session(self, store: SessionStore):
        session_id = store.start_session(42, "T-54", 85.0, 3000.0)
        store.update_session(session_id, end_moe=87.5, end_ema=3200.0, battles=5)

        sessions = store.get_recent_sessions(limit=1)
        assert len(sessions) == 1
        assert sessions[0].id == session_id
        assert sessions[0].end_moe == 87.5
        assert sessions[0].battles == 5
        assert sessions[0].delta == pytest.approx(2.5)

    def test_get_recent_sessions_ordering(self, store: SessionStore):
        s1 = store.start_session(1, "Tank A", 80.0, 2800.0)
        store.update_session(s1, end_moe=82.0, end_ema=2900.0, battles=3)

        s2 = store.start_session(2, "Tank B", 90.0, 3500.0)
        store.update_session(s2, end_moe=91.0, end_ema=3550.0, battles=2)

        sessions = store.get_recent_sessions(limit=10)
        assert len(sessions) == 2
        # Most recent first
        assert sessions[0].tank_name == "Tank B"
        assert sessions[1].tank_name == "Tank A"

    def test_get_tank_sessions(self, store: SessionStore):
        s1 = store.start_session(42, "T-54", 85.0, 3000.0)
        store.update_session(s1, 86.0, 3100.0, 2)

        s2 = store.start_session(99, "IS-7", 70.0, 2500.0)
        store.update_session(s2, 72.0, 2600.0, 3)

        s3 = store.start_session(42, "T-54", 86.0, 3100.0)
        store.update_session(s3, 88.0, 3300.0, 4)

        # Only T-54 sessions
        tank_sessions = store.get_tank_sessions(42)
        assert len(tank_sessions) == 2
        assert all(s.tank_id == 42 for s in tank_sessions)

    def test_get_recent_sessions_limit(self, store: SessionStore):
        for i in range(10):
            sid = store.start_session(i, f"Tank {i}", 80.0, 2800.0)
            store.update_session(sid, 82.0, 2900.0, 1)

        sessions = store.get_recent_sessions(limit=3)
        assert len(sessions) == 3

    def test_empty_sessions(self, store: SessionStore):
        assert store.get_recent_sessions() == []
        assert store.get_tank_sessions(42) == []


class TestSessionRecord:
    def test_delta_property(self, store: SessionStore):
        sid = store.start_session(42, "T-54", 85.0, 3000.0)
        store.update_session(sid, end_moe=87.5, end_ema=3200.0, battles=5)
        session = store.get_recent_sessions(limit=1)[0]
        assert session.delta == pytest.approx(2.5)
