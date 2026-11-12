"""
Penalty Manager for Layer 3

Manages CPD penalties for:
1. Score distribution (threshold derivation)
2. Price regime (SL/TP and leverage)

Storage: SQLite database (data/tte.db)
Namespace: exchange + pair + timeframe

Lifecycle:
- Every pipeline run: meta-learning computes fresh penalties from current data
- Penalties saved to SQLite for namespace isolation
- No time-based refresh — data drives recomputation, not human interval

Constitution Compliance:
  ✅ No time-based refresh interval (was 7 days — removed)
  ✅ Penalties computed fresh from data every run
  ✅ No stale penalty risk — always reflects current data structure
  ✅ Cached values used only as fallback if meta-learning fails
"""

import logging
from typing import Optional

from src.utils import database as db
from src.layer3.meta_learning import meta_learn_all_layer3_penalties

logger = logging.getLogger(__name__)


class PenaltyManagerLayer3:
    """
    Manages Layer 3 penalty computation, caching, and loading.

    Computes fresh penalties every pipeline run using meta-learning.
    Saves results to SQLite for namespace isolation.
    Falls back to cached values only if meta-learning fails.

    Stores two independent penalties:
    - score_penalty: For score distribution CPD
    - price_penalty: For price regime CPD
    """

    def __init__(self, exchange: str, pair: str | None = None,
                 timeframe: str | None = None):
        self.exchange = exchange
        self.pair = pair
        self.timeframe = timeframe
        self.score_penalty: Optional[float] = None
        self.price_penalty: Optional[float] = None
        self.score_model: str = "rbf"
        self.price_model: str = "rbf"

        # Load cached penalties if namespace is known
        if pair and timeframe:
            self._load_cached_penalties()

    def _load_cached_penalties(self) -> None:
        """Load Layer 3 penalties from SQLite as fallback."""
        if not self.pair or not self.timeframe:
            return

        all_penalties = db.get_all_penalties(
            self.exchange, self.pair, self.timeframe, "l3"
        )

        if all_penalties:
            score_data = all_penalties.get("score")
            price_data = all_penalties.get("price")

            if score_data:
                self.score_penalty = score_data["penalty"]
                self.score_model = score_data["model"]
            if price_data:
                self.price_penalty = price_data["penalty"]
                self.price_model = price_data["model"]

            if self.score_penalty and self.price_penalty:
                logger.info(
                    f"Loaded cached L3 penalties: "
                    f"score={self.score_penalty} (model={self.score_model}), "
                    f"price={self.price_penalty} (model={self.price_model}) "
                    f"({self.exchange}/{self.pair}/{self.timeframe})"
                )
            else:
                logger.warning(
                    f"Incomplete L3 penalties for "
                    f"{self.exchange}/{self.pair}/{self.timeframe}"
                )
        else:
            logger.info(
                f"No cached L3 penalties found for "
                f"{self.exchange}/{self.pair}/{self.timeframe}. "
                f"Meta-learning will compute fresh penalties."
            )

    def _save_penalties(
        self, score_penalty: float, price_penalty: float,
        score_model: str = "rbf", price_model: str = "rbf"
    ) -> None:
        """Save Layer 3 penalties to SQLite."""
        if not self.pair or not self.timeframe:
            logger.warning("Cannot save penalties: pair/timeframe not set")
            return

        db.save_penalty(
            self.exchange, self.pair, self.timeframe,
            "l3", "score", score_penalty, score_model
        )
        db.save_penalty(
            self.exchange, self.pair, self.timeframe,
            "l3", "price", price_penalty, price_model
        )

        logger.info(
            f"Saved L3 penalties: score={score_penalty} (model={score_model}), "
            f"price={price_penalty} (model={price_model}) "
            f"({self.exchange}/{self.pair}/{self.timeframe})"
        )

    def get_score_penalty(self) -> float:
        """
        Retrieve score penalty.

        Raises RuntimeError if penalty not available (meta-learning not run).
        No fallback defaults — system fails loudly instead of trading with unjustified value.

        Returns:
            Penalty value for score distribution
        """
        if self.score_penalty is not None:
            return self.score_penalty

        raise RuntimeError(
            "Score penalty not available. "
            "Run calibration first: python scripts/run_calibration.py --once"
        )

    def get_price_penalty(self) -> float:
        """
        Retrieve price penalty.

        Raises RuntimeError if penalty not available (meta-learning not run).
        No fallback defaults — system fails loudly instead of trading with unjustified value.

        Returns:
            Penalty value for price regime
        """
        if self.price_penalty is not None:
            return self.price_penalty

        raise RuntimeError(
            "Price penalty not available. "
            "Run calibration first: python scripts/run_calibration.py --once"
        )

    def get_score_model(self) -> str:
        """
        Retrieve CPD model type for score distribution analysis.
        Backward compatible — defaults to 'rbf' if not set.

        Returns:
            Model name (e.g., 'rbf', 'l2', 'l1')
        """
        return self.score_model

    def get_price_model(self) -> str:
        """
        Retrieve CPD model type for price regime analysis.
        Backward compatible — defaults to 'rbf' if not set.

        Returns:
            Model name (e.g., 'rbf', 'l2', 'l1')
        """
        return self.price_model

    def refresh_penalties(
        self, score_penalty: float, price_penalty: float,
        score_model: str = "rbf", price_model: str = "rbf"
    ) -> None:
        """
        Update penalties and models with new values from meta-learning.

        Args:
            score_penalty: New score penalty value
            price_penalty: New price penalty value
            score_model: New score CPD model type
            price_model: New price CPD model type
        """
        self.score_penalty = score_penalty
        self.price_penalty = price_penalty
        self.score_model = score_model
        self.price_model = price_model
        self._save_penalties(score_penalty, price_penalty, score_model, price_model)
        logger.info("Layer 3 penalties and models refreshed and saved")

    def force_refresh_if_needed(
        self, score_history, price_history
    ) -> bool:
        """
        Always recompute penalties fresh from current data.

        Previous behavior: skip if < 7 days old (human assumption).
        New behavior: always recompute. Data structure may have changed
        since last run. Computational cost is minimal (< 1 second).

        Falls back to cached penalties only if meta-learning fails.

        Args:
            score_history: Layer 2 score history time-series
            price_history: Asset price history time-series

        Returns:
            True if refresh was performed, False if meta-learning failed
        """
        logger.info("Computing fresh Layer 3 penalties from current data...")

        try:
            # Apply window cap to limit PELT computation
            from src.layer2.meta_learning import _apply_window_cap
            if self.timeframe:
                score_history = _apply_window_cap(score_history, self.timeframe)
                price_history = _apply_window_cap(price_history, self.timeframe)

            new_result = meta_learn_all_layer3_penalties(
                score_history, price_history
            )
            new_penalties = new_result["penalties"]
            new_models = new_result["models"]
            self.refresh_penalties(
                new_penalties["score"], new_penalties["price"],
                new_models.get("score", "rbf"), new_models.get("price", "rbf")
            )
            return True

        except Exception as e:
            if self.score_penalty is not None and self.price_penalty is not None:
                logger.warning(
                    f"Fresh computation failed: {e}. "
                    f"Using previously cached penalties as fallback."
                )
            else:
                logger.error(
                    f"Fresh computation failed and no cached penalties available: {e}"
                )
            return False
