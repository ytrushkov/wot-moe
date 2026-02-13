"""Detect battle start/end from damage counter changes."""

import logging
import time

logger = logging.getLogger(__name__)


class BattleDetector:
    """Detects battle transitions by monitoring damage counter values.

    A new battle is detected when:
        - Damage was previously nonzero
        - Damage drops to zero
        - A minimum time gap has elapsed (to avoid false positives from brief glitches)

    Args:
        reset_gap_seconds: Minimum time between last nonzero reading and zero
            reading to consider it a new battle (not a glitch).
        zero_frames_required: Number of consecutive zero-damage frames required
            to confirm a battle reset.
    """

    def __init__(
        self,
        reset_gap_seconds: float = 3.0,
        zero_frames_required: int = 3,
    ) -> None:
        self.reset_gap_seconds = reset_gap_seconds
        self.zero_frames_required = zero_frames_required

        self._last_nonzero_damage: int = 0
        self._last_nonzero_time: float = 0.0
        self._consecutive_zeros: int = 0
        self._in_battle: bool = False
        self._battle_count: int = 0

    @property
    def in_battle(self) -> bool:
        return self._in_battle

    @property
    def battle_count(self) -> int:
        return self._battle_count

    def update(self, damage: int) -> str:
        """Feed a new damage reading and return the detected state.

        Args:
            damage: Current combined damage value from OCR.

        Returns:
            One of:
            - "battle_active": Ongoing battle with damage being dealt.
            - "battle_ended": Battle just ended (damage reset detected). The
              caller should finalize the previous battle's damage.
            - "idle": No battle detected (waiting for activity).
        """
        now = time.monotonic()

        if damage > 0:
            self._consecutive_zeros = 0
            self._last_nonzero_damage = damage
            self._last_nonzero_time = now

            if not self._in_battle:
                self._in_battle = True
                self._battle_count += 1
                logger.info("Battle #%d started (damage: %d)", self._battle_count, damage)

            return "battle_active"

        # damage == 0
        self._consecutive_zeros += 1

        if not self._in_battle:
            return "idle"

        # Check if enough zero frames and time gap to confirm battle end
        time_since_nonzero = now - self._last_nonzero_time
        if (
            self._consecutive_zeros >= self.zero_frames_required
            and time_since_nonzero >= self.reset_gap_seconds
        ):
            self._in_battle = False
            logger.info(
                "Battle #%d ended (final damage: %d)",
                self._battle_count,
                self._last_nonzero_damage,
            )
            return "battle_ended"

        # Still in cooldown, treat as active
        return "battle_active"

    @property
    def last_battle_damage(self) -> int:
        """The last nonzero damage reading (final damage of the ended battle)."""
        return self._last_nonzero_damage

    def reset(self) -> None:
        """Reset all state."""
        self._last_nonzero_damage = 0
        self._last_nonzero_time = 0.0
        self._consecutive_zeros = 0
        self._in_battle = False
        self._battle_count = 0
