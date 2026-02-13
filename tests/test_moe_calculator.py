"""Tests for MoE calculator."""

from tankvision.calculation.moe_calculator import MoeCalculator, MoeState
from tankvision.ocr.ocr_pipeline import DamageReading


class TestMoeState:
    def test_to_dict_has_all_fields(self):
        state = MoeState()
        d = state.to_dict()
        expected_keys = {
            "tank_name", "moe_percent", "projected_moe_percent", "delta",
            "ema", "target_damage", "direct_damage", "assisted_damage",
            "combined_damage", "battles_this_session", "in_battle", "status",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_rounds_floats(self):
        state = MoeState(moe_percent=85.12345, delta=0.12345, ema=3456.789)
        d = state.to_dict()
        assert d["moe_percent"] == 85.12
        assert d["delta"] == 0.12
        assert d["ema"] == 3456.8


class TestMoeCalculator:
    def test_initial_state_is_idle(self):
        calc = MoeCalculator(target_damage=4000, tank_name="T-54")
        reading = DamageReading(direct_damage=0, assisted_damage=0)
        state = calc.update(reading)
        assert state.status == "idle"
        assert state.tank_name == "T-54"
        assert state.combined_damage == 0

    def test_damage_starts_battle(self):
        calc = MoeCalculator(target_damage=4000)
        reading = DamageReading(direct_damage=1500, assisted_damage=300)
        state = calc.update(reading)
        assert state.status == "battle_active"
        assert state.in_battle
        assert state.combined_damage == 1800

    def test_projected_moe_changes_with_damage(self):
        calc = MoeCalculator(current_moe=50.0, target_damage=4000)
        state_low = calc.update(DamageReading(500, 0))
        state_high = calc.update(DamageReading(5000, 0))
        assert state_high.projected_moe_percent > state_low.projected_moe_percent

    def test_zero_target_damage_gives_zero_moe(self):
        calc = MoeCalculator(target_damage=0)
        state = calc.update(DamageReading(3000, 0))
        assert state.moe_percent == 0.0
        assert state.projected_moe_percent == 0.0

    def test_set_tank_resets_state(self):
        calc = MoeCalculator(target_damage=4000, tank_name="T-54")
        calc.update(DamageReading(2000, 0))
        calc.set_tank("Object 140", target_damage=5000, current_moe=60.0)
        state = calc.update(DamageReading(0, 0))
        assert state.tank_name == "Object 140"
        assert state.target_damage == 5000

    def test_damage_reading_combined(self):
        reading = DamageReading(direct_damage=2500, assisted_damage=800)
        assert reading.combined == 3300


class TestMoeCalculatorEmaProgression:
    def test_high_damage_increases_moe(self):
        """Doing more damage than your EMA should increase MoE%."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        # Simulate a battle with very high damage
        reading = DamageReading(direct_damage=6000, assisted_damage=0)
        state = calc.update(reading)

        # Projected MoE should be higher than starting 80%
        assert state.projected_moe_percent > 80.0

    def test_low_damage_decreases_moe(self):
        """Doing less damage than your EMA should decrease MoE%."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        reading = DamageReading(direct_damage=100, assisted_damage=0)
        state = calc.update(reading)

        # Projected MoE should be lower than starting 80%
        assert state.projected_moe_percent < 80.0
