"""
Layer 3 — The Statistical Executioner (Main Orchestrator)

Transforms high-level Layer 2 scores into pure structural trading signals:
- Entry decision (LONG/SHORT/0)
- Raw Stop Loss (SL)
- Raw Take Profit (TP)

Layer 3 is 100% Kat A pure (stateread-only, deterministic, zero leverage calculation, zero Kat B).
Leverage and Position Sizing are managed exclusively in Layer 4 based on risk capital and SL distance.

Flow:
1. Receive score_history (from Layer 2 time-series)
2. Receive current score
3. Receive price_history
4. Receive current price
5. Derive thresholds from score history (CPD-based)
6. Decide entry based on current score vs thresholds
7. If entry ≠ 0: calculate SL and TP from price regime width
8. Return pure trading signal object
"""

import logging
from typing import Dict, List, Tuple, Any, Optional

import numpy as np

from src.layer3.thresholds import get_score_thresholds
from src.layer3.risk import get_sl_tp
from src.layer3.regime_classifier import classify_regime

logger = logging.getLogger(__name__)


def decide_entry(
    score: float,
    threshold_up: float,
    threshold_down: float,
    regime_type: str = "insufficient_data",
) -> str:
    """
    Convert score to entry decision using regime-adaptive polarity.

    Args:
        score: Current Layer 2 score
        threshold_up: Upper threshold (from CPD analysis)
        threshold_down: Lower threshold (from CPD analysis)
        regime_type: 'trending' | 'mean_reverting' | 'insufficient_data'

    Returns:
        "LONG" | "SHORT" | "0" (neutral)
    """
    if regime_type == "insufficient_data":
        return "0"

    exceeds_up = score > threshold_up
    exceeds_down = score < threshold_down

    if not exceeds_up and not exceeds_down:
        return "0"

    if regime_type == "trending":
        # Breakout logic: score extreme -> continue in same direction
        if exceeds_up and score > 0:
            return "LONG"
        elif exceeds_down and score < 0:
            return "SHORT"
    elif regime_type == "mean_reverting":
        # Reversal logic: score extreme -> reverse direction
        if exceeds_up and score > 0:
            return "SHORT"
        elif exceeds_down and score < 0:
            return "LONG"

    return "0"


