"""Tests for MoE threshold provider."""

import json
import time
from pathlib import Path

import pytest

from tankvision.data.threshold_provider import (
    CACHE_TTL_SECONDS,
    MoeThresholds,
    ThresholdProvider,
    parse_marks_page,
)


class TestMoeThresholds:
    def test_target_for_mark_levels(self):
        t = MoeThresholds(
            tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000,
        )
        assert t.target_for_mark(1) == 2500
        assert t.target_for_mark(2) == 3200
        assert t.target_for_mark(3) == 4000

    def test_target_for_invalid_mark_raises(self):
        t = MoeThresholds(
            tank_name="T-54",
            mark_65=2500, mark_85=3200, mark_95=4000,
        )
        with pytest.raises(ValueError):
            t.target_for_mark(4)

    def test_round_trip_serialization(self):
        original = MoeThresholds(
            tank_name="Object 140",
            mark_65=2800, mark_85=3600, mark_95=4500,
        )
        restored = MoeThresholds.from_dict(original.to_dict())
        assert restored.tank_name == original.tank_name
        assert restored.mark_65 == original.mark_65
        assert restored.mark_95 == original.mark_95


class TestParseMarksPage:
    def test_parse_html_table(self):
        html = """
        <html><body>
        <table>
        <thead>
        <tr><th>Tank Name</th><th>65%</th><th>85%</th><th>95%</th></tr>
        </thead>
        <tbody>
        <tr><td>T-54</td><td>2,500</td><td>3,200</td><td>4,000</td></tr>
        <tr><td>Object 140</td><td>2,800</td><td>3,600</td><td>4,500</td></tr>
        <tr><td>IS-7</td><td>2,200</td><td>2,800</td><td>3,500</td></tr>
        <tr><td>E 100</td><td>2,000</td><td>2,600</td><td>3,200</td></tr>
        <tr><td>Obj. 268</td><td>1,800</td><td>2,300</td><td>2,900</td></tr>
        <tr><td>T110E5</td><td>2,100</td><td>2,700</td><td>3,400</td></tr>
        <tr><td>Leopard 1</td><td>2,300</td><td>3,000</td><td>3,700</td></tr>
        <tr><td>FV215b</td><td>2,400</td><td>3,100</td><td>3,800</td></tr>
        <tr><td>AMX 50B</td><td>2,500</td><td>3,200</td><td>4,000</td></tr>
        <tr><td>Centurion AX</td><td>2,000</td><td>2,600</td><td>3,300</td></tr>
        <tr><td>T57 Heavy</td><td>2,100</td><td>2,700</td><td>3,400</td></tr>
        </tbody>
        </table>
        </body></html>
        """
        results = parse_marks_page(html)
        assert len(results) == 11

        t54 = next(r for r in results if r["tank_name"] == "T-54")
        assert t54["mark_95"] == 4000
        assert t54["mark_85"] == 3200
        assert t54["mark_65"] == 2500

    def test_parse_embedded_json(self):
        entries = [
            {"name": f"Tank{i}", "mark_65": 2000 + i * 100, "mark_85": 2800 + i * 100, "mark_95": 3500 + i * 100}
            for i in range(15)
        ]
        html = f"<html><script>var tankData = {json.dumps(entries)};</script></html>"
        results = parse_marks_page(html)
        assert len(results) == 15
        assert results[0]["tank_name"] == "Tank0"

    def test_parse_empty_html_returns_empty(self):
        results = parse_marks_page("<html><body><p>No data</p></body></html>")
        assert results == []

    def test_parse_table_with_only_95_column(self):
        html = """
        <table>
        <thead><tr><th>Vehicle</th><th>95%</th></tr></thead>
        <tbody>
        """ + "".join(
            f"<tr><td>Tank{i}</td><td>{3000 + i * 100}</td></tr>"
            for i in range(12)
        ) + """
        </tbody>
        </table>
        """
        results = parse_marks_page(html)
        assert len(results) == 12
        # 65% and 85% should be estimated from 95%
        t0 = results[0]
        assert t0["mark_95"] == 3000
        assert t0["mark_85"] == pytest.approx(3000 * 85.0 / 95.0, rel=0.01)
        assert t0["mark_65"] == pytest.approx(3000 * 65.0 / 95.0, rel=0.01)


