"""
Layer 3 — The Statistical Executioner (Kat A Pure)

Converts Layer 2 score into structural trading decisions (LONG/SHORT/0)
with Stop Loss and Take Profit levels from natural price regimes.

Modules:
- thresholds.py: Threshold derivation from score distribution (CPD-based)
- risk.py: SL and TP calculation from price structure
- executor.py: Main orchestrator (layer3) and entry decision logic
- meta_learning.py: Meta-learning for score + price penalties (Kategori A)
- penalty_manager.py: Penalty caching and lifecycle management
"""

from src.layer3.executor import layer3
from src.layer3.thresholds import get_score_thresholds, InsufficientScoreHistoryError
from src.layer3.risk import get_sl_tp
from src.layer3.meta_learning import (
    meta_learn_score_penalty,
    meta_learn_price_penalty,
    meta_learn_all_layer3_penalties,
)
from src.layer3.penalty_manager import PenaltyManagerLayer3

__all__ = [
    "layer3",
    "get_score_thresholds",
    "InsufficientScoreHistoryError",
    "get_sl_tp",
    "meta_learn_score_penalty",
    "meta_learn_price_penalty",
    "meta_learn_all_layer3_penalties",
    "PenaltyManagerLayer3",
]
