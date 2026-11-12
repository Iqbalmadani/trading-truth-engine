"""
TTE — Neutral 3-Way Exit Method Comparison Simulator

Compares four exit mechanisms on identical TTE Layer 3 entry signals:
1. ATR Trailing 1.5x (Production Baseline)
2. CPD-Adaptive Chandelier (Dynamic Regime-Scaled Chandelier Exit)
3. Triple-Barrier Method — Full (López de Prado 2018: TP + SL + Time Barrier)
4. Triple-Barrier Method — SL + Time Only (López de Prado 2018: SL + Time Barrier, no TP cap)

Anti-Bias Guarantees:
- Single Entry Signal Generation: L1->L2->L3 pipeline runs ONCE.
- Fixed-Window Anchoring: Identical OHLCV and scope historical data using --end-date.
- Pre-Simulation Sanity Check: Calibrates tau_sl and tau_pt using median ATR stop distance.
- Friction Drag: Identical 0.04% fee + 0.05% slippage applied per trade.
- No production files in src/ are touched.

Usage:
    python scripts/simulate_tpsl_3way.py --exchange gateio --pair ETH_USDT --tf 1h --candles 800 --end-date 2026-07-31
"""

import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.layer1.connectors.registry import get_connector
from src.layer2.cpd import get_cpd_window
from src.layer2.zscore import zscore_in_window
from src.layer3.executor import layer3

from scripts.run_backtest import (
    fetch_historical_ohlcv,
    fetch_historical_scope,
    compute_bt_penalties,
)
from scripts.simulate_tpsl import (
    compute_fixed_window_indices,
    apply_window,
    compute_atr,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Exit Simulators — Standardized Interface
# Signature: (candles, i, direction, entry_price, params, fee_pct, slip_pct)
# Returns: Dict with outcome, exit_offset, r_multiple, net_pnl_pct, duration
# =====================================================================

def exit_atr_trailing(
    candles: List[Dict],
    i: int,
    direction: str,
    entry_price: float,
    atr: float,
    trail_mult: float = 1.5,
    max_hold: int = 50,
    fee_pct: float = 0.0004,
    slip_pct: float = 0.0005,
) -> Optional[Dict[str, Any]]:
    """Method 1: ATR Trailing 1.5x (Baseline)."""
    if atr is None or atr <= 0 or entry_price <= 0:
        return None

    risk_dist = trail_mult * atr
    risk_pct = risk_dist / entry_price
    if direction == "LONG":
        stop = entry_price - risk_dist
        extreme = entry_price
    else:
        stop = entry_price + risk_dist
        extreme = entry_price

    total_friction = 2 * (fee_pct + slip_pct)

    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]
        if direction == "LONG":
            if c["low"] <= stop:
                exit_price = min(c["open"], stop)
                raw_ret = (exit_price - entry_price) / entry_price
                net_ret = raw_ret - total_friction
                r_mult = raw_ret / risk_pct
                outcome = "WIN" if net_ret > 0 else "LOSS"
                return {
                    "method": "ATR_TRAILING_1.5x",
                    "outcome": outcome,
                    "exit_offset": offset,
                    "r": r_mult,
                    "net_ret": net_ret,
                    "exit_reason": "SL_HIT",
                }
            extreme = max(extreme, c["high"])
            stop = max(stop, extreme - risk_dist)
        else:
            if c["high"] >= stop:
                exit_price = max(c["open"], stop)
                raw_ret = (entry_price - exit_price) / entry_price
                net_ret = raw_ret - total_friction
                r_mult = raw_ret / risk_pct
                outcome = "WIN" if net_ret > 0 else "LOSS"
                return {
                    "method": "ATR_TRAILING_1.5x",
                    "outcome": outcome,
                    "exit_offset": offset,
                    "r": r_mult,
                    "net_ret": net_ret,
                    "exit_reason": "SL_HIT",
                }
            extreme = min(extreme, c["low"])
            stop = min(stop, extreme + risk_dist)

    j = min(i + max_hold, len(candles) - 1)
    close_price = candles[j]["close"]
    raw_ret = (close_price - entry_price) / entry_price if direction == "LONG" else (entry_price - close_price) / entry_price
    net_ret = raw_ret - total_friction
    r_mult = raw_ret / risk_pct
    outcome = "WIN" if net_ret > 0 else "LOSS"
    return {
        "method": "ATR_TRAILING_1.5x",
        "outcome": outcome,
        "exit_offset": max_hold,
        "r": r_mult,
        "net_ret": net_ret,
        "exit_reason": "TIMEOUT",
    }