class TestThresholdProvider:
    @pytest.fixture
    def provider(self, tmp_path: Path) -> ThresholdProvider:
        return ThresholdProvider(cache_dir=tmp_path)

    def test_empty_cache_returns_none(self, provider: ThresholdProvider):
        assert provider.get_by_name("T-54") is None

    def test_empty_cache_is_stale(self, provider: ThresholdProvider):
        assert provider.is_stale

    def test_get_by_name_exact_match(self, provider: ThresholdProvider):
        provider._name_index["t-54"] = MoeThresholds("T-54", 2500, 3200, 4000)
        provider._all_names = list(provider._name_index.keys())

        result = provider.get_by_name("T-54")
        assert result is not None
        assert result.mark_95 == 4000

    def test_get_by_name_case_insensitive(self, provider: ThresholdProvider):
        provider._name_index["object 140"] = MoeThresholds("Object 140", 2800, 3600, 4500)
        provider._all_names = list(provider._name_index.keys())

        result = provider.get_by_name("OBJECT 140")
        assert result is not None
        assert result.mark_95 == 4500

    def test_get_by_name_fuzzy_match(self, provider: ThresholdProvider):
        provider._name_index["object 140"] = MoeThresholds("Object 140", 2800, 3600, 4500)
        provider._all_names = list(provider._name_index.keys())

        # Slight misspelling from OCR
        result = provider.get_by_name("0bject 14O")
        assert result is not None
        assert result.tank_name == "Object 140"

    def test_disk_persistence(self, tmp_path: Path):
        # Write cache via internal methods
        provider1 = ThresholdProvider(cache_dir=tmp_path)
        entries = [
            {"tank_name": "T-54", "mark_65": 2500, "mark_85": 3200, "mark_95": 4000},
            {"tank_name": "Object 140", "mark_65": 2800, "mark_85": 3600, "mark_95": 4500},
        ]
        provider1._rebuild_index(entries)
        provider1._fetched_at = time.time()
        provider1._save_to_disk(entries)

        # Read with a new provider instance (cold start)
        provider2 = ThresholdProvider(cache_dir=tmp_path)
        assert provider2.tank_count == 2
        result = provider2.get_by_name("T-54")
        assert result is not None
        assert result.mark_95 == 4000

    def test_stale_cache_detected(self, tmp_path: Path):
        # Write a stale cache file
        cache_file = tmp_path / "moe_thresholds.json"
        payload = {
            "_meta": {"fetched_at": time.time() - CACHE_TTL_SECONDS - 100},
            "tanks": [
                {"tank_name": "T-54", "mark_65": 2500, "mark_85": 3200, "mark_95": 4000},
            ],
        }
        cache_file.write_text(json.dumps(payload))

        provider = ThresholdProvider(cache_dir=tmp_path)
        # Data is loaded but stale
        assert provider.tank_count == 1
        assert provider.is_stale
        # get_by_name still works with stale data
        assert provider.get_by_name("T-54") is not None

    def test_fresh_cache_not_stale(self, tmp_path: Path):
        cache_file = tmp_path / "moe_thresholds.json"
        payload = {
            "_meta": {"fetched_at": time.time()},
            "tanks": [
                {"tank_name": "T-54", "mark_65": 2500, "mark_85": 3200, "mark_95": 4000},
            ],
        }
        cache_file.write_text(json.dumps(payload))

        provider = ThresholdProvider(cache_dir=tmp_path)
        assert not provider.is_stale

    def test_corrupt_cache_handled(self, tmp_path: Path):
        cache_file = tmp_path / "moe_thresholds.json"
        cache_file.write_text("not valid json{{{")

        provider = ThresholdProvider(cache_dir=tmp_path)
        assert provider.tank_count == 0
        assert provider.is_stale
