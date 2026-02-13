"""Exponential Moving Average for Marks of Excellence calculation.

MoE in World of Tanks uses an EMA over approximately 100 battles:
    new_ema = old_ema * (1 - alpha) + battle_damage * alpha

Where alpha = 2 / (N + 1), N ~ 100 battles, so alpha ~ 0.0198.
"""

# Default EMA period (number of battles in the moving window)
DEFAULT_PERIOD = 100

# Default alpha derived from the period
DEFAULT_ALPHA = 2.0 / (DEFAULT_PERIOD + 1)


def compute_ema_update(current_ema: float, battle_damage: float, alpha: float = DEFAULT_ALPHA) -> float:
    """Compute the new EMA after a single battle.

    Args:
        current_ema: The current EMA value before this battle.
        battle_damage: Combined damage dealt in this battle.
        alpha: Smoothing factor. Default is 2/101.

    Returns:
        Updated EMA value.
    """
    return current_ema * (1.0 - alpha) + battle_damage * alpha


def project_ema(
    current_ema: float,
    hypothetical_damage: float,
    alpha: float = DEFAULT_ALPHA,
) -> float:
    """Project what the EMA would be if the battle ended with the given damage.

    Same formula as compute_ema_update, but used for "what-if" projection
    during a live match.
    """
    return compute_ema_update(current_ema, hypothetical_damage, alpha)


def battles_to_target(
    current_ema: float,
    target_ema: float,
    avg_damage: float,
    alpha: float = DEFAULT_ALPHA,
    max_battles: int = 500,
) -> int | None:
    """Estimate how many battles at avg_damage to reach target_ema.

    Args:
        current_ema: Current EMA value.
        target_ema: Desired EMA target.
        avg_damage: Assumed average combined damage per battle.
        alpha: EMA smoothing factor.
        max_battles: Cap on search iterations.

    Returns:
        Number of battles needed, or None if not reachable within max_battles.
    """
    if avg_damage <= 0:
        return None

    ema = current_ema
    for i in range(1, max_battles + 1):
        ema = compute_ema_update(ema, avg_damage, alpha)
        if ema >= target_ema:
            return i

    return None
