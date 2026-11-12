"""
Penalty Manager Module for Layer 2

Manages CPD penalty computation and caching using SQLite database.

Strategy:
- Every pipeline run: meta-learning computes fresh penalties from current data
- Penalties saved to SQLite for namespace isolation (exchange/pair/timeframe)
- No time-based refresh — data drives recomputation, not human interval
- Cached values used only as fallback if meta-learning fails

Constitution Compliance:
  ✅ No time-based refresh interval (was 7 days — removed)
  ✅ Penalties computed fresh from data every run
  ✅ No stale penalty risk — always reflects current data structure
  ✅ Namespace isolation per exchange/pair/timeframe
"""

import logging
from typing import Dict, Optional

from src.utils import database as db

logger = logging.getLogger(__name__)


class PenaltyManager:
    """
    Manages penalty caching, loading, and refresh logic.

    All penalties are namespaced by (exchange, pair, timeframe).
    """

    def __init__(self, exchange: str, pair: str | None = None,
                 timeframe: str | None = None):
        self.exchange = exchange
        self.pair = pair
        self.timeframe = timeframe
        self.penalties: Dict[str, float] = {}
        self.models: Dict[str, str] = {}

        # Load cached penalties if namespace is known
        if pair and timeframe:
            self._load_cached_penalties()

    def _load_cached_penalties(self) -> None:
        """Load penalties from SQLite for current namespace."""
        if not self.pair or not self.timeframe:
            return

        all_penalties = db.get_all_penalties(
            self.exchange, self.pair, self.timeframe, "l2"
        )

        if all_penalties:
            self.penalties = {
                scope: data["penalty"] for scope, data in all_penalties.items()
            }
            self.models = {
                scope: data["model"] for scope, data in all_penalties.items()
            }
            logger.info(
                f"Loaded {len(self.penalties)} cached L2 penalties "
                f"({self.exchange}/{self.pair}/{self.timeframe})"
            )
        else:
            logger.info(
                f"No cached L2 penalties found for "
                f"{self.exchange}/{self.pair}/{self.timeframe}. "
                f"Meta-learning will be run."
            )

    def should_refresh(self) -> bool:
        """
        Always returns True — penalties are recomputed fresh every pipeline run.

        This ensures L2 always uses penalties derived from current data structure,
        not stale cached values from days ago.

        Returns:
            Always True (fresh computation every run)
        """
        logger.debug("Fresh penalty computation requested (every run)")
        return True

    def get_penalty(self, scope_name: str, default: float = 1.0) -> float:
        """
        Retrieve penalty for a specific scope.

        Args:
            scope_name: Name of the scope (e.g., "ohlcv", "funding_rate")
            default: Default penalty if not found in cache

        Returns:
            Penalty value for the scope
        """
        if scope_name in self.penalties:
            return self.penalties[scope_name]

        logger.warning(
            f"Penalty for {scope_name} not found in cache, using default: {default}"
        )
        return default

    def get_all_penalties(self) -> Dict[str, float]:
        """
        Get all cached penalties.

        Returns:
            Dict of {scope_name: penalty}
        """
        return self.penalties.copy()

    def get_model(self, scope_name: str, default: str = "rbf") -> str:
        """
        Retrieve CPD model type for a scope.
        Returns default if not found (backward compatible).

        Args:
            scope_name: Name of the scope (e.g., "ohlcv", "funding_rate")
            default: Default model if not found in cache

        Returns:
            Model name for the scope
        """
        return self.models.get(scope_name, default)

    def refresh_penalties(
        self, new_penalties: Dict[str, float],
        new_models: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Refresh penalties and models with new values from meta-learning.

        Args:
            new_penalties: Dict of {scope_name: penalty} from meta-learning
            new_models: Dict of {scope_name: model} from meta-learning (optional)
        """
        self.penalties = new_penalties.copy()
        if new_models:
            self.models = new_models.copy()

        # Save to SQLite (namespaced)
        if self.pair and self.timeframe:
            db.save_all_penalties(
                self.exchange, self.pair, self.timeframe,
                "l2", self.penalties, self.models
            )

        logger.info("L2 penalties refreshed and saved to database")

    def force_refresh_if_needed(
        self, connector, pair: str, timeframe: str, market_type: str
    ) -> bool:
        """
        Check if refresh is needed and optionally perform it.

        This is a convenience method - typically called before layer2() execution.

        Args:
            connector: Exchange connector (for fetching fresh data)
            pair: Trading pair
            timeframe: Candlestick timeframe
            market_type: Market type

        Returns:
            True if refresh was performed, False if penalties were fresh
        """
        # Update namespace
        self.pair = pair
        self.timeframe = timeframe

        # Reload cached penalties for this namespace
        self._load_cached_penalties()

        if not self.should_refresh():
            logger.debug("Penalties are fresh, no refresh needed")
            return False

        logger.info("Performing automatic penalty refresh...")

        try:
            from src.layer2.meta_learning import meta_learn_all_scopes

            result = meta_learn_all_scopes(
                connector, self.exchange, pair, timeframe, market_type
            )
            # result is now {"penalties": {...}, "models": {...}}
            new_penalties = result.get("penalties", {})
            new_models = result.get("models", {})
            self.refresh_penalties(new_penalties, new_models)
            return True

        except Exception as e:
            logger.error(f"Auto-refresh failed: {e}. Using existing penalties.")
            return False
