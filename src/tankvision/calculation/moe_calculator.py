"""MoE calculation: EMA tracking, percentage projection, delta computation."""

import logging
from dataclasses import dataclass, field

from tankvision.calculation.battle_detector import BattleDetector
from tankvision.calculation.ema import DEFAULT_ALPHA, compute_ema_update, project_ema
from tankvision.ocr.ocr_pipeline import DamageReading

logger = logging.getLogger(__name__)


@dataclass
class MoeState:
    """Current state of MoE tracking, broadcast to the overlay."""

    tank_name: str = ""
    moe_percent: float = 0.0
    projected_moe_percent: float = 0.0
    delta: float = 0.0
    ema: float = 0.0
    target_damage: int = 0
    direct_damage: int = 0
    assisted_damage: int = 0
    combined_damage: int = 0
    battles_this_session: int = 0
    in_battle: bool = False
    status: str = "idle"  # "idle", "battle_active", "battle_ended"

    def to_dict(self) -> dict:
        return {
            "tank_name": self.tank_name,
            "moe_percent": round(self.moe_percent, 2),
            "projected_moe_percent": round(self.projected_moe_percent, 2),
            "delta": round(self.delta, 2),
            "ema": round(self.ema, 1),
            "target_damage": self.target_damage,
            "direct_damage": self.direct_damage,
            "assisted_damage": self.assisted_damage,
            "combined_damage": self.combined_damage,
            "battles_this_session": self.battles_this_session,
            "in_battle": self.in_battle,
            "status": self.status,
        }


