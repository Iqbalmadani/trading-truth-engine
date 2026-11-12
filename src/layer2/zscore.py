"""
Z-score Calculation Module

Calculates normalized z-scores within a given window.
Z-score tells us how many standard deviations away from the mean a value is.

For Layer 2: We calculate z-scores within the CPD-determined window,
NOT with a fixed historical window. This respects data-driven regimes.
"""

import logging
from typing import List, Union

import numpy as np

logger = logging.getLogger(__name__)


def zscore_in_window(
    series: List[float], current_value: float, epsilon: float = 1e-9
) -> float:
    """
    Calculate z-score of current_value within the given series window.

    Args:
        series: Reference window (e.g., from CPD) - should be ordered oldest to newest
        current_value: The value to calculate z-score for
        epsilon: Small constant to avoid division by zero

    Returns:
        Float z-score. Can be positive (above mean), negative (below mean), or 0 (no volatility)

    Formula:
        z = (x - μ) / σ

    Edge cases:
        - If series is empty: return 0 (no reference)
        - If std = 0 (no volatility): return 0 (no signal from constant data)
    """
    if not series:
        logger.warning("Empty series for z-score calculation, returning 0")
        return 0.0

    try:
        series_array = np.array(series, dtype=float)

        # Calculate mean and standard deviation
        mean = np.mean(series_array)
        std = np.std(series_array)

        # If no volatility (all values the same), no meaningful signal
        if std < epsilon:
            logger.debug(
                f"Zero volatility in series (std={std}), "
                f"returning z-score=0"
            )
            return 0.0

        # Calculate and return z-score
        z_score = (current_value - mean) / std

        return float(z_score)

    except Exception as e:
        logger.error(f"Z-score calculation failed: {e}")
        return 0.0


def normalize_zscore_range(
    z_score: float, hard_limit: float = 5.0
) -> float:
    """
    Optionally cap extreme z-scores for monitoring purposes (not for logic).

    This is NO LONGER USED in Layer 2 logic - included for monitoring/debugging only.
    We keep z-scores as raw because they already contain direction and magnitude.

    Args:
        z_score: Raw z-score
        hard_limit: Cap for logging/monitoring (values beyond this are logged as extreme)

    Returns:
        Same z-score (unchanged)
    """
    if abs(z_score) > hard_limit:
        logger.warning(f"Extreme z-score detected: {z_score} (exceeds ±{hard_limit})")
    return z_score
