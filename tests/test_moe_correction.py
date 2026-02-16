"""Tests for MoeCalculator EMA correction (post-battle API correction)."""

import time

from tankvision.calculation.ema import compute_ema_update, DEFAULT_ALPHA
from tankvision.calculation.moe_calculator import MoeCalculator
from tankvision.ocr.ocr_pipeline import DamageReading


class TestCorrectLastBattle:
    def test_correct_reduces_inflated_estimate(self):
        """OCR overestimates combined damage (tracking + spotting);
        API correction with the real value should lower the MoE."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        # Simulate a battle with high OCR-estimated damage
        # (tracking + spotting = 1200, but WG uses max(tracking, spotting) = 800)
        for _ in range(5):
            calc.update(DamageReading(direct_damage=3000, assisted_damage=1200))

        # Force battle end by feeding zeros
        import tankvision.calculation.battle_detector as bd
        calc._detector._in_battle = True
        calc._detector._last_nonzero_damage = 4200
        calc._detector._last_nonzero_time = time.monotonic() - 10
        calc._detector._consecutive_zeros = 10

        state = calc.update(DamageReading(0, 0))
        assert state.status == "battle_ended"

        moe_before_correction = calc.current_moe
        ema_before_correction = calc.ema

        # Correct with API value (3800 vs OCR's 4200)
        corrected_state = calc.correct_last_battle(3800)
        assert corrected_state is not None
        assert calc.current_moe < moe_before_correction
        assert calc.ema < ema_before_correction

    def test_correct_with_same_damage_is_noop(self):
        """If API damage matches OCR damage, correction shouldn't change anything."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        # Start a battle, then force conditions for battle end
        calc.update(DamageReading(3500, 0))
        # Set detector state AFTER the nonzero reading to force battle end
        calc._detector._last_nonzero_time = time.monotonic() - 10
        calc._detector._consecutive_zeros = 10

        state = calc.update(DamageReading(0, 0))
        assert state.status == "battle_ended"

        moe_after_estimate = calc.current_moe
        # Detector recorded 3500 as last_battle_damage
        calc.correct_last_battle(3500)
        # Should be essentially the same
        assert abs(calc.current_moe - moe_after_estimate) < 0.01

    def test_correct_without_battle_returns_none(self):
        """Correcting before any battle has ended should return None."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)
        assert calc.correct_last_battle(3000) is None

    def test_correct_clears_ema_before(self):
        """After correction, _ema_before_last_battle should be None."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        calc.update(DamageReading(3500, 0))
        calc._detector._last_nonzero_time = time.monotonic() - 10
        calc._detector._consecutive_zeros = 10
        calc.update(DamageReading(0, 0))

        assert calc._ema_before_last_battle is not None
        calc.correct_last_battle(3000)
        assert calc._ema_before_last_battle is None

    def test_double_correct_returns_none(self):
        """Second correction attempt should return None since state was cleared."""
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)

        calc.update(DamageReading(3500, 0))
        calc._detector._last_nonzero_time = time.monotonic() - 10
        calc._detector._consecutive_zeros = 10
        calc.update(DamageReading(0, 0))

        assert calc.correct_last_battle(3000) is not None
        assert calc.correct_last_battle(3000) is None


class TestSetMoeFromApi:
    def test_overrides_ema(self):
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)
        calc.set_moe_from_api(90.0)
        assert abs(calc.current_moe - 90.0) < 0.01

    def test_override_zero_target_is_noop(self):
        calc = MoeCalculator(current_moe=0.0, target_damage=0)
        calc.set_moe_from_api(90.0)
        assert calc.ema == 0.0


class TestMoeCalculatorProperties:
    def test_ema_property(self):
        calc = MoeCalculator(current_moe=50.0, target_damage=4000)
        assert calc.ema > 0

    def test_current_moe_property(self):
        calc = MoeCalculator(current_moe=75.0, target_damage=4000)
        assert abs(calc.current_moe - 75.0) < 0.01

    def test_battles_this_session_property(self):
        calc = MoeCalculator(target_damage=4000)
        assert calc.battles_this_session == 0

    def test_set_tank_resets_correction_state(self):
        calc = MoeCalculator(current_moe=80.0, target_damage=4000)
        # Start battle, then force conditions for end
        calc.update(DamageReading(3500, 0))
        calc._detector._last_nonzero_time = time.monotonic() - 10
        calc._detector._consecutive_zeros = 10
        calc.update(DamageReading(0, 0))

        assert calc._ema_before_last_battle is not None

        calc.set_tank("Object 140", target_damage=5000, current_moe=60.0)
        assert calc._ema_before_last_battle is None
        assert calc._last_battle_damage == 0
