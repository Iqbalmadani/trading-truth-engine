"""
Meta-Learning Module for Layer 3 CPD Penalties

Selects optimal CPD penalties for:
1. Score distribution (threshold derivation)
2. Price regime (SL/TP and leverage calculations)

Shares core algorithms (BIC candidates, Calinski-Harabasz, find_optimal)
with Layer 2 meta-learning — single source of truth.

L3-specific functions:
- meta_learn_score_penalty(): For score threshold detection
- meta_learn_price_penalty(): For price regime detection
- meta_learn_all_layer3_penalties(): Convenience wrapper for both
"""

import logging
from typing import List, Dict, Tuple, Optional

import numpy as np

from src.layer2.cpd import get_cpd_window

# DRY: Import shared CPD selection algorithms from Layer 2.
# These functions are mathematically identical across layers:
# - compute_penalty_candidates: BIC-scaled penalty generation
# - calculate_calinski_harabasz: Segmentation quality metric
# - find_optimal_penalty: Grid search over penalties × models
#
# Having two copies caused divergence risk: a bug fix in L2
# would not automatically apply to L3 (and vice versa).
from src.layer2.meta_learning import (
    compute_penalty_candidates,
    calculate_calinski_harabasz,
    find_optimal_penalty,
)

logger = logging.getLogger(__name__)


def meta_learn_score_penalty(
    score_history: List[float],
    penalty_candidates: Optional[List[float]] = None,
) -> Tuple[float, str, float, bool]:
    """
    Find optimal CPD penalty and model for score distribution analysis.

    This penalty is used in Layer 3 Stage 1 (threshold derivation)
    to detect regimes in the score history.

    Args:
        score_history: Time-series of Layer 2 scores
        penalty_candidates: List of penalties to test (default: BIC-scaled)

    Returns:
        Tuple: (best_penalty, best_model, ch_score, is_significant)
    """
    logger.info("Meta-learning for score distribution penalty")

    if not score_history or len(score_history) < 3:
        logger.warning("Insufficient score history for meta-learning")
        candidates = compute_penalty_candidates(score_history) if score_history else [1.0]
        mid_idx = len(candidates) // 2
        return candidates[mid_idx], "rbf", 0.0, False

    penalty, model, ch_score, is_significant = find_optimal_penalty(score_history, penalty_candidates)
    logger.info(
        f"Score penalty: {penalty:.6f}, model: {model} (CH={ch_score:.4f}, significant={is_significant})"
    )
    return penalty, model, ch_score, is_significant


def meta_learn_price_penalty(
    price_history: List[float],
    penalty_candidates: Optional[List[float]] = None,
) -> Tuple[float, str, float, bool]:
    """
    Find optimal CPD penalty and model for price regime analysis.

    This penalty is used in Layer 3 Stages 3 & 4 (SL/TP and leverage calculations)
    to detect price regimes for natural support/resistance levels.

    Args:
        price_history: Time-series of asset prices
        penalty_candidates: List of penalties to test (default: BIC-scaled)

    Returns:
        Tuple: (best_penalty, best_model, ch_score, is_significant)
    """
    logger.info("Meta-learning for price regime penalty")

    if not price_history or len(price_history) < 3:
        logger.warning("Insufficient price history for meta-learning")
        candidates = compute_penalty_candidates(price_history) if price_history else [1.0]
        mid_idx = len(candidates) // 2
        return candidates[mid_idx], "rbf", 0.0, False

    penalty, model, ch_score, is_significant = find_optimal_penalty(price_history, penalty_candidates)
    logger.info(
        f"Price penalty: {penalty:.6f}, model: {model} (CH={ch_score:.4f}, significant={is_significant})"
    )
    return penalty, model, ch_score, is_significant


def meta_learn_all_layer3_penalties(
    score_history: List[float],
    price_history: List[float],
    penalty_candidates: Optional[List[float]] = None,
) -> Dict:
    """
    Run meta-learning for both Layer 3 penalties and models.

    Args:
        score_history: Layer 2 score time-series
        price_history: Asset price time-series
        penalty_candidates: List of penalties to evaluate (default: BIC-scaled per series)

    Returns:
        Dict with two keys:
        - "penalties": {"score": float, "price": float}
        - "models": {"score": str, "price": str}
    """
    penalties = {}
    models = {}

    # Meta-learn score penalty and model
    score_penalty, score_model, score_ch, score_sig = meta_learn_score_penalty(
        score_history, penalty_candidates
    )
    penalties["score"] = score_penalty
    models["score"] = score_model

    # Meta-learn price penalty and model
    price_penalty, price_model, price_ch, price_sig = meta_learn_price_penalty(
        price_history, penalty_candidates
    )
    penalties["price"] = price_penalty
    models["price"] = price_model

    logger.info(
        f"Layer 3 meta-learning complete: "
        f"score_penalty={score_penalty:.6f} model={score_model} (CH={score_ch:.4f}, sig={score_sig}), "
        f"price_penalty={price_penalty:.6f} model={price_model} (CH={price_ch:.4f}, sig={price_sig})"
    )

    return {"penalties": penalties, "models": models}
