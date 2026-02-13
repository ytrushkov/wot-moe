"""Tests for battle detection logic."""

import time
from unittest.mock import patch

from tankvision.calculation.battle_detector import BattleDetector


class TestBattleDetector:
    def test_initial_state_is_idle(self):
        detector = BattleDetector()
        assert not detector.in_battle
        assert detector.battle_count == 0

    def test_nonzero_damage_starts_battle(self):
        detector = BattleDetector()
        status = detector.update(500)
        assert status == "battle_active"
        assert detector.in_battle
        assert detector.battle_count == 1

    def test_increasing_damage_stays_active(self):
        detector = BattleDetector()
        detector.update(500)
        status = detector.update(1200)
        assert status == "battle_active"
        assert detector.battle_count == 1

    def test_zero_during_battle_does_not_immediately_end(self):
        """A single zero frame shouldn't end the battle (could be a glitch)."""
        detector = BattleDetector(zero_frames_required=3)
        detector.update(1000)
        status = detector.update(0)
        assert status == "battle_active"  # Not enough zeros yet
        assert detector.in_battle

    def test_battle_ends_after_enough_zeros_and_time(self):
        detector = BattleDetector(
            zero_frames_required=2,
            reset_gap_seconds=0.0,  # No time gap required for test
        )
        detector.update(1000)

        # Simulate time passing
        with patch("tankvision.calculation.battle_detector.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 5.0
            detector.update(0)  # zero #1
            status = detector.update(0)  # zero #2

        assert status == "battle_ended"
        assert not detector.in_battle
        assert detector.last_battle_damage == 1000

    def test_second_battle_increments_count(self):
        detector = BattleDetector(
            zero_frames_required=1,
            reset_gap_seconds=0.0,
        )
        # Battle 1
        detector.update(500)
        with patch("tankvision.calculation.battle_detector.time") as mock_time:
            mock_time.monotonic.return_value = time.monotonic() + 5.0
            detector.update(0)
        assert detector.battle_count == 1

        # Battle 2
        detector.update(800)
        assert detector.battle_count == 2

    def test_zero_damage_when_idle_stays_idle(self):
        detector = BattleDetector()
        status = detector.update(0)
        assert status == "idle"
        assert not detector.in_battle

    def test_reset(self):
        detector = BattleDetector()
        detector.update(1000)
        assert detector.in_battle
        detector.reset()
        assert not detector.in_battle
        assert detector.battle_count == 0

    def test_last_battle_damage_tracks_highest_reading(self):
        detector = BattleDetector()
        detector.update(500)
        detector.update(1200)
        detector.update(2000)
        assert detector.last_battle_damage == 2000
