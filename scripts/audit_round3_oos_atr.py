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

OOS_DATASETS = [
    ("gateio", "BTC_USDT", "1h", 500),
    ("gateio", "ETH_USDT", "4h", 500),
    ("gateio", "BTC_USDT", "4h", 500),
]

MULTIPLIERS = [1.0, 1.5, 2.0, 2.5]

def run_oos_atr_audit():
    print("=" * 85, flush=True)
    print("  TTE AUDIT ROUND 3 — PART D: OUT-OF-SAMPLE ATR MULTIPLIER VALIDATION", flush=True)
    print("  Evaluating whether 2.0x ATR holds across other assets and timeframes", flush=True)
    print("=" * 85, flush=True)

    fee_rate = 0.0004
    slippage_rate = 0.0005

    for exchange, pair, tf, n_candles in OOS_DATASETS:
        print(f"\n--- Testing Dataset: {pair} {tf} ({n_candles} candles) ---", flush=True)
        connector = get_connector(exchange)
        candles = fetch_historical_ohlcv(connector, pair, tf, "perpetual", n_candles)
        if not candles or len(candles) < 200:
            print(f"Failed to fetch enough candles for {pair} {tf}", flush=True)
            continue

        close_prices = [c["close"] for c in candles]
        high_prices = [c["high"] for c in candles]
        low_prices = [c["low"] for c in candles]

        scope_histories = {}
        for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
            hist = fetch_historical_scope(connector, pair, tf, "perpetual", scope, n_candles)
            if hist and len(hist) >= 50:
                scope_histories[scope] = hist

        # Generate signals ONCE
        signals = []
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
                    signals.append({"candle_idx": i, "direction": direction, "entry_price": close_prices[i], "atr": atr})

        print(f"Fixed Signals Generated: N = {len(signals)}", flush=True)
        if len(signals) == 0:
            continue

        rows = []
        for k in MULTIPLIERS:
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
                max_hold = 50

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

                raw_pct = (exit_price - entry) / entry if direction == "LONG" else (entry - exit_price) / entry
                friction = 2 * (fee_rate + slippage_rate)
                net_pct = raw_pct - friction
                r_mult = raw_pct / (risk_dist / entry)
                resolved.append({"net_pct": net_pct, "r_multiple": r_mult, "duration": duration})

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

            rows.append({
                "multiplier": f"{k:.1f}x",
                "win_rate": f"{win_rate:.1f}%",
                "expectancy_R": f"{avg_r:+.3f}R",
                "profit_factor": f"{pf:.2f}",
                "net_return": f"{net_ret:+.2f}%",
                "median_dur": f"{med_dur:.0f}c",
            })

        print(pd.DataFrame(rows).to_string(index=False), flush=True)

if __name__ == "__main__":
    run_oos_atr_audit()
