import sys
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from src.layer2.zscore import zscore_in_window
from src.layer3.thresholds import get_score_thresholds, InsufficientScoreHistoryError
from src.layer3.risk import compute_atr
from scripts.run_backtest import fetch_historical_ohlcv, fetch_historical_scope
from scripts.audit_l2_alpha import fast_cpd_window

def simulate_trades(candles, signals, k=2.0, fee_rate=0.0004, slippage_rate=0.0005, max_hold=50):
    resolved = []
    for sig in signals:
        idx = sig["candle_idx"]
        direction = sig["direction"]
        entry = sig["entry_price"]
        atr = sig["atr"]
        risk_dist = k * atr
        initial_sl = (entry - risk_dist) if direction == "LONG" else (entry + risk_dist)

        curr_sl = initial_sl
        outcome = "TIMEOUT"
        exit_price = entry
        duration = 0

        for step in range(1, max_hold + 1):
            c_idx = idx + step
            if c_idx >= len(candles):
                break
            c = candles[c_idx]
            h = c["high"]
            l = c["low"]
            duration = step

            if direction == "LONG":
                if l <= curr_sl:
                    outcome = "SL_HIT" if curr_sl <= entry else "TRAIL_HIT"
                    exit_price = curr_sl
                    break
                else:
                    new_sl = max(curr_sl, h - risk_dist)
                    if new_sl > curr_sl:
                        curr_sl = new_sl
            elif direction == "SHORT":
                if h >= curr_sl:
                    outcome = "SL_HIT" if curr_sl >= entry else "TRAIL_HIT"
                    exit_price = curr_sl
                    break
                else:
                    new_sl = min(curr_sl, l + risk_dist)
                    if new_sl < curr_sl:
                        curr_sl = new_sl

        if outcome == "TIMEOUT":
            exit_price = candles[min(idx + max_hold, len(candles) - 1)]["close"]

        if direction == "LONG":
            raw_pct = (exit_price - entry) / entry
        else:
            raw_pct = (entry - exit_price) / entry

        friction = 2 * (fee_rate + slippage_rate)
        net_pct = raw_pct - friction
        r_mult = raw_pct / (risk_dist / entry)

        resolved.append({
            "outcome": outcome,
            "net_pct": net_pct,
            "r_multiple": r_mult,
            "duration": duration,
            "win": 1 if net_pct > 0 else 0,
        })

    df = pd.DataFrame(resolved)
    wins = df[df["net_pct"] > 0]["net_pct"]
    losses = df[df["net_pct"] <= 0]["net_pct"]
    win_rate = (len(wins) / len(df)) * 100 if len(df) > 0 else 0.0
    sum_gains = wins.sum() if len(wins) > 0 else 0.0
    sum_losses = abs(losses.sum()) if len(losses) > 0 else 1e-9
    pf = sum_gains / sum_losses if sum_losses > 0 else 0.0
    avg_r = df["r_multiple"].mean() if len(df) > 0 else 0.0
    net_ret = df["net_pct"].sum() * 100 if len(df) > 0 else 0.0
    med_dur = df["duration"].median() if len(df) > 0 else 0.0

    return {
        "N": len(df),
        "win_rate": f"{win_rate:.1f}%",
        "expectancy_R": f"{avg_r:+.3f}R",
        "profit_factor": f"{pf:.2f}",
        "net_return_pct": f"{net_ret:+.2f}%",
        "median_dur": f"{med_dur:.0f}",
    }

