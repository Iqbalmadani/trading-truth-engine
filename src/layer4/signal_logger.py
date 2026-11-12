"""
Layer 4 — Signal Logger & Outcome Tracker

Logs live trading signals and evaluates outcomes against subsequent market prices.

Outcome Types:
- TP_HIT: Take profit level was reached
- SL_HIT: Stop loss level was triggered
- EXPIRED: Max holding duration reached without SL/TP hit
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List
from src.utils import database as db
from src.utils.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)


def log_signal(
    exchange: str,
    pair: str,
    timeframe: str,
    signal_output: Dict[str, Any],
) -> Optional[int]:
    """
    Log a live trading signal to signal_history table.

    Args:
        exchange: Exchange identifier (e.g. 'gateio', 'binance')
        pair: Trading pair (e.g. 'ETH_USDT')
        timeframe: Candle timeframe (e.g. '1h')
        signal_output: Output dictionary from layer3()

    Returns:
        signal_id (int) or None if neutral/skipped
    """
    entry = signal_output.get("entry", "0")
    if entry not in ("LONG", "SHORT"):
        return None

    entry_price = float(signal_output.get("entry_price", 0.0))
    stop_loss = signal_output.get("stop_loss")
    take_profit = signal_output.get("take_profit")
    meta = signal_output.get("metadata", {})
    score = meta.get("score")
    leverage = signal_output.get("effective_leverage", 1.0)
    now_iso = datetime.now(timezone.utc).isoformat()

    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO signal_history (
                exchange, pair, timeframe, signal_type,
                entry_price, stop_loss, take_profit, leverage,
                score, created_at, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (
            exchange.lower(),
            pair,
            timeframe,
            entry,
            entry_price,
            stop_loss,
            take_profit,
            leverage,
            score,
            now_iso,
        ))
        conn.commit()
        signal_id = cursor.lastrowid
        conn.close()

        logger.info(
            f"Logged signal #{signal_id} ({exchange}/{pair}/{timeframe}): "
            f"{entry} @ {entry_price:.6f}, SL={stop_loss}, TP={take_profit}"
        )
        return signal_id

    except Exception as e:
        logger.error(f"Failed to log signal: {e}")
        return None


def check_outcomes(
    exchange: str,
    pair: str,
    timeframe: str,
    current_price: float,
    current_high: Optional[float] = None,
    current_low: Optional[float] = None,
    max_pending_candles: int = 48,
) -> Dict[str, int]:
    """
    Evaluate all pending signals for a combo against current high/low price.

    Args:
        exchange: Exchange identifier
        pair: Trading pair
        timeframe: Candle timeframe
        current_price: Current close / mid price
        current_high: High of the current candle (defaults to current_price)
        current_low: Low of the current candle (defaults to current_price)
        max_pending_candles: Maximum candles before a pending trade expires

    Returns:
        Dict with outcome resolution summary
    """
    high = current_high if current_high is not None else current_price
    low = current_low if current_low is not None else current_price

    results = {"tp_hit": 0, "sl_hit": 0, "expired": 0, "checked": 0}

    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, signal_type, entry_price, stop_loss, take_profit, created_at
            FROM signal_history
            WHERE exchange = ? AND pair = ? AND timeframe = ? AND outcome = 'PENDING'
            ORDER BY id ASC
        """, (exchange.lower(), pair, timeframe))
        pending_rows = cursor.fetchall()

        if not pending_rows:
            conn.close()
            return results

        now = datetime.now(timezone.utc)
        tf_sec = TIMEFRAME_SECONDS.get(timeframe, 3600)
        max_age_seconds = max_pending_candles * tf_sec

        for row in pending_rows:
            sig_id = row["id"]
            direction = row["signal_type"]
            entry_p = row["entry_price"]
            sl = row["stop_loss"]
            tp = row["take_profit"]
            created_at_str = row["created_at"]
            
            results["checked"] += 1
            outcome = None
            outcome_price = None

            # 1. Check Stop Loss & Take Profit
            if direction == "LONG":
                if sl is not None and low <= sl:
                    outcome = "SL_HIT"
                    outcome_price = sl
                    results["sl_hit"] += 1
                elif tp is not None and high >= tp:
                    outcome = "TP_HIT"
                    outcome_price = tp
                    results["tp_hit"] += 1
            elif direction == "SHORT":
                if sl is not None and high >= sl:
                    outcome = "SL_HIT"
                    outcome_price = sl
                    results["sl_hit"] += 1
                elif tp is not None and low <= tp:
                    outcome = "TP_HIT"
                    outcome_price = tp
                    results["tp_hit"] += 1

            # 2. Check Expiration
            if outcome is None and created_at_str:
                try:
                    created_dt = datetime.fromisoformat(created_at_str)
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=timezone.utc)
                    age_seconds = (now - created_dt).total_seconds()
                    if age_seconds >= max_age_seconds:
                        outcome = "EXPIRED"
                        outcome_price = current_price
                        results["expired"] += 1
                except Exception:
                    pass

            # 3. Update outcome in DB if resolved
            if outcome is not None:
                if direction == "LONG":
                    pnl_pct = ((outcome_price - entry_p) / entry_p) * 100.0
                else:  # SHORT
                    pnl_pct = ((entry_p - outcome_price) / entry_p) * 100.0

                cursor.execute("""
                    UPDATE signal_history
                    SET outcome = ?, outcome_price = ?, outcome_at = ?, pnl_percent = ?
                    WHERE id = ?
                """, (outcome, outcome_price, now.isoformat(), pnl_pct, sig_id))

                logger.info(
                    f"Resolved signal #{sig_id} ({direction}): {outcome} "
                    f"@ {outcome_price:.6f} (P&L: {pnl_pct:+.2f}%)"
                )

        conn.commit()
        conn.close()

    except Exception as e:
        logger.error(f"Error checking outcomes for {exchange}/{pair}/{timeframe}: {e}")

    return results