class MoeCalculator:
    """Tracks MoE progress across battles using EMA.

    Args:
        current_moe: Starting MoE percentage (from player's service record).
        target_damage: Damage threshold for the current mark target. If 0, projection is skipped.
        ema_alpha: EMA smoothing factor.
        tank_name: Name of the tank being tracked.
    """

    def __init__(
        self,
        current_moe: float = 0.0,
        target_damage: int = 0,
        ema_alpha: float = DEFAULT_ALPHA,
        tank_name: str = "",
    ) -> None:
        self.ema_alpha = ema_alpha
        self.target_damage = target_damage
        self.tank_name = tank_name

        # Derive initial EMA from the starting MoE% and target damage
        # If target_damage is known: ema ≈ (moe% / 100) * target_at_100%
        # Simplified: we store the raw EMA and compute % from threshold
        self._ema = self._moe_to_ema(current_moe) if target_damage > 0 else 0.0
        self._session_start_moe = current_moe
        self._battles_this_session = 0

        # For post-battle API correction: remember EMA before the last update
        # so we can undo the estimated update and redo with corrected damage.
        self._ema_before_last_battle: float | None = None
        self._last_battle_damage: int = 0

        self._detector = BattleDetector()
        self._current_damage = DamageReading(0, 0)

    def _moe_to_ema(self, moe_percent: float) -> float:
        """Convert a MoE percentage to an approximate EMA value."""
        if self.target_damage <= 0:
            return 0.0
        # target_damage is the threshold for the mark the player is chasing.
        # MoE% represents where the player sits relative to the server population.
        # Simplified linear approximation: ema ≈ target_damage * (moe% / 100)
        return self.target_damage * (moe_percent / 100.0)

    def _ema_to_moe(self, ema: float) -> float:
        """Convert an EMA value to an approximate MoE percentage."""
        if self.target_damage <= 0:
            return 0.0
        return min(100.0, max(0.0, (ema / self.target_damage) * 100.0))

    def update(self, reading: DamageReading) -> MoeState:
        """Process a new damage reading and return the current state.

        Args:
            reading: Damage values from OCR.

        Returns:
            Current MoeState for broadcast to the overlay.
        """
        self._current_damage = reading
        battle_status = self._detector.update(reading.combined)

        if battle_status == "battle_ended":
            # Finalize the battle: update EMA with the last known damage
            final_damage = self._detector.last_battle_damage
            self._ema_before_last_battle = self._ema
            self._last_battle_damage = final_damage
            self._ema = compute_ema_update(self._ema, final_damage, self.ema_alpha)
            self._battles_this_session += 1
            logger.info(
                "Battle finalized: damage=%d, new_ema=%.1f, moe=%.2f%%",
                final_damage,
                self._ema,
                self._ema_to_moe(self._ema),
            )

        # Project what MoE would be if battle ended now
        projected_ema = project_ema(self._ema, reading.combined, self.ema_alpha)
        current_moe = self._ema_to_moe(self._ema)
        projected_moe = self._ema_to_moe(projected_ema)

        return MoeState(
            tank_name=self.tank_name,
            moe_percent=current_moe,
            projected_moe_percent=projected_moe,
            delta=projected_moe - self._session_start_moe,
            ema=self._ema,
            target_damage=self.target_damage,
            direct_damage=reading.direct_damage,
            assisted_damage=reading.assisted_damage,
            combined_damage=reading.combined,
            battles_this_session=self._battles_this_session,
            in_battle=self._detector.in_battle,
            status=battle_status,
        )

    def set_target(self, target_damage: int) -> None:
        """Update the target damage threshold (e.g., from API fetch)."""
        self.target_damage = target_damage

    def set_tank(self, tank_name: str, target_damage: int = 0, current_moe: float = 0.0) -> None:
        """Switch to a new tank, resetting battle state."""
        self.tank_name = tank_name
        self.target_damage = target_damage
        self._ema = self._moe_to_ema(current_moe) if target_damage > 0 else 0.0
        self._session_start_moe = current_moe
        self._battles_this_session = 0
        self._ema_before_last_battle = None
        self._last_battle_damage = 0
        self._detector.reset()

    def correct_last_battle(self, corrected_damage: int) -> MoeState | None:
        """Re-compute the last EMA update using corrected damage from the API.

        The in-game HUD shows combined assisted damage (tracking + spotting),
        but WG's MoE formula uses max(tracking, spotting). This causes our
        OCR-based estimate to be inflated. After a battle, the API's cumulative
        stat deltas give us the server-side value, which we use to correct.

        Args:
            corrected_damage: The server-side combined damage for the last battle.

        Returns:
            Updated MoeState reflecting the correction, or None if no battle to correct.
        """
        if self._ema_before_last_battle is None:
            return None

        old_estimated_moe = self._ema_to_moe(self._ema)
        self._ema = compute_ema_update(
            self._ema_before_last_battle, corrected_damage, self.ema_alpha
        )
        corrected_moe = self._ema_to_moe(self._ema)
        self._ema_before_last_battle = None

        logger.info(
            "API correction: ocr_damage=%d, api_damage=%d, moe %.2f%% → %.2f%%",
            self._last_battle_damage,
            corrected_damage,
            old_estimated_moe,
            corrected_moe,
        )

        return self._build_state("idle")

    def set_moe_from_api(self, moe_percent: float) -> None:
        """Override the current EMA with a known MoE percentage from an external source."""
        self._ema = self._moe_to_ema(moe_percent)
        logger.info("MoE set from API: %.2f%% (ema=%.1f)", moe_percent, self._ema)

    @property
    def ema(self) -> float:
        """Current EMA value (for persistence)."""
        return self._ema

    @property
    def current_moe(self) -> float:
        """Current MoE percentage."""
        return self._ema_to_moe(self._ema)

    @property
    def battles_this_session(self) -> int:
        return self._battles_this_session

    def _build_state(self, status: str) -> MoeState:
        """Build a MoeState from current internal state."""
        current_moe = self._ema_to_moe(self._ema)
        return MoeState(
            tank_name=self.tank_name,
            moe_percent=current_moe,
            projected_moe_percent=current_moe,
            delta=current_moe - self._session_start_moe,
            ema=self._ema,
            target_damage=self.target_damage,
            direct_damage=self._current_damage.direct_damage,
            assisted_damage=self._current_damage.assisted_damage,
            combined_damage=self._current_damage.combined,
            battles_this_session=self._battles_this_session,
            in_battle=self._detector.in_battle,
            status=status,
        )