def exit_cpd_chandelier(
    candles: List[Dict],
    i: int,
    direction: str,
    entry_price: float,
    atr: float,
    sigma_regime: float,
    sigma_baseline: float,
    base_mult: float = 1.5,
    clamp_min: float = 1.0,
    clamp_max: float = 2.5,
    max_hold: int = 50,
    fee_pct: float = 0.0004,
    slip_pct: float = 0.0005,
) -> Optional[Dict[str, Any]]:
    """Method 2: CPD-Adaptive Chandelier Exit."""
    if atr is None or atr <= 0 or entry_price <= 0:
        return None

    v_ratio = (sigma_regime / sigma_baseline) if (sigma_baseline and sigma_baseline > 0) else 1.0
    k_adaptive = float(np.clip(base_mult * np.sqrt(v_ratio), clamp_min, clamp_max))

    risk_dist = k_adaptive * atr
    risk_pct = risk_dist / entry_price

    if direction == "LONG":
        stop = entry_price - risk_dist
        extreme = entry_price
    else:
        stop = entry_price + risk_dist
        extreme = entry_price

    total_friction = 2 * (fee_pct + slip_pct)

    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]
        if direction == "LONG":
            if c["low"] <= stop:
                exit_price = min(c["open"], stop)
                raw_ret = (exit_price - entry_price) / entry_price
                net_ret = raw_ret - total_friction
                r_mult = raw_ret / risk_pct
                outcome = "WIN" if net_ret > 0 else "LOSS"
                return {
                    "method": "CPD_CHANDELIER",
                    "outcome": outcome,
                    "exit_offset": offset,
                    "r": r_mult,
                    "net_ret": net_ret,
                    "exit_reason": "SL_HIT",
                }
            extreme = max(extreme, c["high"])
            stop = max(stop, extreme - risk_dist)
        else:
            if c["high"] >= stop:
                exit_price = max(c["open"], stop)
                raw_ret = (entry_price - exit_price) / entry_price
                net_ret = raw_ret - total_friction
                r_mult = raw_ret / risk_pct
                outcome = "WIN" if net_ret > 0 else "LOSS"
                return {
                    "method": "CPD_CHANDELIER",
                    "outcome": outcome,
                    "exit_offset": offset,
                    "r": r_mult,
                    "net_ret": net_ret,
                    "exit_reason": "SL_HIT",
                }
            extreme = min(extreme, c["low"])
            stop = min(stop, extreme + risk_dist)

    j = min(i + max_hold, len(candles) - 1)
    close_price = candles[j]["close"]
    raw_ret = (close_price - entry_price) / entry_price if direction == "LONG" else (entry_price - close_price) / entry_price
    net_ret = raw_ret - total_friction
    r_mult = raw_ret / risk_pct
    outcome = "WIN" if net_ret > 0 else "LOSS"
    return {
        "method": "CPD_CHANDELIER",
        "outcome": outcome,
        "exit_offset": max_hold,
        "r": r_mult,
        "net_ret": net_ret,
        "exit_reason": "TIMEOUT",
    }


