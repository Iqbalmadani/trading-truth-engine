"""
History Manager — Persistent Score & Snapshot History Storage

Stores Layer 2 scores and snapshot scope values using SQLite database.
No synthetic data, no assumptions — only real Layer 2 outputs.

Storage: data/tte.db (SQLite)
Namespace: exchange + pair + timeframe (fully isolated)

Lifecycle:
1. Initialize → ready to read/write from SQLite
2. After Layer 2 execution → append_score(score)
3. Access via get_score_history()
4. Data persists across pipeline runs, isolated per namespace

Migration from v1:
  - score_history.json → score_history table
  - snapshot_history.json → snapshot_history table
"""

import logging
from typing import List

from src.utils import database as db

logger = logging.getLogger(__name__)

# PELT algorithm requires at least 2 segments to detect a change point.
# With min_size=1 per segment, minimum data points = 3.
# This is a technical constraint of the CPD algorithm, not a human choice.
MINIMUM_HISTORY_FOR_CPD = 3


class HistoryManager:
    """
    Manages persistent storage of Layer 2 scores.

    No synthetic data — only real scores from Layer 2.
    If insufficient history exists, trading is not permitted.

    All data is namespaced by (exchange, pair, timeframe) to prevent
    cross-pair contamination.
    """

    def __init__(self, exchange: str, pair: str | None = None,
                 timeframe: str | None = None):
        """
        Initialize history manager.

        Args:
            exchange: Exchange name (e.g., 'gateio', 'binance')
            pair: Trading pair (e.g., 'ETH_USDT')
            timeframe: Timeframe (e.g., '1h')
        """
        self.exchange = exchange
        self.pair = pair
        self.timeframe = timeframe

    def append_score(self, score: float, pair: str, timeframe: str, candle_timestamp: int | None = None) -> None:
        """
        Append a new Layer 2 score to history.

        Args:
            score: Layer 2 score from aggregator
            pair: Trading pair (e.g., "ETH_USDT")
            timeframe: Timeframe (e.g., "1h")
            candle_timestamp: Timestamp of the candle (optional)
        """
        # Update instance pair/timeframe
        self.pair = pair
        self.timeframe = timeframe

        db.append_score(self.exchange, pair, timeframe, score, candle_timestamp)
        count = db.get_score_count(self.exchange, pair, timeframe)
        logger.info(f"Appended score {score:.4f} to history (total: {count}, timestamp: {candle_timestamp})")

    def get_score_history(self) -> List[float]:
        """
        Get pure score history as list of floats.

        Returns:
            List of scores (oldest → newest), empty if no history
        """
        if not self.pair or not self.timeframe:
            logger.warning("Pair or timeframe not set, returning empty history")
            return []

        return db.get_score_history(self.exchange, self.pair, self.timeframe)

    def get_full_history(self) -> List[dict]:
        """
        Get full score history with timestamps and metadata.

        Note: Returns simplified format since SQLite handles metadata.
        """
        scores = self.get_score_history()
        return [{"score": s} for s in scores]

    def is_sufficient_for_layer3(self) -> bool:
        """
        Check if score history is sufficient for Layer 3 (≥ MINIMUM_HISTORY_FOR_CPD points).

        Returns:
            True if len(scores) >= MINIMUM_HISTORY_FOR_CPD, False otherwise
        """
        if not self.pair or not self.timeframe:
            return False

        count = db.get_score_count(self.exchange, self.pair, self.timeframe)
        is_sufficient = count >= MINIMUM_HISTORY_FOR_CPD

        if not is_sufficient:
            logger.warning(
                f"Insufficient score history for Layer 3: "
                f"{count} scores, need >= 3"
            )
        return is_sufficient

    def clear_history(self) -> None:
        """
        Clear all score history for current namespace.

        Use only for testing or manual reset.
        """
        logger.warning(
            f"Clearing score history for "
            f"{self.exchange}/{self.pair}/{self.timeframe}"
        )
        # Note: For safety, this requires direct SQL execution
        # Not implementing auto-clear to prevent accidental data loss


# ============================================================================
# SNAPSHOT HISTORY FUNCTIONS
# Standalone functions (not class methods) to match the existing API
# that connectors and aggregator call directly.
# ============================================================================

def append_snapshot_value(
    scope: str, value: float, pair: str, timeframe: str,
    exchange: str,
) -> None:
    """
    Append a snapshot value to persistent history.

    Used for scopes that only have current data (not historical):
    - open_interest

    Args:
        scope: Scope name (e.g., "open_interest")
        value: Snapshot value to append
        pair: Trading pair (e.g., "ETH_USDT")
        timeframe: Timeframe (e.g., "1h")
        exchange: Exchange name (e.g. "gateio", "binance") — required
    """
    if not exchange or not str(exchange).strip():
        raise ValueError("exchange is required for snapshot namespace isolation")
    db.append_snapshot(str(exchange).strip().lower(), pair, timeframe, scope, value)


def get_snapshot_history(
    scope: str, pair: str, timeframe: str,
    exchange: str,
) -> List[float]:
    """
    Get accumulated snapshot history for a scope.

    Returns list of values from oldest to newest.
    Returns empty list if no history exists.

    Args:
        scope: Scope name (e.g., "open_interest")
        pair: Trading pair (REQUIRED for namespace isolation)
        timeframe: Timeframe (REQUIRED for namespace isolation)
        exchange: Exchange name (e.g. "gateio", "binance") — required

    Returns:
        List of float values (oldest → newest)
    """
    if not exchange or not str(exchange).strip():
        raise ValueError("exchange is required for snapshot namespace isolation")
    if not pair or not timeframe:
        raise ValueError(
            f"pair and timeframe are required for snapshot history ({scope})"
        )

    return db.get_snapshot_history(
        str(exchange).strip().lower(), pair, timeframe, scope
    )