def run_round3_baseline():
    print("=" * 85)
    print("  TTE AUDIT ROUND 3 — PART A: RANDOM & PERIODIC BASELINE VS L2/L3 ENTRY")
    print("  Target: Isolate whether edge comes from Entry Timing or 2.0x ATR Trailing Exit")
    print("=" * 85)

    connector = get_connector("gateio")
    pair = "ETH_USDT"
    tf = "1h"
    n_candles = 500
    candles = fetch_historical_ohlcv(connector, pair, tf, "perpetual", n_candles)
    close_prices = [c["close"] for c in candles]
    high_prices = [c["high"] for c in candles]
    low_prices = [c["low"] for c in candles]

    scope_histories = {}
    for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
        hist = fetch_historical_scope(connector, pair, tf, "perpetual", scope, n_candles)
        if hist and len(hist) >= 50:
            scope_histories[scope] = hist

    # 1. Authentic L2/L3 Signals
    l2_signals = []
    score_history = []
    min_warmup = 40

    for i in range(min_warmup, len(candles) - 50):
        scope_slice = {scope: h[:min(i + 1, len(h))] for scope, h in scope_histories.items()}
        z_scores = {}
        for scope, data in scope_slice.items():
            recent = data[-150:] if len(data) > 150 else data
            if len(recent) < 3:
                continue
            try:
                var = float(np.var(recent))
                pen = max(1.0, 2.0 * var * np.log(len(recent))) if var > 1e-12 else 1.0
                window, _ = fast_cpd_window(recent, pen=pen)
                if len(window) >= 2:
                    z = zscore_in_window(window, recent[-1])
                    z_scores[scope] = float(z) if not (np.isnan(z) or np.isinf(z)) else 0.0
            except Exception:
                pass

        score = sum(z_scores.values())
        score_history.append(score)
        if len(score_history) < 10:
            continue

        try:
            th_up, th_down = get_score_thresholds(score_history, penalty=1.0)
        except InsufficientScoreHistoryError:
            continue

        direction = None
        if score > th_up and score > 0:
            direction = "LONG"
        elif score < th_down and score < 0:
            direction = "SHORT"

        if direction:
            atr = compute_atr(close_prices[:i + 1], period=14, high_history=high_prices[:i + 1], low_history=low_prices[:i + 1])
            if atr and atr > 0:
                l2_signals.append({
                    "candle_idx": i,
                    "direction": direction,
                    "entry_price": close_prices[i],
                    "atr": atr,
                })

    N_target = len(l2_signals)
    print(f"Authentic L2/L3 signals generated: N = {N_target}")

    # 2. Baseline 1: Pure Random Entry Timing & Direction (Reproducible seed 42)
    np.random.seed(42)
    valid_indices = list(range(min_warmup, len(candles) - 50))
    random_indices = np.random.choice(valid_indices, size=N_target, replace=True)
    random_indices.sort()
    random_signals_1 = []
    for idx in random_indices:
        direction = np.random.choice(["LONG", "SHORT"])
        atr = compute_atr(close_prices[:idx + 1], period=14, high_history=high_prices[:idx + 1], low_history=low_prices[:idx + 1])
        if atr and atr > 0:
            random_signals_1.append({
                "candle_idx": idx,
                "direction": direction,
                "entry_price": close_prices[idx],
                "atr": atr,
            })

    # Run multiple random iterations (Monte Carlo 20 seeds) to get mean & std of random entry
    random_pfs = []
    random_returns = []
    random_exp_r = []
    for seed in range(20):
        np.random.seed(100 + seed)
        rand_idx = np.random.choice(valid_indices, size=N_target, replace=True)
        r_sigs = []
        for idx in rand_idx:
            direction = np.random.choice(["LONG", "SHORT"])
            atr = compute_atr(close_prices[:idx + 1], period=14, high_history=high_prices[:idx + 1], low_history=low_prices[:idx + 1])
            if atr and atr > 0:
                r_sigs.append({"candle_idx": idx, "direction": direction, "entry_price": close_prices[idx], "atr": atr})
        res = simulate_trades(candles, r_sigs, k=2.0)
        random_pfs.append(float(res["profit_factor"]))
        random_returns.append(float(res["net_return_pct"].replace("%", "")))
        random_exp_r.append(float(res["expectancy_R"].replace("R", "")))

    # 3. Baseline 2: Fixed-Interval Periodic Entry (every 2 candles, alternating LONG/SHORT)
    periodic_signals = []
    step_size = max(1, len(valid_indices) // N_target)
    for k_step, idx in enumerate(valid_indices[::step_size][:N_target]):
        direction = "LONG" if k_step % 2 == 0 else "SHORT"
        atr = compute_atr(close_prices[:idx + 1], period=14, high_history=high_prices[:idx + 1], low_history=low_prices[:idx + 1])
        if atr and atr > 0:
            periodic_signals.append({
                "candle_idx": idx,
                "direction": direction,
                "entry_price": close_prices[idx],
                "atr": atr,
            })

    # Simulate all
    res_l2 = simulate_trades(candles, l2_signals, k=2.0)
    res_rand_single = simulate_trades(candles, random_signals_1, k=2.0)
    res_periodic = simulate_trades(candles, periodic_signals, k=2.0)

    print("\n" + "=" * 85)
    print("  RESULTS COMPARISON: 2.0x ATR TRAILING EXIT ON DIFFERENT ENTRY ENGINES")
    print("=" * 85)
    table_rows = [
        {"Model": "1. Authentic L2/L3 Entry", **res_l2},
        {"Model": "2. Random Entry (Seed 42)", **res_rand_single},
        {"Model": f"3. Random Entry (20 Monte Carlo Mean)", 
         "N": N_target,
         "win_rate": "-",
         "expectancy_R": f"{np.mean(random_exp_r):+.3f}R (std {np.std(random_exp_r):.2f})",
         "profit_factor": f"{np.mean(random_pfs):.2f} (std {np.std(random_pfs):.2f})",
         "net_return_pct": f"{np.mean(random_returns):+.2f}%",
         "median_dur": "-"},
        {"Model": "4. Periodic Interval Entry (Alternating L/S)", **res_periodic},
    ]
    df_res = pd.DataFrame(table_rows)
    print(df_res.to_string(index=False))

if __name__ == "__main__":
    run_round3_baseline()
