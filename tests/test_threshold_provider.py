"""Tests for MoE threshold provider."""

import json
import time
from pathlib import Path

import pytest

from tankvision.data.threshold_provider import (
    CACHE_TTL_SECONDS,
    MoeThresholds,
    ThresholdProvider,
)


class TestMoeThresholds:
    def test_target_for_mark_levels(self):
        t = MoeThresholds(
            tank_id=1, tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000, fetched_at=time.time(),
        )
        assert t.target_for_mark(1) == 2500
        assert t.target_for_mark(2) == 3200
        assert t.target_for_mark(3) == 4000

    def test_target_for_invalid_mark_raises(self):
        t = MoeThresholds(
            tank_id=1, tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000,
        )
        with pytest.raises(ValueError):
            t.target_for_mark(4)

    def test_is_stale_when_old(self):
        t = MoeThresholds(
            tank_id=1, tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000,
            fetched_at=time.time() - CACHE_TTL_SECONDS - 1,
        )
        assert t.is_stale

    def test_is_not_stale_when_fresh(self):
        t = MoeThresholds(
            tank_id=1, tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000,
            fetched_at=time.time(),
        )
        assert not t.is_stale

    def test_round_trip_serialization(self):
        original = MoeThresholds(
            tank_id=42, tank_name="Object 140",
            mark_65=2800, mark_85=3600, mark_95=4500,
            fetched_at=1700000000.0,
        )
        restored = MoeThresholds.from_dict(original.to_dict())
        assert restored.tank_id == original.tank_id
        assert restored.tank_name == original.tank_name
        assert restored.mark_65 == original.mark_65
        assert restored.mark_95 == original.mark_95

    def test_manual_factory(self):
        t = MoeThresholds.manual(1, "T-54", target_damage=4000)
        assert t.mark_95 == 4000
        assert t.mark_85 == pytest.approx(4000 * 85 / 95, rel=0.01)
        assert t.mark_65 == pytest.approx(4000 * 65 / 95, rel=0.01)
        assert t.fetched_at > 0


class TestThresholdProvider:
    @pytest.fixture
    def provider(self, tmp_path: Path) -> ThresholdProvider:
        return ThresholdProvider(cache_dir=tmp_path)

    def test_empty_cache_returns_none(self, provider: ThresholdProvider):
        assert provider.get_cached(999) is None

    def test_manual_set_and_get(self, provider: ThresholdProvider):
        provider.set_manual(42, "Object 140", 4500)
        cached = provider.get_cached(42)
        assert cached is not None
        assert cached.mark_95 == 4500
        assert cached.tank_name == "Object 140"

    def test_disk_persistence(self, tmp_path: Path):
        # Write with one provider instance
        provider1 = ThresholdProvider(cache_dir=tmp_path)
        provider1.set_manual(42, "Object 140", 4500)

        # Read with a new provider instance (empty memory cache)
        provider2 = ThresholdProvider(cache_dir=tmp_path)
        cached = provider2.get_cached(42)
        assert cached is not None
        assert cached.mark_95 == 4500

    def test_stale_cache_not_returned(self, tmp_path: Path):
        provider = ThresholdProvider(cache_dir=tmp_path)

        # Write a stale entry directly to disk
        cache_file = tmp_path / "moe_thresholds.json"
        stale_data = {
            "42": {
                "tank_id": 42,
                "tank_name": "Object 140",
                "mark_65": 2800,
                "mark_85": 3600,
                "mark_95": 4500,
                "fetched_at": time.time() - CACHE_TTL_SECONDS - 100,
            }
        }
        cache_file.write_text(json.dumps(stale_data))

        # Fresh cache should return None
        assert provider.get_cached(42) is None

    @pytest.mark.asyncio
    async def test_get_thresholds_falls_back_to_stale(self, tmp_path: Path):
        """When remote fetch fails, stale cache should be returned."""
        provider = ThresholdProvider(cache_dir=tmp_path)

        # Pre-populate with stale data
        cache_file = tmp_path / "moe_thresholds.json"
        stale_data = {
            "42": {
                "tank_id": 42,
                "tank_name": "Object 140",
                "mark_65": 2800,
                "mark_85": 3600,
                "mark_95": 4500,
                "fetched_at": time.time() - CACHE_TTL_SECONDS - 100,
            }
        }
        cache_file.write_text(json.dumps(stale_data))

        # get_thresholds should return stale data when remote fails
        result = await provider.get_thresholds(42, "Object 140")
        assert result is not None
        assert result.mark_95 == 4500

    def test_multiple_tanks_cached(self, provider: ThresholdProvider):
        provider.set_manual(1, "T-54", 4000)
        provider.set_manual(2, "Object 140", 4500)
        provider.set_manual(3, "IS-7", 3500)

        assert provider.get_cached(1).mark_95 == 4000
        assert provider.get_cached(2).mark_95 == 4500
        assert provider.get_cached(3).mark_95 == 3500
