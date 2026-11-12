"""
Layer 4 — Performance Circuit Breaker & Kill Switch

Monitors live trade outcomes and determines whether trading should be paused
using Effective Sample Size (ESS) autocorrelation-adjusted statistics.

Principles:
- Effective Sample Size (ESS) accounts for autocorrelation in trade outcomes
- 3-Way Bartlett Truncation Rule prevents ESS divergence or instability
- Auto kill-switch halts trading if Profit Factor < 0.50 after reaching statistical significance (ESS >= 30)
- Circuit breaker NEVER alters model parameters automatically; it solely issues should_trade = False
"""

import logging
from typing import List, Dict, Any, Optional
import numpy as np
from src.utils import database as db

logger = logging.getLogger(__name__)


def compute_effective_sample_size(outcomes: List[int]) -> float:
    """
    Compute Effective Sample Size (ESS) from trade outcome autocorrelation.

    Formula:
        ESS = N / (1 + 2 * sum_{k=1}^K rho(k))

    3-Way Truncation Rule:
    Summation stops at lag K where ANY of the following occurs:
    1. |rho(k)| < 1.96 / sqrt(N)  (Bartlett's test: autocorrelation is indistinguishable from noise)
    2. rho(k) < 0                 (Negative autocorrelation: stop at lag k-1)
    3. Lag exceeds MAX_LAG = min(N // 4, 20) (Safety cap)

    Args:
        outcomes: List of binary outcomes (1 = win, 0 = loss)

    Returns:
        float: Effective sample size bounded within [1.0, N]
    """
    n = len(outcomes)
    if n < 4:
        return float(max(n, 1))

    arr = np.array(outcomes, dtype=float)
    var_0 = float(np.var(arr))
    if var_0 < 1e-12:
        # All wins or all losses (zero variance)
        return float(n)

    mean_0 = float(np.mean(arr))
    norm_arr = arr - mean_0

    # Denominator variance sum
    gamma_0 = float(np.sum(norm_arr ** 2))

    max_lag = min(n // 4, 20)
    bartlett_threshold = 1.96 / np.sqrt(n)

    rho_sum = 0.0
    for k in range(1, max_lag + 1):
        gamma_k = float(np.sum(norm_arr[:-k] * norm_arr[k:]))
        rho_k = gamma_k / gamma_0

        # Condition 2: First negative autocorrelation
        if rho_k < 0:
            break

        # Condition 1: First non-significant lag
        if abs(rho_k) < bartlett_threshold:
            break

        rho_sum += rho_k

    denom = 1.0 + 2.0 * rho_sum
    if denom <= 0:
        denom = 1.0

    ess = n / denom
    # Clamp ESS strictly between 1.0 and N
    return float(max(1.0, min(float(n), ess)))


def evaluate_performance(
    exchange: str,
    pair: str,
    timeframe: str,
    min_ess: int = 30,
) -> Dict[str, Any]:
    """
    Evaluate live trading performance for a specific combo.

    Returns:
        {
            "should_trade": bool,
            "reason": str,
            "raw_n": int,
            "ess": float,
            "profit_factor": float,
            "win_rate": float,
            "consecutive_losses": int,
        }
    """
    try:
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT pnl_percent, outcome
            FROM signal_history
            WHERE exchange = ? AND pair = ? AND timeframe = ?
              AND outcome IN ('TP_HIT', 'SL_HIT', 'EXPIRED', 'LIQUIDATED')
            ORDER BY id ASC
        """, (exchange.lower(), pair, timeframe))
        rows = cursor.fetchall()
        conn.close()

    except Exception as e:
        logger.error(f"Error querying performance from DB: {e}")
        return {
            "should_trade": True,
            "reason": f"DB read error ({e}), default pass-through",
            "raw_n": 0,
            "ess": 0.0,
            "profit_factor": 1.0,
            "win_rate": 0.0,
            "consecutive_losses": 0,
        }

    raw_n = len(rows)
    if raw_n == 0:
        return {
            "should_trade": True,
            "reason": "No resolved trade history (initial state)",
            "raw_n": 0,
            "ess": 0.0,
            "profit_factor": 1.0,
            "win_rate": 0.0,
            "consecutive_losses": 0,
        }

    pnls = [float(r["pnl_percent"]) if r["pnl_percent"] is not None else 0.0 for r in rows]
    outcomes = [1 if p > 0 else 0 for p in pnls]

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))

    if gross_loss > 0:
        pf = gross_profit / gross_loss
    else:
        pf = 10.0 if gross_profit > 0 else 1.0

    win_rate = (sum(outcomes) / raw_n) * 100.0
    ess = compute_effective_sample_size(outcomes)

    # Compute trailing consecutive losses
    consecutive_losses = 0
    for o in reversed(outcomes):
        if o == 0:
            consecutive_losses += 1
        else:
            break

    # Kill Conditions Evaluation
    if consecutive_losses >= 10:
        return {
            "should_trade": False,
            "reason": f"Circuit breaker tripped: {consecutive_losses} consecutive losses",
            "raw_n": raw_n,
            "ess": ess,
            "profit_factor": pf,
            "win_rate": win_rate,
            "consecutive_losses": consecutive_losses,
        }

    if ess >= min_ess and pf < 0.5:
        return {
            "should_trade": False,
            "reason": f"Circuit breaker tripped: Profit Factor {pf:.2f} < 0.50 (ESS={ess:.1f})",
            "raw_n": raw_n,
            "ess": ess,
            "profit_factor": pf,
            "win_rate": win_rate,
            "consecutive_losses": consecutive_losses,
        }

    return {
        "should_trade": True,
        "reason": "Performance within normal bounds",
        "raw_n": raw_n,
        "ess": ess,
        "profit_factor": pf,
        "win_rate": win_rate,
        "consecutive_losses": consecutive_losses,
    }
