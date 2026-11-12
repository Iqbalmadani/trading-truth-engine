"""
Layer 3.2 — Risk Management (Stop Loss & Take Profit)

Determines Stop Loss and Take Profit levels based on ATR Trailing logic.

Principles:
  - Initial SL derived from ATR (Average True Range) * multiplier (default 1.5x)
  - For LONG: initial SL = entry_price - (trail_mult * ATR), TP = None (dynamic trailing in L4)
  - For SHORT: initial SL = entry_price + (trail_mult * ATR), TP = None (dynamic trailing in L4)
  - Trailing stop updates dynamically as price moves in favor of the position.

Constitution Compliance:
  ✅ All parameters derived from statistical market volatility (ATR)
  ✅ Dynamic exit execution without arbitrary fixed target caps
"""

import logging
from typing import List, Tuple, Optional

import numpy as np

logger = logging.getLogger(__name__)


def compute_atr(
    price_history: List[float],
    period: int = 14,
    high_history: Optional[List[float]] = None,
    low_history: Optional[List[float]] = None,
) -> Optional[float]:
    """
    Calculate Average True Range (ATR) over price history.
    Uses True Range (OHLC) if high_history and low_history are provided and valid;
    otherwise falls back to close-to-close absolute differences.
    """
    if not price_history or len(price_history) < period + 1:
        return None

    if (
        high_history is not None
        and low_history is not None
        and len(high_history) == len(price_history)
        and len(low_history) == len(price_history)
        and len(price_history) >= period + 1
    ):
        start_idx = max(1, len(price_history) - period)
        trs = []
        for i in range(start_idx, len(price_history)):
            h = high_history[i]
            l = low_history[i]
            prev_c = price_history[i - 1]
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
            trs.append(tr)
        if trs:
            return float(np.mean(trs))

    recent = price_history[-(period + 1):]
    changes = [abs(recent[i] - recent[i - 1]) for i in range(1, len(recent))]
    if not changes:
        return None
    return float(np.mean(changes))


def get_sl_tp(
    price_history: List[float],
    direction: str = "LONG",
    atr_period: int = 14,
    trail_mult: float = 1.5,
    high_history: Optional[List[float]] = None,
    low_history: Optional[List[float]] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Determine initial Stop Loss level using ATR Trailing logic.

    Args:
        price_history: Time-series of asset prices / close prices (oldest → newest)
        direction: "LONG" or "SHORT"
        atr_period: Lookback period for ATR (default: 14)
        trail_mult: ATR multiplier for trailing stop distance (default: 1.5)
        high_history: Optional time-series of high prices
        low_history: Optional time-series of low prices

    Returns:
        (stop_loss, take_profit) tuple — take_profit is None for pure trailing stop.
    """
    if not price_history or len(price_history) < 2:
        logger.warning("Price history too short for SL/TP calculation")
        return None, None

    if direction == "0":
        logger.debug("Entry direction is 0 (neutral), no SL/TP")
        return None, None

    try:
        atr = compute_atr(
            price_history,
            period=atr_period,
            high_history=high_history,
            low_history=low_history,
        )
        if atr is None or atr <= 0:
            if len(price_history) >= 3:
                atr = float(np.std(np.diff(price_history)))
            if atr is None or atr <= 0:
                logger.warning("Zero/invalid ATR calculated, returning no SL/TP")
                return None, None

        entry_price = price_history[-1]
        if entry_price <= 0:
            logger.warning(f"Invalid entry price: {entry_price}")
            return None, None

        risk = trail_mult * atr

        if direction == "LONG":
            sl = entry_price - risk
            tp = None
        elif direction == "SHORT":
            sl = entry_price + risk
            tp = None
        else:
            logger.warning(f"Unknown direction: {direction}")
            return None, None

        if sl <= 0:
            logger.warning(f"Calculated SL is non-positive: {sl:.6f}")
            return None, None

        logger.info(
            f"ATR_TRAILING for {direction}: initial SL={sl:.6f}, TP=None "
            f"(ATR={atr:.6f}, mult={trail_mult}, risk={risk:.6f})"
        )
        return sl, tp

    except Exception as e:
        logger.error(f"Failed to compute ATR SL/TP: {e}")
        return None, None