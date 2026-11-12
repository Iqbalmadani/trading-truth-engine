"""
Layer 2 Aggregator — The Main Function

Orchestrates CPD + Z-score calculation across all active scopes to produce
a single aggregated score.

Flow:
1. Get active scopes from Layer 1 results
2. For each active scope:
   a. Fetch historical data
   b. Get penalty from penalty_manager
   c. Apply CPD to find recent regime
   d. Calculate z-score within that regime
3. Sum all z-scores
4. Return score + metadata
"""

import logging
from typing import Dict, List, Tuple, Any

import numpy as np

from src.layer2.cpd import get_cpd_window
from src.layer2.zscore import zscore_in_window
from src.layer2.penalty_manager import PenaltyManager
from src.utils.config_loader import get_sources_config

logger = logging.getLogger(__name__)


def layer2(
    connector,
    exchange: str,
    layer1_results: Dict[str, int],
    pair: str,
    timeframe: str,
    market_type: str,
    penalty_manager: PenaltyManager,
    lookback_periods: int = 50,
) -> Tuple[float, Dict[str, Any]]:
    """
    Layer 2 — The Structural Analyzer.

    Transforms multi-source existence check results (from Layer 1) into a single score
    using CPD + Z-score analysis.

    Args:
        connector: Exchange connector (Binance or Gate.io)
        exchange: Exchange id for DB namespace (must match connector.exchange_id)
        layer1_results: Dict from Layer 1 {scope_name: 0 or 1}
        pair: Trading pair (e.g., "ETH_USDT")
        timeframe: Candlestick timeframe (e.g., "1h")
        market_type: Market type ("perpetual", "spot", etc.)
        penalty_manager: PenaltyManager instance for managing CPD penalties
        lookback_periods: Number of candles to fetch for analysis (default: 50)

    Returns:
        Tuple: (score, metadata)
        - score: float - aggregated z-score (can be +/-, 0)
        - metadata: dict with details (active_scopes, penalties_used, etc.)

    Constitution Compliance:
    ✅ No weights from humans (z-scores embody magnitude)
    ✅ Penalties from meta-learning (not hardcoded)
    ✅ No manual window sizes (CPD determines regime)
    ✅ Sum aggregation (simple, mathematical, no subjective weighting)
    """
    exchange_key = str(exchange).strip().lower()
    if hasattr(connector, "exchange_id") and connector.exchange_id != exchange_key:
        logger.warning(
            f"layer2 exchange={exchange_key} differs from "
            f"connector.exchange_id={connector.exchange_id}"
        )

    sources_config = get_sources_config()
    scopes = sources_config["scopes"]

    # Identify active scopes (those that passed Layer 1 existence check)
    active_scopes = [name for name, value in layer1_results.items() if value == 1]

    logger.info(
        f"Layer 2 starting | {len(active_scopes)} active scopes out of {len(layer1_results)}"
    )

    if not active_scopes:
        logger.warning("All scopes are inactive (all Layer 1 = 0), returning score = 0")
        metadata = {
            "active_scopes": [],
            "num_active": 0,
            "z_scores": {},
            "penalties_used": {},
            "timestamp": None,
            "score": 0.0,
            "aggregation_method": "sum",
        }
        return 0.0, metadata

    # Aggregate z-scores from active scopes
    z_scores = {}
    penalties_used = {}
    cpd_window_sizes = {}
    cpd_change_points = {}

    logger.info(f"Layer 2 DEBUG: Processing {len(active_scopes)} scopes...")

    price_cpd_window = None

    for scope_name in active_scopes:
        logger.debug(f"Processing scope: {scope_name}")

        try:
            # Auto-detect data_type from sources.yaml config
            scope_config = scopes.get(scope_name, {})
            data_type = scope_config.get("data_type", "historical")

            if data_type == "snapshot":
                # Scope only has current value from API
                # connector.fetch_history() already appends to snapshot_history
                # We just need to call it (to trigger the write) then read the full history
                connector.fetch_history(
                    scope_name, pair, timeframe, market_type, lookback_periods
                )
                from src.utils.history_manager import get_snapshot_history
                data_history = get_snapshot_history(
                    scope_name, pair=pair, timeframe=timeframe, exchange=exchange_key
                )
            else:
                # data_type == "historical"
                # API returns full time-series directly
                data_history = connector.fetch_history(
                    scope_name, pair, timeframe, market_type, lookback_periods
                )

            logger.debug(f"Layer 2 DEBUG [{scope_name}]: Fetched {len(data_history) if data_history else 0} data points")

            if not data_history or len(data_history) < 2:
                logger.warning(
                    f"Insufficient data for {scope_name} "
                    f"({len(data_history) if data_history else 0} points), skipping"
                )
                z_scores[scope_name] = 0.0
                continue

            # Get penalty for this scope
            penalty = penalty_manager.get_penalty(scope_name)
            cpd_model = penalty_manager.get_model(scope_name, default="rbf")
            penalties_used[scope_name] = penalty
            logger.debug(f"Using penalty={penalty}, model={cpd_model} for {scope_name}")

            # Apply CPD to find recent regime (returns window + change point count)
            cpd_window, num_change_points = get_cpd_window(data_history, penalty, model=cpd_model)
            cpd_window_sizes[scope_name] = len(cpd_window)
            cpd_change_points[scope_name] = num_change_points
            
            if scope_name == "ohlcv":
                price_cpd_window = cpd_window

            logger.info(
                f"Layer 2 DEBUG [{scope_name}]: CPD window size = {len(cpd_window)}, "
                f"change_points = {cpd_change_points[scope_name]} "
                f"(from {len(data_history)} total points)"
            )

            # Get current value (last data point)
            current_value = data_history[-1]

            # Exclude current value from reference window before computing z-score.
            # Including current_value in the window it's measured against
            # dampens its own z-score by ~41% (self-inclusion bias).
            # This is like measuring "how tall are you vs class average"
            # but including yourself in the average calculation.
            if len(cpd_window) > 2:
                reference_window = cpd_window[:-1]
            else:
                reference_window = cpd_window

            # Calculate z-score within reference window (excluding current)
            z = zscore_in_window(reference_window, current_value)
            z_scores[scope_name] = z

            logger.info(
                f"Layer 2 DEBUG [{scope_name}]: z-score = {z:.4f}, "
                f"current_value = {current_value:.6f}, "
                f"ref_window_size = {len(reference_window)}, "
                f"window_mean = {np.mean(reference_window):.6f}, "
                f"window_std = {np.std(reference_window):.6f}"
            )

        except Exception as e:
            logger.error(f"Failed to process {scope_name}: {e}")
            z_scores[scope_name] = 0.0
            continue

    # Aggregate all z-scores using sum (no manual weights)
    total_score = sum(z_scores.values())

    logger.info(
        f"Layer 2 DEBUG: All z-scores = {z_scores} | sum = {total_score:.4f}"
    )

    # Calculate aggregate CPD metrics from tracking (no recomputation)
    avg_window_size = int(np.mean(list(cpd_window_sizes.values()))) if cpd_window_sizes else 0
    total_change_points = sum(cpd_change_points.values()) if cpd_change_points else 0

    # Build metadata
    metadata = {
        "active_scopes": active_scopes,
        "num_active": len(active_scopes),
        "z_scores": z_scores,
        "penalties_used": penalties_used,
        "score": total_score,
        "price_cpd_window": price_cpd_window,
        "aggregation_method": "sum",  # Always sum (no weights)
        "cpd_info": {
            "penalty_used": list(penalties_used.values())[0] if penalties_used else 0,
            "penalties_per_scope": penalties_used,
            "window_size": avg_window_size,
            "window_size_per_scope": cpd_window_sizes,
            "change_points": total_change_points,
            "change_points_per_scope": cpd_change_points,
            # Classification is diagnostic metadata only — L3 determines actual direction via CPD
            # Using sign of score (no hardcoded threshold)
            "regime_classification": "BEARISH" if total_score < 0 else ("BULLISH" if total_score > 0 else "NEUTRAL"),
        },
    }

    logger.info(
        f"Layer 2 complete | "
        f"active_scopes={active_scopes} | "
        f"score={total_score:.4f}"
    )

    return total_score, metadata

