"""Integration tests for __main__ startup, persistence, and post-battle correction."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tankvision.__main__ import (
    _poll_api_correction,
    _resolve_startup_data,
    _resolve_target_damage,
)
from tankvision.calculation.moe_calculator import MoeCalculator
from tankvision.data.session_store import SessionStore
from tankvision.data.threshold_provider import MoeThresholds, ThresholdProvider
from tankvision.data.wargaming_api import TankSnapshot, WargamingApi


# --- Startup resolution ---


class TestResolveStartupData:
    @pytest.mark.asyncio
    async def test_offline_mode_when_no_gamertag(self):
        config = {"player": {"gamertag": "", "platform": "xbox"}}
        api = WargamingApi(application_id="test")
        result = await _resolve_startup_data(config, api)
        assert result["account_id"] is None
        assert result["tank_id"] == 0

    @pytest.mark.asyncio
    async def test_resolves_gamertag_and_tank(self):
        config = {"player": {"gamertag": "TestPlayer", "platform": "xbox"}}
        api = WargamingApi(application_id="test")

        with (
            patch.object(api, "resolve_gamertag", new_callable=AsyncMock) as mock_resolve,
            patch.object(api, "detect_active_tank", new_callable=AsyncMock) as mock_detect,
            patch.object(api, "get_vehicles", new_callable=AsyncMock) as mock_vehicles,
            patch.object(api, "get_tank_snapshot", new_callable=AsyncMock) as mock_snapshot,
        ):
            mock_resolve.return_value = 12345
            mock_detect.return_value = {
                "tank_id": 42,
                "marks_on_gun": 2,
                "last_battle_time": 1700000000,
            }
            mock_vehicles.return_value = {
                "42": {"short_name": "T-54", "tier": 9},
            }
            mock_snapshot.return_value = TankSnapshot(
                tank_id=42, battles=150, marks_on_gun=2,
                damage_dealt=450000, damage_assisted=120000,
            )

            result = await _resolve_startup_data(config, api)

        assert result["account_id"] == 12345
        assert result["tank_id"] == 42
        assert result["tank_name"] == "T-54"
        assert result["marks_on_gun"] == 2
        assert result["api_snapshot"].battles == 150

    @pytest.mark.asyncio
    async def test_gamertag_not_found_degrades(self):
        config = {"player": {"gamertag": "Nobody", "platform": "xbox"}}
        api = WargamingApi(application_id="test")

        with patch.object(api, "resolve_gamertag", new_callable=AsyncMock) as mock:
            mock.return_value = None
            result = await _resolve_startup_data(config, api)

        assert result["account_id"] is None
        assert result["tank_id"] == 0

    @pytest.mark.asyncio
    async def test_api_error_degrades_gracefully(self):
        config = {"player": {"gamertag": "TestPlayer", "platform": "xbox"}}
        api = WargamingApi(application_id="test")

        with patch.object(api, "resolve_gamertag", new_callable=AsyncMock) as mock:
            mock.side_effect = Exception("Network error")
            result = await _resolve_startup_data(config, api)

        assert result["account_id"] is None


# --- Target damage resolution ---


class TestResolveTargetDamage:
    @pytest.mark.asyncio
    async def test_uses_threshold_provider(self, tmp_path: Path):
        provider = ThresholdProvider(cache_dir=tmp_path)
        provider.set_manual(42, "T-54", 4000)

        config = {"moe": {"target_damage": 0}}
        target = await _resolve_target_damage(config, 42, "T-54", 2, provider)
        # 2 marks → targeting 3rd mark → mark_95 = 4000
        assert target == 4000

    @pytest.mark.asyncio
    async def test_falls_back_to_config(self, tmp_path: Path):
        provider = ThresholdProvider(cache_dir=tmp_path)
        config = {"moe": {"target_damage": 3500}}
        target = await _resolve_target_damage(config, 0, "", 0, provider)
        assert target == 3500

    @pytest.mark.asyncio
    async def test_no_tank_id_uses_config(self, tmp_path: Path):
        provider = ThresholdProvider(cache_dir=tmp_path)
        provider.set_manual(42, "T-54", 4000)

        config = {"moe": {"target_damage": 2000}}
        # tank_id=0 means no tank detected
        target = await _resolve_target_damage(config, 0, "", 0, provider)
        assert target == 2000


# --- Post-battle API correction ---


class TestPollApiCorrection:
    @pytest.fixture(autouse=True)
    def _fast_polling(self):
        """Speed up API polling for tests."""
        import tankvision.__main__ as main_mod
        orig_attempts = main_mod._API_POLL_ATTEMPTS
        orig_delay = main_mod._API_POLL_BASE_DELAY
        main_mod._API_POLL_ATTEMPTS = 2
        main_mod._API_POLL_BASE_DELAY = 0.01
        yield
        main_mod._API_POLL_ATTEMPTS = orig_attempts
        main_mod._API_POLL_BASE_DELAY = orig_delay

    @pytest.mark.asyncio
    async def test_correction_with_single_battle(self, tmp_path: Path):
        """After one battle, API returns +1 battle with damage delta."""
        api = WargamingApi(application_id="test")
        calculator = MoeCalculator(current_moe=80.0, target_damage=4000, tank_name="T-54")
        store = SessionStore(db_path=tmp_path / "test.db")
        server = MagicMock()
        server.broadcast = AsyncMock()

        # Simulate a battle ending in the calculator
        import time
        from tankvision.ocr.ocr_pipeline import DamageReading
        calculator.update(DamageReading(4200, 0))
        # Force detector state AFTER the nonzero reading so battle end triggers
        calculator._detector._last_nonzero_time = time.monotonic() - 10
        calculator._detector._consecutive_zeros = 10
        calculator.update(DamageReading(0, 0))

        moe_estimated = calculator.current_moe

        before = TankSnapshot(
            tank_id=42, battles=150, marks_on_gun=2,
            damage_dealt=450000, damage_assisted=120000,
        )
        after = TankSnapshot(
            tank_id=42, battles=151, marks_on_gun=2,
            damage_dealt=453500, damage_assisted=120300,
        )

        with patch.object(api, "get_tank_snapshot", new_callable=AsyncMock) as mock:
            mock.return_value = after
            result = await _poll_api_correction(
                api, calculator, store, 12345, 42, None, before, server,
            )

        assert result.battles == 151
        # API combined = 3500 + 300 = 3800, less than OCR's 4200
        assert calculator.current_moe < moe_estimated
        server.broadcast.assert_called_once()
        store.close()

    @pytest.mark.asyncio
    async def test_correction_skipped_for_multiple_battles(self, tmp_path: Path):
        """If API shows >1 new battle, correction is skipped."""
        api = WargamingApi(application_id="test")
        calculator = MoeCalculator(current_moe=80.0, target_damage=4000)
        store = SessionStore(db_path=tmp_path / "test.db")
        server = MagicMock()
        server.broadcast = AsyncMock()

        # Simulate a battle ending so _ema_before_last_battle is set
        import time
        from tankvision.ocr.ocr_pipeline import DamageReading
        calculator.update(DamageReading(4200, 0))
        calculator._detector._last_nonzero_time = time.monotonic() - 10
        calculator._detector._consecutive_zeros = 10
        calculator.update(DamageReading(0, 0))

        moe_estimated = calculator.current_moe

        before = TankSnapshot(
            tank_id=42, battles=150, marks_on_gun=2,
            damage_dealt=450000, damage_assisted=120000,
        )
        after = TankSnapshot(
            tank_id=42, battles=153, marks_on_gun=2,
            damage_dealt=462000, damage_assisted=123000,
        )

        with patch.object(api, "get_tank_snapshot", new_callable=AsyncMock) as mock:
            mock.return_value = after
            result = await _poll_api_correction(
                api, calculator, store, 12345, 42, None, before, server,
            )

        assert result.battles == 153
        # MoE should not have changed since correction was skipped
        assert calculator.current_moe == moe_estimated
        server.broadcast.assert_not_called()
        store.close()

    @pytest.mark.asyncio
    async def test_timeout_returns_original_snapshot(self, tmp_path: Path):
        """If API never updates, return the original snapshot."""
        api = WargamingApi(application_id="test")
        calculator = MoeCalculator(current_moe=80.0, target_damage=4000)
        store = SessionStore(db_path=tmp_path / "test.db")
        server = MagicMock()
        server.broadcast = AsyncMock()

        before = TankSnapshot(
            tank_id=42, battles=150, marks_on_gun=2,
            damage_dealt=450000, damage_assisted=120000,
        )

        with patch.object(api, "get_tank_snapshot", new_callable=AsyncMock) as mock:
            # API always returns same battle count
            mock.return_value = before
            result = await _poll_api_correction(
                api, calculator, store, 12345, 42, None, before, server,
            )

        assert result is before
        server.broadcast.assert_not_called()
        store.close()


# --- Persistence flow ---


class TestPersistenceFlow:
    def test_ema_round_trip(self, tmp_path: Path):
        """EMA saved by one session is restored by the next."""
        store = SessionStore(db_path=tmp_path / "test.db")

        # Simulate first session saving EMA
        store.save_ema(tank_id=42, ema=3200.5, moe_percent=87.3)
        store.close()

        # New session restores it
        store2 = SessionStore(db_path=tmp_path / "test.db")
        snapshot = store2.load_ema(42)
        assert snapshot is not None
        assert snapshot.ema == 3200.5
        assert snapshot.moe_percent == 87.3
        store2.close()

    def test_session_lifecycle(self, tmp_path: Path):
        """Start session → log battles → update session → verify."""
        store = SessionStore(db_path=tmp_path / "test.db")

        session_id = store.start_session(42, "T-54", 85.0, 3000.0)
        assert session_id > 0

        store.log_battle(
            session_id=session_id, tank_id=42,
            direct_damage=3500, assisted_damage=800, combined_damage=4300,
            ema_before=3000.0, ema_after=3025.7, moe_before=85.0, moe_after=85.5,
        )

        store.update_session(session_id, end_moe=85.5, end_ema=3025.7, battles=1)

        sessions = store.get_recent_sessions(limit=1)
        assert len(sessions) == 1
        assert sessions[0].battles == 1
        assert sessions[0].end_moe == 85.5
        store.close()
