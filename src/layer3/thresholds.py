"""
Layer 3.1 — Threshold Derivation

Determines buy/sell thresholds from the distribution of Layer 2 scores.
Thresholds are NOT fixed percentiles (90%, 10%, 5%) nor statistical formulas (mean±σ).
Instead, they're the natural boundaries of the current regime from CPD itself.

Principle:
  - CPD finds natural regimes in the score distribution
  - Thresholds = max and min of the recent regime (no formula, no multiplier)
  - Completely data-driven, zero human judgment

Constitution Compliance:
  ✅ No fixed percentiles (90%, 80%, etc.)
  ✅ No statistical multipliers (mean±1σ, mean±1.5σ, etc.)
  ✅ Thresholds are actual observed boundaries, not formulas
  ✅ Meta-learning via CPD penalty selection
  ✅ No hardcoded fallback thresholds — error on insufficient data
"""

import logging
from typing import List, Tuple

import numpy as np
import ruptures as rpt

logger = logging.getLogger(__name__)


class InsufficientScoreHistoryError(Exception):
    """Raised when score history is too short for threshold derivation."""
    pass


def get_score_thresholds(
    score_history: List[float], penalty: float, model: str = "rbf"
) -> Tuple[float, float]:
    """
    Derive natural thresholds from score distribution using CPD.

    Args:
        score_history: Time-series of Layer 2 scores (oldest → newest)
        penalty: CPD penalty parameter for regime detection
        model: CPD model type (default: "rbf", backward compatible)

    Returns:
        (threshold_up, threshold_down) tuple defining LONG/SHORT/0 regions

    Raises:
        InsufficientScoreHistoryError: if score_history < 3 points

    Algorithm:
        1. Apply PELT algorithm to score_history
        2. Find the most recent regime (segment)
        3. Thresholds = max and min of that recent regime

    This ensures:
        - Thresholds reflect actual observed regime boundaries
        - Zero human multipliers (no mean±σ formulas)
        - No manual percentiles hardcoded
        - NO hardcoded fallback thresholds — error on insufficient data
    """
    # Option B: Error propagation (do not trade without sufficient data)
    if not score_history or len(score_history) < 3:
        error_msg = (
            f"Insufficient score history for threshold derivation: "
            f"{len(score_history) if score_history else 0} samples, need >= 3. "
            f"Cannot proceed with trading signal."
        )
        logger.error(error_msg)
        raise InsufficientScoreHistoryError(error_msg)

    try:
        # Convert to numpy array
        data = np.array(score_history, dtype=float)

        # Apply PELT algorithm with data-selected model (from meta-learning)
        jump_val = 3 if len(data) > 100 else 1
        algo = rpt.Pelt(model=model, min_size=3, jump=jump_val).fit(data)
        change_points = algo.predict(pen=penalty)

        # Extract change points and determine regimes
        if len(change_points) > 1:
            last_cp = change_points[-2]  # Second-to-last (last is always len(data))
        else:
            last_cp = 0

        # Use PREVIOUS regime as reference frame for thresholds.
        #
        # Why previous regime instead of current regime max/min:
        # Using max/min of the CURRENT regime causes monotonic threshold increase:
        # each new extreme score becomes the new threshold, making it progressively
        # harder to generate signals — "silent death" after weeks of operation.
        # (Like a sprint that must break the world record every time.)
        #
        # Using the PREVIOUS regime's bounds as thresholds means:
        # - A score exceeding the old regime's range indicates genuine breakout
        # - Thresholds reset when PELT detects a new regime shift
        # - No monotonic increase — reference frame changes with each regime
        # - Still 100% data-driven (PELT segments determine everything)
        if len(change_points) > 2:
            # Multiple regimes: use the segment before the current one
            prev_cp = change_points[-3]
            reference_scores = score_history[prev_cp:last_cp]
        elif last_cp > 0:
            # One change point: previous regime is everything before it
            reference_scores = score_history[:last_cp]
        else:
            # No change points: one regime, use all scores except current
            reference_scores = score_history[:-1]

        logger.debug(
            f"CPD detected {len(change_points) - 1} regimes, "
            f"using {len(reference_scores)} reference scores from previous regime"
        )

        # Fallback if reference regime is too short
        if len(reference_scores) < 2:
            reference_scores = score_history[:-1]

        if len(reference_scores) < 2:
            raise InsufficientScoreHistoryError(
                "Not enough reference scores to derive threshold"
            )

        threshold_up = float(np.max(reference_scores))
        threshold_down = float(np.min(reference_scores))

        logger.info(
            f"Score thresholds: UP={threshold_up:.4f}, DOWN={threshold_down:.4f} "
            f"(from previous regime, {len(reference_scores)} reference scores)"
        )

        return threshold_up, threshold_down

    except InsufficientScoreHistoryError:
        # Re-raise our own exception
        raise
    except Exception as e:
        error_msg = f"CPD algorithm failed for threshold derivation: {e}"
        logger.error(error_msg)
        raise InsufficientScoreHistoryError(error_msg)
