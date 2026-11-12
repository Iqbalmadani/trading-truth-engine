"""
Layer 2 — The Structural Analyzer

Transforms multi-source time-series data (that passed Layer 1 existence check)
into a single aggregated score using Change Point Detection (CPD) + Z-score analysis.

Constitution:
- No weights from humans (z-scores embody magnitude)
- No hardcoded thresholds (penalties from meta-learning)
- Penalties selected via Calinski-Harabasz index, not profit
- BIC-scaled penalty candidates from data variance
"""

from src.layer2.cpd import get_cpd_window, get_all_change_points
from src.layer2.zscore import zscore_in_window
from src.layer2.meta_learning import (
    compute_penalty_candidates,
    calculate_calinski_harabasz,
    find_optimal_penalty,
    meta_learn_all_scopes,
)
from src.layer2.penalty_manager import PenaltyManager
from src.layer2.aggregator import layer2

__all__ = [
    "get_cpd_window",
    "get_all_change_points",
    "zscore_in_window",
    "compute_penalty_candidates",
    "calculate_calinski_harabasz",
    "find_optimal_penalty",
    "meta_learn_all_scopes",
    "PenaltyManager",
    "layer2",
]