def layer3(
    current_score: float,
    score_history: List[float],
    current_price: float,
    price_history: List[float],
    penalty_score: float,
    penalty_price: float,
    high_history: Optional[List[float]] = None,
    low_history: Optional[List[float]] = None,
    score_model: str = "rbf",
    price_model: str = "rbf",
    price_cpd_window: Optional[List[float]] = None,
) -> Dict[str, Any]:
    """
    Layer 3 — The Statistical Executioner.

    Translates Layer 2 scores into raw structural trading signals (Kat A)
    adapted to the structural market regime (trending vs mean-reverting).

    Args:
        current_score: Latest score from Layer 2
        score_history: Time-series of scores from Layer 2 (oldest → newest)
        current_price: Current asset price
        price_history: Time-series of asset prices / close prices (oldest → newest)
        penalty_score: CPD penalty for score threshold derivation
        penalty_price: CPD penalty for price regime analysis
        high_history: Optional time-series of high prices for OHLC ATR
        low_history: Optional time-series of low prices for OHLC ATR
        score_model: CPD model type for score analysis (default: "rbf")
        price_model: CPD model type for price analysis (default: "rbf")
        price_cpd_window: Optional price CPD window from L2 for regime classification

    Returns:
        Dictionary with signal details and metadata.
    """
    logger.info(
        f"Layer 3 starting | "
        f"current_score={current_score:.4f}, "
        f"current_price={current_price:.6f}"
    )

    # Validate inputs
    if not score_history or len(score_history) < 3:
        logger.warning(
            f"Score history too short ({len(score_history)}), cannot proceed"
        )
        return _neutral_signal(current_price)

    # Cap score history at 500 points to mimic production window limit and speed up CPD
    if len(score_history) > 500:
        score_history = score_history[-500:]

    if not price_history or len(price_history) < 2:
        logger.warning(
            f"Price history too short ({len(price_history)}), cannot proceed"
        )
        return _neutral_signal(current_price)

    # Cap price history at 250 points to focus on recent regime structure and prevent drift
    if len(price_history) > 250:
        price_history = price_history[-250:]
        if high_history is not None:
            high_history = high_history[-250:]
        if low_history is not None:
            low_history = low_history[-250:]

    try:
        # Step 1: Derive thresholds from score distribution
        threshold_up, threshold_down = get_score_thresholds(
            score_history, penalty_score, model=score_model
        )
        logger.debug(
            f"Score thresholds derived: UP={threshold_up:.4f}, DOWN={threshold_down:.4f}"
        )

        # Step 2: Regime Classification
        regime_type = "insufficient_data"
        if price_cpd_window is not None:
            regime_type = classify_regime(price_cpd_window)
        logger.debug(f"Regime type classified: {regime_type}")

        # Step 3: Decide entry based on score and regime
        entry = decide_entry(current_score, threshold_up, threshold_down, regime_type=regime_type)
        logger.info(f"Entry decision: {entry} (regime={regime_type})")

        # Step 4: If neutral, return neutral signal
        if entry == "0":
            logger.info("Entry is 0 (neutral), returning neutral signal")
            metadata = {
                "threshold_up": threshold_up,
                "threshold_down": threshold_down,
                "score": current_score,
                "score_z": _calculate_score_z(score_history, current_score),
                "price": current_price,
                "regime_volatility": _calculate_regime_volatility(
                    price_history, penalty_price
                ),
                "regime_type": regime_type,
            }
            return {
                "entry": "0",
                "entry_price": current_price,
                "stop_loss": None,
                "take_profit": None,
                "risk_reward_ratio": None,
                "metadata": metadata,
            }

        # Step 4: Calculate SL/TP for LONG or SHORT
        sl, tp = get_sl_tp(
            price_history,
            direction=entry,
            high_history=high_history,
            low_history=low_history,
        )

        if sl is None:
            logger.warning(f"Failed to calculate SL for {entry}, returning neutral")
            return _neutral_signal(current_price)

        # Validate current price is in a viable position relative to SL/TP
        if entry == "LONG" and (current_price <= sl or (tp is not None and current_price >= tp)):
            logger.warning(
                f"LONG entry rejected: current_price={current_price:.6f} "
                f"beyond SL={sl:.6f}"
            )
            return _neutral_signal(current_price)
        if entry == "SHORT" and (current_price >= sl or (tp is not None and current_price <= tp)):
            logger.warning(
                f"SHORT entry rejected: current_price={current_price:.6f} "
                f"beyond SL={sl:.6f}"
            )
            return _neutral_signal(current_price)

        # Step 5: Calculate risk-reward ratio (None if tp is None)
        if entry == "LONG":
            risk = abs(current_price - sl)
            reward = abs(tp - current_price) if tp is not None else None
        else:  # SHORT
            risk = abs(current_price - sl)
            reward = abs(current_price - tp) if tp is not None else None

        if risk > 0 and reward is not None:
            rr_ratio = reward / risk
        else:
            rr_ratio = None

        # Build pure output signal
        signal = {
            "entry": entry,
            "entry_price": current_price,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_reward_ratio": rr_ratio,
            "metadata": {
                "threshold_up": threshold_up,
                "threshold_down": threshold_down,
                "score": current_score,
                "score_z": _calculate_score_z(score_history, current_score),
                "price": current_price,
                "regime_volatility": _calculate_regime_volatility(
                    price_history, penalty_price
                ),
                "regime_type": regime_type,
            },
        }

        tp_str = f"{tp:.6f}" if tp is not None else "None"
        rr_str = f"{rr_ratio:.2f}" if rr_ratio is not None else "N/A"
        logger.info(
            f"Layer 3 pure signal generated: "
            f"{entry} @ {current_price:.6f}, "
            f"SL={sl:.6f}, "
            f"TP={tp_str}, "
            f"RR={rr_str}"
        )

        return signal

    except Exception as e:
        logger.error(f"Layer 3 failed: {e}")
        return _neutral_signal(current_price)


def _neutral_signal(price: float) -> Dict[str, Any]:
    """Return a safe neutral signal when Layer 3 cannot decide."""
    return {
        "entry": "0",
        "entry_price": price,
        "stop_loss": None,
        "take_profit": None,
        "risk_reward_ratio": None,
        "metadata": {
            "threshold_up": None,
            "threshold_down": None,
            "score": None,
            "score_z": None,
            "price": price,
            "regime_volatility": None,
        },
    }


def _calculate_score_z(score_history: List[float], current_score: float) -> float:
    """
    Calculate how many standard deviations the current score is
    from the mean of the score history.
    """
    try:
        if not score_history or len(score_history) < 2:
            return 0.0

        mean = float(np.mean(score_history))
        std = float(np.std(score_history))

        if std == 0:
            return 0.0

        z = (current_score - mean) / std
        return float(z)
    except Exception as e:
        logger.warning(f"Failed to calculate score Z: {e}")
        return 0.0


def _calculate_regime_volatility(price_history: List[float], penalty: float) -> float:
    """
    Calculate volatility within the CPD regime.
    """
    from src.layer2.cpd import get_cpd_window

    try:
        if not price_history or len(price_history) < 2:
            return 0.0

        window, _ = get_cpd_window(price_history, penalty)

        if not window or len(window) < 2:
            return 0.0

        window_arr = np.array(window, dtype=float)

        if np.any(window_arr <= 0):
            logger.warning("Price data contains zero or negative values in regime volatility")
            return 0.0

        returns = np.diff(np.log(window_arr))
        vol = float(np.std(returns))
        return vol
    except Exception as e:
        logger.warning(f"Failed to calculate regime volatility: {e}")
        return 0.0
