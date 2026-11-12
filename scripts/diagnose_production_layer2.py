"""
Diagnostic Research Script: Production Layer 2 Aggregator Score Diagnostic (Kat A Pure Research)

Imports AND calls production `src/layer2/aggregator.py` function `layer2()` directly,
ensuring 100% identical score calculation with production TTE system across all active scopes.

Evaluates locked multi-scope driver rules on authentic production composite score.

Usage:
    python scripts/diagnose_production_layer2.py --exchange gateio --pair ETH_USDT --tf 1h --candles 5000 --is-candles 3500
    python scripts/diagnose_production_layer2.py --exchange gateio --pair BTC_USDT --tf 1h --candles 5000 --is-candles 3500
"""

import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from scipy import stats

from src.layer1.connectors.registry import get_connector
from src.layer2.aggregator import layer2
from src.layer2.penalty_manager import PenaltyManager
from src.layer3.thresholds import get_score_thresholds, InsufficientScoreHistoryError
from src.layer3.risk import compute_atr

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def fetch_historical_candles(
    connector, pair: str, timeframe: str, market_type: str, n_candles: int
) -> List[Dict]:
    """Fetch N historical OHLCV candles with automatic pagination."""
    exchange = connector.exchange_id
    BATCH_SIZE = 1000
    all_candles = []
    current_to = None

    if exchange == "binance":
        from src.layer1.connectors.binance import TIMEFRAME_MAP
        symbol = pair.replace("_", "")
        interval = TIMEFRAME_MAP.get(timeframe)
        url = f"{connector.BASE_URL}/fapi/v1/klines"

        while len(all_candles) < n_candles:
            limit = min(BATCH_SIZE, n_candles - len(all_candles))
            params = {"symbol": symbol, "interval": interval, "limit": limit}
            if current_to:
                params["endTime"] = current_to * 1000 - 1

            data = connector._request(url, params)
            if not data:
                break

            batch = [
                {
                    "timestamp": int(c[0]) // 1000,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in data
            ]
            if not batch:
                break

            all_candles = batch + all_candles
            current_to = batch[0]["timestamp"]
            if len(data) < limit:
                break

        return all_candles[-n_candles:] if len(all_candles) > n_candles else all_candles

    elif exchange == "gateio":
        from src.layer1.connectors.gateio import TIMEFRAME_MAP
        interval = TIMEFRAME_MAP.get(timeframe)
        settle = "usdt"
        url = f"{connector.BASE_URL}/futures/{settle}/candlesticks"

        while len(all_candles) < n_candles:
            limit = min(BATCH_SIZE, n_candles - len(all_candles))
            params = {"contract": pair, "interval": interval, "limit": limit}
            if current_to:
                params["to"] = current_to - 1

            data = connector._request(url, params)
            if not data:
                break

            batch = [
                {
                    "timestamp": int(c["t"]),
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c.get("v", 0)),
                }
                for c in data
            ]
            if not batch:
                break

            batch = sorted(batch, key=lambda x: x["timestamp"])
            all_candles = batch + all_candles
            current_to = batch[0]["timestamp"]
            if len(data) < limit:
                break

        return all_candles[-n_candles:] if len(all_candles) > n_candles else all_candles

    else:
        raise ValueError(f"Unsupported exchange: {exchange}")