def exit_triple_barrier_full(
    candles: List[Dict],
    i: int,
    direction: str,
    entry_price: float,
    sigma_target: float,
    tau_pt: float,
    tau_sl: float,
    max_hold: int = 50,
    fee_pct: float = 0.0004,
    slip_pct: float = 0.0005,
) -> Optional[Dict[str, Any]]:
    """Method 3: Triple-Barrier Method — Variant 1 (Full TP + SL + Time)."""
    if entry_price <= 0 or sigma_target <= 0:
        return None

    sl_dist_pct = tau_sl * sigma_target
    tp_dist_pct = tau_pt * sigma_target
    if sl_dist_pct <= 0:
        return None

    if direction == "LONG":
        p_upper = entry_price * (1.0 + tp_dist_pct)
        p_lower = entry_price * (1.0 - sl_dist_pct)
    else:
        p_upper = entry_price * (1.0 - tp_dist_pct)
        p_lower = entry_price * (1.0 + sl_dist_pct)

    total_friction = 2 * (fee_pct + slip_pct)

    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]

        if direction == "LONG":
            hit_sl = c["low"] <= p_lower
            hit_tp = c["high"] >= p_upper
        else:
            hit_sl = c["high"] >= p_lower
            hit_tp = c["low"] <= p_upper

        if hit_sl and hit_tp:
            # Conservative: treat as SL hit first
            raw_ret = -sl_dist_pct
            net_ret = raw_ret - total_friction
            r_mult = raw_ret / sl_dist_pct
            return {
                "method": "TRIPLE_BARRIER_FULL",
                "outcome": "LOSS",
                "exit_offset": offset,
                "r": r_mult,
                "net_ret": net_ret,
                "exit_reason": "SL_HIT",
            }
        if hit_sl:
            raw_ret = -sl_dist_pct
            net_ret = raw_ret - total_friction
            r_mult = raw_ret / sl_dist_pct
            return {
                "method": "TRIPLE_BARRIER_FULL",
                "outcome": "LOSS",
                "exit_offset": offset,
                "r": r_mult,
                "net_ret": net_ret,
                "exit_reason": "SL_HIT",
            }
        if hit_tp:
            raw_ret = tp_dist_pct
            net_ret = raw_ret - total_friction
            r_mult = raw_ret / sl_dist_pct
            outcome = "WIN" if net_ret > 0 else "LOSS"
            return {
                "method": "TRIPLE_BARRIER_FULL",
                "outcome": outcome,
                "exit_offset": offset,
                "r": r_mult,
                "net_ret": net_ret,
                "exit_reason": "TP_HIT",
            }

    # Vertical Barrier (t_max = max_hold)
    j = min(i + max_hold, len(candles) - 1)
    close_price = candles[j]["close"]
    raw_ret = (close_price - entry_price) / entry_price if direction == "LONG" else (entry_price - close_price) / entry_price
    net_ret = raw_ret - total_friction
    r_mult = raw_ret / sl_dist_pct
    outcome = "WIN" if net_ret > 0 else "LOSS"
    return {
        "method": "TRIPLE_BARRIER_FULL",
        "outcome": outcome,
        "exit_offset": max_hold,
        "r": r_mult,
        "net_ret": net_ret,
        "exit_reason": "TIMEOUT",
    }


def exit_triple_barrier_sl_time(
    candles: List[Dict],
    i: int,
    direction: str,
    entry_price: float,
    sigma_target: float,
    tau_sl: float,
    max_hold: int = 50,
    fee_pct: float = 0.0004,
    slip_pct: float = 0.0005,
) -> Optional[Dict[str, Any]]:
    """Method 4: Triple-Barrier Method — Variant 2 (SL + Time Only, No TP Cap)."""
    if entry_price <= 0 or sigma_target <= 0:
        return None

    sl_dist_pct = tau_sl * sigma_target
    if sl_dist_pct <= 0:
        return None

    if direction == "LONG":
        p_lower = entry_price * (1.0 - sl_dist_pct)
    else:
        p_lower = entry_price * (1.0 + sl_dist_pct)

    total_friction = 2 * (fee_pct + slip_pct)

    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]

        if direction == "LONG":
            hit_sl = c["low"] <= p_lower
        else:
            hit_sl = c["high"] >= p_lower

        if hit_sl:
            raw_ret = -sl_dist_pct
            net_ret = raw_ret - total_friction
            r_mult = raw_ret / sl_dist_pct
            return {
                "method": "TRIPLE_BARRIER_SL_TIME",
                "outcome": "LOSS",
                "exit_offset": offset,
                "r": r_mult,
                "net_ret": net_ret,
                "exit_reason": "SL_HIT",
            }

    # Vertical Barrier (t_max = max_hold)
    j = min(i + max_hold, len(candles) - 1)
    close_price = candles[j]["close"]
    raw_ret = (close_price - entry_price) / entry_price if direction == "LONG" else (entry_price - close_price) / entry_price
    net_ret = raw_ret - total_friction
    r_mult = raw_ret / sl_dist_pct
    outcome = "WIN" if net_ret > 0 else "LOSS"
    return {
        "method": "TRIPLE_BARRIER_SL_TIME",
        "outcome": outcome,
        "exit_offset": max_hold,
        "r": r_mult,
        "net_ret": net_ret,
        "exit_reason": "TIMEOUT",
    }


