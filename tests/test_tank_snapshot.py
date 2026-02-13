"""Tests for TankSnapshot, BattleDelta, and WargamingApi snapshot methods."""

from unittest.mock import AsyncMock, patch

import pytest

from tankvision.data.wargaming_api import (
    BattleDelta,
    TankSnapshot,
    WargamingApi,
)


class TestTankSnapshot:
    def test_battle_delta_single_battle(self):
        before = TankSnapshot(
            tank_id=42, battles=100, marks_on_gun=2,
            damage_dealt=400000, damage_assisted=100000,
        )
        after = TankSnapshot(
            tank_id=42, battles=101, marks_on_gun=2,
            damage_dealt=403500, damage_assisted=100800,
        )
        delta = before.battle_delta(after)
        assert delta is not None
        assert delta.damage_dealt == 3500
        assert delta.damage_assisted == 800
        assert delta.combined == 4300
        assert not delta.marks_changed

    def test_battle_delta_mark_changed(self):
        before = TankSnapshot(
            tank_id=42, battles=100, marks_on_gun=2,
            damage_dealt=400000, damage_assisted=100000,
        )
        after = TankSnapshot(
            tank_id=42, battles=101, marks_on_gun=3,
            damage_dealt=405000, damage_assisted=101500,
        )
        delta = before.battle_delta(after)
        assert delta is not None
        assert delta.marks_changed
        assert delta.marks_on_gun_before == 2
        assert delta.marks_on_gun_after == 3

    def test_battle_delta_multiple_battles_returns_none(self):
        before = TankSnapshot(
            tank_id=42, battles=100, marks_on_gun=2,
            damage_dealt=400000, damage_assisted=100000,
        )
        after = TankSnapshot(
            tank_id=42, battles=103, marks_on_gun=2,
            damage_dealt=412000, damage_assisted=103000,
        )
        assert before.battle_delta(after) is None

    def test_battle_delta_no_change_returns_none(self):
        before = TankSnapshot(
            tank_id=42, battles=100, marks_on_gun=2,
            damage_dealt=400000, damage_assisted=100000,
        )
        same = TankSnapshot(
            tank_id=42, battles=100, marks_on_gun=2,
            damage_dealt=400000, damage_assisted=100000,
        )
        assert before.battle_delta(same) is None


class TestBattleDelta:
    def test_combined_property(self):
        delta = BattleDelta(
            damage_dealt=3500, damage_assisted=800,
            marks_on_gun_before=2, marks_on_gun_after=2,
        )
        assert delta.combined == 4300

    def test_marks_changed_false(self):
        delta = BattleDelta(
            damage_dealt=3500, damage_assisted=800,
            marks_on_gun_before=2, marks_on_gun_after=2,
        )
        assert not delta.marks_changed

    def test_marks_changed_true(self):
        delta = BattleDelta(
            damage_dealt=5000, damage_assisted=1500,
            marks_on_gun_before=2, marks_on_gun_after=3,
        )
        assert delta.marks_changed


class TestWargamingApiSnapshot:
    @pytest.fixture
    def api(self):
        return WargamingApi(application_id="test", platform="xbox")

    @pytest.mark.asyncio
    async def test_get_tank_snapshot(self, api):
        tank_stats = [{
            "tank_id": 42,
            "marks_on_gun": 2,
            "all": {
                "battles": 150,
                "damage_dealt": 450000,
                "damage_assisted": 120000,
            },
        }]
        with patch.object(api, "get_player_tanks", new_callable=AsyncMock) as mock:
            mock.return_value = tank_stats
            snap = await api.get_tank_snapshot(789, 42)
            assert snap is not None
            assert snap.tank_id == 42
            assert snap.battles == 150
            assert snap.marks_on_gun == 2
            assert snap.damage_dealt == 450000
            assert snap.damage_assisted == 120000

    @pytest.mark.asyncio
    async def test_get_tank_snapshot_empty(self, api):
        with patch.object(api, "get_player_tanks", new_callable=AsyncMock) as mock:
            mock.return_value = []
            assert await api.get_tank_snapshot(789, 42) is None

    @pytest.mark.asyncio
    async def test_detect_active_tank(self, api):
        tanks = [
            {"tank_id": 1, "last_battle_time": 1000},
            {"tank_id": 2, "last_battle_time": 3000},
            {"tank_id": 3, "last_battle_time": 2000},
        ]
        with patch.object(api, "get_player_tanks", new_callable=AsyncMock) as mock:
            mock.return_value = tanks
            result = await api.detect_active_tank(789)
            assert result["tank_id"] == 2  # Most recent

    @pytest.mark.asyncio
    async def test_detect_active_tank_empty(self, api):
        with patch.object(api, "get_player_tanks", new_callable=AsyncMock) as mock:
            mock.return_value = []
            assert await api.detect_active_tank(789) is None
