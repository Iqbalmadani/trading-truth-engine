"""
Change Point Detection (CPD) Module

Uses ruptures.Pelt (PELT: Pruned Exact Linear Time) with Radial Basis Function kernel
to detect natural breakpoints in time-series data. The idea is to find the "regime"
where the current value belongs, rather than using human-defined windows.

Returns the segment from the last change point to present (most recent regime).
"""

import logging
from typing import List

import ruptures as rpt
import numpy as np

logger = logging.getLogger(__name__)


def get_cpd_window(series: List[float], penalty: float, model: str = "rbf") -> tuple:
    """
    Detect change points in the series and return the most recent regime.

    Args:
        series: Time-series data (oldest to newest)
        penalty: CPD penalty parameter (higher = fewer change points, lower = more)
        model: CPD model type ("l2", "rbf", or "l1", default: "rbf")

    Returns:
        Tuple of (window, num_change_points):
        - window: List of values from the last change point to the present (most recent regime)
        - num_change_points: Number of detected change points (excluding endpoint)

    Algorithm:
        1. Apply PELT algorithm with specified kernel
        2. Get change points
        3. Return segment from last change point to end, plus change point count
    """
    if not series:
        return [], 0

    # Convert to numpy array for ruptures
    data = np.array(series, dtype=float)

    # Edge case: series too short for meaningful CPD
    if len(data) < 3:
        logger.debug(f"Series too short ({len(data)} values), returning entire series")
        return series, 0

    try:
        # Initialize PELT algorithm with specified model
        # min_size=3: minimum 3 data points per segment.
        # Proven necessary from 7-day data observation:
        # min_size=1 caused 49/144 scores to be exact
        # integers, indicating unstable z-scores from
        # windows that were too small.
        # This is a technical constraint of the CPD
        # algorithm, not a human choice of window size.
        jump_val = 3 if len(data) > 100 else 1
        algo = rpt.Pelt(model=model, min_size=3, jump=jump_val).fit(data)

        # Predict change points with given penalty
        change_points = algo.predict(pen=penalty)

        # Number of change points (exclude the endpoint which is always len(data))
        num_change_points = len(change_points) - 1

        # change_points is a list ending with len(data)
        # Example: [5, 10, 15, len(data)]
        # The last change point before the full length is the start of current regime
        if len(change_points) > 1:
            last_cp = change_points[-2]  # Second to last (last is always len(data))
        else:
            last_cp = 0

        # Return segment from last change point onwards
        window = series[last_cp:]
        logger.debug(
            f"CPD detected {num_change_points} regime(s), "
            f"current regime starts at index {last_cp}, "
            f"window size: {len(window)}"
        )
        return window, num_change_points

    except Exception as e:
        logger.error(f"CPD failed: {e}. Returning entire series as fallback.")
        return series, 0


def get_all_change_points(series: List[float], penalty: float, model: str = "rbf") -> List[int]:
    """
    Get all detected change points in the series (for debugging/monitoring).

    Args:
        series: Time-series data
        penalty: CPD penalty parameter
        model: CPD model type ("l2", "rbf", or "l1", default: "rbf")

    Returns:
        List of change point indices (0-indexed)
    """
    if not series or len(series) < 3:
        return [len(series)]

    try:
        data = np.array(series, dtype=float)
        jump_val = 3 if len(data) > 100 else 1
        algo = rpt.Pelt(model=model, min_size=3, jump=jump_val).fit(data)
        change_points = algo.predict(pen=penalty)
        return change_points
    except Exception as e:
        logger.warning(f"Failed to detect change points: {e}")
        return [len(series)]