# =====================================================================
# Main Simulation Orchestrator
# =====================================================================

def run_3way_simulation(
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str,
    n_candles: int,
    min_warmup: int = 50,
    recal_interval: int = 100,
    max_hold: int = 50,
    atr_period: int = 14,
    end_date: Optional[str] = None,
    fetch_buffer: int = 3000,
    fee_pct: float = 0.0004,
    slip_pct: float = 0.0005,
    initial_capital: float = 1000.0,
):
    print(f"\n{'='*75}")
    print(f"  TTE NEUTRAL 3-WAY EXIT METHOD COMPARISON SIMULATOR")
    print(f"  Target: {exchange}/{pair}/{timeframe} | Candles: {n_candles} | Max Hold: {max_hold}")
    if end_date:
        print(f"  FIXED WINDOW ANCHORED AT: {end_date}")
    else:
        print(f"  WARNING: No --end-date provided. Data window will shift over time.")
    print(f"{'='*75}\n")

    connector = get_connector(exchange)
    fetch_count = fetch_buffer if end_date else n_candles
    if fetch_count > 2000:
        print(f"  NOTE: fetch_count ({fetch_count}) capped to Gate.io API limit (2000 candles).")
        fetch_count = 2000

    raw_candles = fetch_historical_ohlcv(connector, pair, timeframe, market_type, fetch_count)
    if len(raw_candles) < min_warmup + 10:
        print(f"  ERROR: Insufficient candles fetched ({len(raw_candles)})")
        return

    start_idx, end_idx = compute_fixed_window_indices(raw_candles, n_candles, end_date)
    candles = apply_window(raw_candles, start_idx, end_idx, "OHLCV candles")
    if len(candles) < min_warmup + 10:
        print(f"  ERROR: Insufficient windowed candles ({len(candles)})")
        return

    # Fetch scope data for L2 scoring
    scope_histories = {}
    active_scopes = []
    CANDLE_ALIGNED_SCOPES = {"ohlcv", "volatility_compression"}

    for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
        history = fetch_historical_scope(connector, pair, timeframe, market_type, scope, fetch_count)
        if not history or len(history) < 2:
            continue
        if scope in CANDLE_ALIGNED_SCOPES and end_idx <= len(history):
            history = apply_window(history, start_idx, end_idx, f"scope:{scope}")
        scope_histories[scope] = history
        active_scopes.append(scope)

    close_prices = [c["close"] for c in candles]

    # Generate signals via L1->L2->L3 pipeline ONCE
    score_history: List[float] = []
    signals: List[Dict[str, Any]] = []

    penalty_score = 1.0
    penalty_price = 1.0
    model_score = "rbf"
    model_price = "rbf"

    active_position = False
    last_exit_idx = -1

    for i in range(min_warmup, len(close_prices)):
        if i % recal_interval == 0 and len(score_history) >= 30:
            ps, pp, ms, mp = compute_bt_penalties(score_history, close_prices[:i])
            if ps is not None:
                penalty_score = ps
            if pp is not None:
                penalty_price = pp
            if ms is not None:
                model_score = ms
            if mp is not None:
                model_price = mp

        sub_close = close_prices[: i + 1]
        sub_high = [c["high"] for c in candles[: i + 1]]
        sub_low = [c["low"] for c in candles[: i + 1]]

        z_scores = []
        for scope in active_scopes:
            sc_hist = scope_histories[scope]
            pos = min(i, len(sc_hist) - 1)
            sub_sc = sc_hist[: pos + 1]

            if len(sub_sc) >= 10:
                try:
                    win, _ = get_cpd_window(sub_sc, penalty=penalty_score, model=model_score)
                    if len(win) >= 2:
                        z = zscore_in_window(win, sub_sc[-1])
                        z_scores.append(float(z) if not (np.isnan(z) or np.isinf(z)) else 0.0)
                except Exception:
                    pass

        current_score = float(np.sum(z_scores)) if z_scores else 0.0
        score_history.append(current_score)

        if active_position and i < last_exit_idx:
            continue
        else:
            active_position = False

        sig = layer3(
            current_score=current_score,
            score_history=score_history,
            current_price=close_prices[i],
            price_history=sub_close,
            penalty_score=penalty_score,
            penalty_price=penalty_price,
            high_history=sub_high,
            low_history=sub_low,
            score_model=model_score,
            price_model=model_price,
        )

        direction = sig.get("entry", "0")
        if direction in ("LONG", "SHORT"):
            atr = compute_atr(candles, i, period=atr_period)
            if atr is None or atr <= 0:
                continue

            # --- TRUE CPD REGIME VOLATILITY CALCULATION ---
            # 1. Use get_cpd_window on sub_close (price history) to detect active price regime points
            win_price, _ = get_cpd_window(sub_close, penalty=penalty_price, model=model_price)
            if len(win_price) >= 3:
                win_returns = np.diff(win_price) / win_price[:-1]
                sigma_regime = float(np.std(win_returns)) if len(win_returns) > 0 else 0.001
            else:
                all_ret = np.diff(sub_close) / sub_close[:-1]
                sigma_regime = float(np.std(all_ret)) if len(all_ret) > 0 else 0.001

            # 2. Baseline volatility: standard deviation over full historical series up to this candle
            all_returns = np.diff(sub_close) / sub_close[:-1]
            sigma_baseline = float(np.std(all_returns)) if len(all_returns) > 0 else sigma_regime

            signals.append({
                "candle_idx": i,
                "timestamp": candles[i].get("timestamp", i),
                "direction": direction,
                "entry_price": close_prices[i],
                "atr": atr,
                "sigma_regime": sigma_regime,
                "sigma_baseline": sigma_baseline,
            })
            active_position = True
            last_exit_idx = i + max_hold

    if not signals:
        print("  ERROR: Zero signals generated by L1->L2->L3 pipeline.")
        return

    print(f"  Generated {len(signals)} entry signals across {len(candles)} candles.\n")

    # =====================================================================
    # Pre-Simulation Sanity Check & Rescaling Factor Calculation
    # =====================================================================
    atr_stop_pcts = [(1.5 * s["atr"]) / s["entry_price"] for s in signals]
    chandelier_stop_pcts = []
    for s in signals:
        v_rat = (s["sigma_regime"] / s["sigma_baseline"]) if s["sigma_baseline"] > 0 else 1.0
        k_adap = float(np.clip(1.5 * np.sqrt(v_rat), 1.0, 2.5))
        chandelier_stop_pcts.append((k_adap * s["atr"]) / s["entry_price"])

    sigma_regime_pcts = [s["sigma_regime"] for s in signals]

    med_atr_risk_pct = float(np.median(atr_stop_pcts))
    med_chandelier_risk_pct = float(np.median(chandelier_stop_pcts))
    med_sigma_pct = float(np.median(sigma_regime_pcts))

    # Option A Rescaling: Scale factor aligns tau_sl and tau_pt to baseline ATR 1.5x risk
    scale_factor = med_atr_risk_pct / med_sigma_pct if med_sigma_pct > 0 else 1.5
    tau_sl_calibrated = scale_factor
    tau_pt_calibrated = scale_factor  # Symmetric 1:1 risk:reward baseline for Variant 1 Full

    print(f"{'='*75}")
    print(f"  PRE-SIMULATION SANITY CHECK & UNIT ALIGNMENT")
    print(f"{'='*75}")
    print(f"  Median Initial Risk % (ATR Trailing 1.5x)    : {med_atr_risk_pct*100:.3f}%")
    print(f"  Median Initial Risk % (CPD Chandelier)       : {med_chandelier_risk_pct*100:.3f}%")
    print(f"  Median Raw sigma_regime Volatility           : {med_sigma_pct*100:.3f}%")
    print(f"  Calculated Unit Scale Factor (Scale Factor)  : {scale_factor:.4f}")
    print(f"  Calibrated Triple-Barrier tau_sl             : {tau_sl_calibrated:.4f}")
    print(f"  Calibrated Triple-Barrier tau_pt (Variant 1) : {tau_pt_calibrated:.4f} (Symmetric 1:1 Baseline)")
    print(f"{'='*75}\n")

    # =====================================================================
    # Execute Exit Simulations Across All 4 Methods
    # =====================================================================
    results_by_method = {
        "ATR_TRAILING_1.5x": [],
        "CPD_CHANDELIER": [],
        "TRIPLE_BARRIER_FULL": [],
        "TRIPLE_BARRIER_SL_TIME": [],
    }

    for s in signals:
        i = s["candle_idx"]
        direction = s["direction"]
        entry_price = s["entry_price"]
        atr = s["atr"]
        sigma_regime = s["sigma_regime"]
        sigma_baseline = s["sigma_baseline"]
        baseline_risk_pct = (1.5 * atr) / entry_price

        # Method 1
        res1 = exit_atr_trailing(candles, i, direction, entry_price, atr, trail_mult=1.5, max_hold=max_hold, fee_pct=fee_pct, slip_pct=slip_pct)
        if res1:
            results_by_method["ATR_TRAILING_1.5x"].append(res1)

        # Method 2
        res2 = exit_cpd_chandelier(candles, i, direction, entry_price, atr, sigma_regime, sigma_baseline, base_mult=1.5, clamp_min=1.0, clamp_max=2.5, max_hold=max_hold, fee_pct=fee_pct, slip_pct=slip_pct)
        if res2:
            results_by_method["CPD_CHANDELIER"].append(res2)

        # Method 3
        res3 = exit_triple_barrier_full(candles, i, direction, entry_price, sigma_regime, tau_pt=tau_pt_calibrated, tau_sl=tau_sl_calibrated, max_hold=max_hold, fee_pct=fee_pct, slip_pct=slip_pct)
        if res3:
            results_by_method["TRIPLE_BARRIER_FULL"].append(res3)

        # Method 4
        res4 = exit_triple_barrier_sl_time(candles, i, direction, entry_price, sigma_regime, tau_sl=tau_sl_calibrated, max_hold=max_hold, fee_pct=fee_pct, slip_pct=slip_pct)
        if res4:
            results_by_method["TRIPLE_BARRIER_SL_TIME"].append(res4)

    # =====================================================================
    # Compute & Print Comparison Report Table
    # =====================================================================
    print(f"\n{'='*75}")
    print(f"  HEAD-TO-HEAD EXIT METHOD SIMULATION RESULTS")
    print(f"{'='*75}\n")

    summary_rows = []

    for method_name, res_list in results_by_method.items():
        n = len(res_list)
        if n == 0:
            continue

        wins = [r for r in res_list if r["outcome"] == "WIN"]
        losses = [r for r in res_list if r["outcome"] == "LOSS"]

        win_rate = (len(wins) / n) * 100.0
        r_vals = [r["r"] for r in res_list]
        exp_r = float(np.mean(r_vals)) if r_vals else 0.0
        std_r = float(np.std(r_vals)) if r_vals else 0.0

        net_returns = [r["net_ret"] for r in res_list]
        total_net_ret = sum(net_returns) * 100.0

        pos_ret = sum([r["net_ret"] for r in wins])
        neg_ret = abs(sum([r["net_ret"] for r in losses]))
        pf_net = (pos_ret / neg_ret) if neg_ret > 0 else (99.0 if pos_ret > 0 else 0.0)

        # Compound equity curve & Max Drawdown
        equity = initial_capital
        eq_curve = [equity]
        for r in res_list:
            equity *= (1.0 + r["net_ret"])
            eq_curve.append(equity)

        peak = eq_curve[0]
        max_dd = 0.0
        for eq in eq_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
        max_dd_pct = max_dd * 100.0

        avg_dur = float(np.mean([r["exit_offset"] for r in res_list]))

        summary_rows.append({
            "Metode": method_name,
            "N": n,
            "Win Rate (%)": f"{win_rate:.1f}%",
            "Expectancy R": f"{exp_r:+.3f}R",
            "PF Net": f"{pf_net:.2f}",
            "Net Return (%)": f"{total_net_ret:+.2f}%",
            "Max DD (%)": f"-{max_dd_pct:.2f}%",
            "Avg Hold (c)": f"{avg_dur:.1f}",
            "Std Dev R": f"{std_r:.3f}",
        })

    # Print Markdown Table
    headers = ["Metode", "N", "Win Rate (%)", "Expectancy R", "PF Net", "Net Return (%)", "Max DD (%)", "Avg Hold (c)", "Std Dev R"]
    print(f"| {' | '.join(headers)} |")
    print(f"|{'|'.join(['---']*len(headers))}|")
    for r in summary_rows:
        vals = [str(r[h]) for h in headers]
        print(f"| {' | '.join(vals)} |")

    # =====================================================================
    # Mandatory Section: Keterbatasan & Potensi Bias
    # =====================================================================
    print(f"\n{'='*75}")
    print(f"  KETERBATASAN & POTENSI BIAS (MANDATORY REPORT SECTION)")
    print(f"{'='*75}")
    print("1. Calibration Scope (Dataset-Wide Median):")
    print(f"   Kalibrasi scale_factor ({scale_factor:.4f}) dihitung dari median seluruh dataset test.")
    print("   Meskipun ini adalah konversi unit murni (bukan optimasi return/profit), statistik")
    print("   keseluruhan rentang waktu digunakan secara kolektif untuk menyamakan baseline risk.")
    print("\n2. Volatility Lookback Dynamics (ATR14 vs sigma_regime):")
    print("   ATR14 menggunakan jendela tetap 14 candle, sedangkan sigma_regime dihitung di atas")
    print("   durasi rezim CPD dinamis. Rescaling median menyamakan besaran rata-rata, namun")
    print("   karakter responsivitas terhadap kejutan harga antar waktu bisa berbeda.")
    print("\n3. Dual Triple-Barrier Variants:")
    print("   Triple-Barrier diuji dalam 2 varian untuk menghindari bias struktural: Varian 1 (Full) ")
    print("   menggunakan TP eksplisit (1:1 R:R), sedangkan Varian 2 (SL+Time) membiarkan profit ")
    print("   berjalan seperti trend-following murni.")

    if len(signals) < 30:
        print(f"\n[WARNING] STATISTICAL DISCLAIMER: Jumlah sinyal (N={len(signals)}) < 30.")
        print("   Ukuran sampel kecil. Hasil di atas belum signifikan secara statistik dan ")
        print("   berpotensi terpengaruh noise sampel.")

    print(f"{'='*75}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TTE Neutral 3-Way Exit Method Comparison Simulator")
    parser.add_argument("--exchange", type=str, default="gateio")
    parser.add_argument("--pair", type=str, default="ETH_USDT")
    parser.add_argument("--tf", type=str, default="1h")
    parser.add_argument("--market-type", type=str, default="futures")
    parser.add_argument("--candles", type=int, default=800)
    parser.add_argument("--max-hold", type=int, default=50)
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--end-date", type=str, default=None, help="Fixed window anchoring ISO date (e.g. 2026-07-31)")
    parser.add_argument("--fetch-buffer", type=int, default=3000)
    args = parser.parse_args()

    run_3way_simulation(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.tf,
        market_type=args.market_type,
        n_candles=args.candles,
        max_hold=args.max_hold,
        atr_period=args.atr_period,
        end_date=args.end_date,
        fetch_buffer=args.fetch_buffer,
    )