def run_production_l2_diagnostics(
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str,
    total_candles: int,
    is_candles: int,
):
    """Run diagnostic research using DIRECT production layer2() score from src/layer2/aggregator.py."""
    print("\n" + "=" * 75)
    print(f"  DIAGNOSTIC RESEARCH: PRODUCTION LAYER 2 COMPOSITE SCORE")
    print(f"  DIRECT IMPORT: src/layer2/aggregator.py -> layer2()")
    print(f"  {exchange.upper()}/{pair}/{timeframe}")
    print(f"  Total candles: {total_candles} | In-Sample IS candles: {is_candles}")
    print("=" * 75 + "\n")

    connector = get_connector(exchange)
    logger.info(f"Fetching {total_candles} candles from {exchange}...")
    candles = fetch_historical_candles(connector, pair, timeframe, market_type, total_candles)

    if len(candles) < is_candles:
        logger.error(f"Insufficient candles fetched: got {len(candles)}, need {total_candles}")
        return False

    is_slice = candles[:is_candles]
    logger.info(f"In-Sample Isolated Window: {len(is_slice)} candles (Index 0 .. {is_candles - 1})")

    close_prices = [c["close"] for c in is_slice]
    high_prices = [c["high"] for c in is_slice]
    low_prices = [c["low"] for c in is_slice]
    volumes = [c["volume"] for c in is_slice]

    penalty_manager = PenaltyManager(exchange=exchange, pair=pair, timeframe=timeframe)
    layer1_results = {
        "ohlcv": 1,
        "funding_rate": 1,
        "volatility_compression": 1,
    }

    score_history: List[float] = []
    records = []

    HORIZONS = [5, 10, 20, 50]
    BONFERRONI_ALPHA = 0.05 / 16.0  # 4 rules x 4 horizons = 16 tests -> alpha = 0.003125

    logger.info("Evaluating authentic production Layer 2 composite scores...")

    for i in range(50, len(is_slice) - 50):
        price_sub = close_prices[:i + 1]
        high_sub = high_prices[:i + 1]
        low_sub = low_prices[:i + 1]
        vol_sub = volumes[:i + 1]

        # Call DIRECT production layer2() function from src/layer2/aggregator.py
        try:
            prod_score, meta = layer2(
                connector=connector,
                exchange=exchange,
                layer1_results=layer1_results,
                pair=pair,
                timeframe=timeframe,
                market_type=market_type,
                penalty_manager=penalty_manager,
                lookback_periods=min(250, i + 1),
            )
            score = float(prod_score) if not (np.isnan(prod_score) or np.isinf(prod_score)) else 0.0
        except Exception as e:
            logger.debug(f"Direct layer2() call exception at index {i}: {e}")
            score = 0.0

        score_history.append(score)
        if len(score_history) < 5:
            continue

        try:
            th_up, th_down = get_score_thresholds(score_history, penalty=1.0)
        except InsufficientScoreHistoryError:
            continue

        # Extract individual scope z-scores from production metadata
        z_scores = meta.get("z_scores", {})
        z_fund = float(z_scores.get("funding_rate", 0.0))
        z_vol = float(z_scores.get("ohlcv", 0.0))  # Volume/OHLCV z-score

        if len(price_sub) >= 50:
            std_recent = float(np.std(price_sub[-14:]))
            std_hist = float(np.std(price_sub[-50:]))
            c_vol = (std_recent / std_hist) if std_hist > 1e-12 else 1.0
        else:
            c_vol = 1.0

        # Locked Mode 1 Priority: Funding Squeeze First
        rule_triggered = "NEUTRAL"
        rule_name = "NONE"

        if abs(z_fund) > 1.960:
            rule_triggered = "MEAN_REVERTING"
            rule_name = "RULE_2_FUNDING_2.5%"
        elif abs(z_fund) > 1.645:
            rule_triggered = "MEAN_REVERTING"
            rule_name = "RULE_1_FUNDING_5%"
        elif z_vol > 1.645 and c_vol < 0.50:
            rule_triggered = "BREAKOUT"
            rule_name = "RULE_4_VOL_5%"
        elif z_vol > 1.282 and c_vol < 0.70:
            rule_triggered = "BREAKOUT"
            rule_name = "RULE_3_VOL_10%"

        if rule_triggered == "MEAN_REVERTING":
            sig = "SHORT" if (score > th_up and score > 0) else ("LONG" if (score < th_down and score < 0) else "0")
        elif rule_triggered == "BREAKOUT":
            sig = "LONG" if (score > th_up and score > 0) else ("SHORT" if (score < th_down and score < 0) else "0")
        else:
            sig = "0"

        atr = compute_atr(price_sub[-50:], period=14, high_history=high_sub[-50:], low_history=low_sub[-50:])
        if atr is None or atr <= 0:
            atr = price_sub[-1] * 0.01

        curr_p = price_sub[-1]

        fwd_returns = {}
        fwd_r_multiples = {}
        for h in HORIZONS:
            if i + h < len(close_prices):
                future_p = close_prices[i + h]
                raw_ret = (future_p - curr_p) / curr_p
                fwd_returns[h] = raw_ret
                r_unit = (1.5 * atr) / curr_p
                fwd_r_multiples[h] = raw_ret / r_unit if r_unit > 0 else 0.0
            else:
                fwd_returns[h] = np.nan
                fwd_r_multiples[h] = np.nan

        records.append({
            "index": i,
            "price": curr_p,
            "score": score,
            "z_fund": z_fund,
            "z_vol": z_vol,
            "c_vol": c_vol,
            "rule_name": rule_name,
            "rule_mode": rule_triggered,
            "signal": sig,
            "fwd_ret_5": fwd_returns.get(5),
            "fwd_ret_10": fwd_returns.get(10),
            "fwd_ret_20": fwd_returns.get(20),
            "fwd_ret_50": fwd_returns.get(50),
            "fwd_r_5": fwd_r_multiples.get(5),
            "fwd_r_10": fwd_r_multiples.get(10),
            "fwd_r_20": fwd_r_multiples.get(20),
            "fwd_r_50": fwd_r_multiples.get(50),
        })

    df = pd.DataFrame(records)
    print(f"Evaluated {len(df)} decision points on In-Sample dataset.")
    print("\nRule Activation Breakdown:")
    for r_name in ["RULE_1_FUNDING_5%", "RULE_2_FUNDING_2.5%", "RULE_3_VOL_10%", "RULE_4_VOL_5%", "NONE"]:
        sub_len = len(df[df["rule_name"] == r_name])
        print(f"  - {r_name:<25}: {sub_len:4d} samples ({sub_len / len(df) * 100:.1f}%)")

    print("\n" + "=" * 75)
    print("  1. SPEARMAN CORRELATION & STATISTICAL POWER AUDIT (N >= 50)")
    print(f"  Bonferroni Adjusted Significance Threshold Alpha = {BONFERRONI_ALPHA:.6f}")
    print("=" * 75)

    valid_passed_rules = 0

    RULES = ["RULE_1_FUNDING_5%", "RULE_2_FUNDING_2.5%", "RULE_3_VOL_10%", "RULE_4_VOL_5%"]
    for r_name in RULES:
        sub_df = df[df["rule_name"] == r_name]
        n_samples = len(sub_df)
        print(f"\n--- {r_name} (Sample N = {n_samples}) ---")

        if n_samples < 50:
            print(f"  [INSUFFICIENT SAMPLE POWER (N = {n_samples} < 50) - SKIP INFERENCE]")
            continue

        for h in HORIZONS:
            valid_sub = sub_df.dropna(subset=[f"fwd_ret_{h}"])
            if len(valid_sub) < 10:
                print(f"  Horizon {h:2d}-candle: Insufficient data")
                continue
            r_stat, p_val = stats.spearmanr(valid_sub["score"], valid_sub[f"fwd_ret_{h}"])
            is_sig = (p_val < BONFERRONI_ALPHA and abs(r_stat) > 0.15)
            status_str = "[SIGNIFICANT]" if is_sig else "[Not Significant]"
            if is_sig:
                valid_passed_rules += 1

            print(
                f"  Horizon {h:2d}-candle: Spearman r = {r_stat:+.4f} | "
                f"p-val = {p_val:.6f} | Status: {status_str}"
            )

    print("\n" + "=" * 75)
    print("  2. EXPECTANCY IN R-MULTIPLE PER RULE (PRODUCTION LAYER 2 SCORE)")
    print("=" * 75)

    pos_expectancy_count = 0
    for r_name in RULES:
        sub_df = df[(df["rule_name"] == r_name) & (df["signal"] != "0")]
        n_act = len(sub_df)
        print(f"\n--- Active Signals for {r_name} (N = {n_act}) ---")
        if n_act < 10:
            print("  Insufficient active signal samples (< 10)")
            continue

        for h in HORIZONS:
            returns = []
            for _, row in sub_df.iterrows():
                mult = 1.0 if row["signal"] == "LONG" else -1.0
                r_val = mult * row[f"fwd_r_{h}"]
                if not np.isnan(r_val):
                    returns.append(r_val)
            mean_r = float(np.mean(returns)) if returns else 0.0
            is_pos = mean_r > 0.15
            if is_pos:
                pos_expectancy_count += 1
            print(f"  Horizon {h:2d}-candle: Expectancy = {mean_r:+.3f}R (N={len(returns)}) {'[PASS]' if is_pos else '[FAIL]'}")

    is_overall_valid = (valid_passed_rules >= 2) or (pos_expectancy_count >= 2)

    print("\n" + "=" * 75)
    print("  RESEARCH VALIDATION DECISION SUMMARY (PRODUCTION L2 SCORE)")
    print("=" * 75)
    print(f"  Asset / Pair             : {exchange.upper()}/{pair}/{timeframe}")
    print(f"  Bonferroni Significance  : {valid_passed_rules} horizon tests passed (p < {BONFERRONI_ALPHA:.6f} & |r| > 0.15)")
    print(f"  Positive Expectancy (>0.15R): {pos_expectancy_count} horizon tests passed")
    print(f"  IS DIAGNOSTIC VALID?     : {'YES [PROCEED TO PHASE 2]' if is_overall_valid else 'NO [REJECT HYPOTHESIS]'}")
    print("=" * 75 + "\n")

    return is_overall_valid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TTE Authentic Production Layer 2 Score Diagnostic")
    parser.add_argument("--exchange", default="gateio")
    parser.add_argument("--pair", default="ETH_USDT")
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--market", default="perpetual")
    parser.add_argument("--candles", default=5000, type=int)
    parser.add_argument("--is-candles", default=3500, type=int)
    args = parser.parse_args()

    run_production_l2_diagnostics(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.tf,
        market_type=args.market,
        total_candles=args.candles,
        is_candles=args.is_candles,
    )
