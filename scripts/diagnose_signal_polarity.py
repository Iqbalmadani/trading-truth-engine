"""
Diagnostic Research Script: Signal Polarity & Dynamic Regime Classification (Kat A Pure Research)

Evaluates whether market regime classification (Hurst Exponent) dynamically resolves
the signal polarity non-stationarity problem (Masalah 1).

Runs isolated on In-Sample historical data (3,500 candles), WITHOUT modifying production src/ code.

Usage:
    python scripts/diagnose_signal_polarity.py --exchange gateio --pair ETH_USDT --tf 1h --candles 5000 --is-candles 3500
    python scripts/diagnose_signal_polarity.py --exchange gateio --pair BTC_USDT --tf 1h --candles 5000 --is-candles 3500
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
from src.layer2.cpd import get_cpd_window
from src.layer2.zscore import zscore_in_window
from src.layer3.thresholds import get_score_thresholds, InsufficientScoreHistoryError
from src.layer3.risk import compute_atr

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def compute_hurst_exponent(price_series: List[float], max_lag: int = 100) -> float:
    """
    Calculate Hurst Exponent H using Rescaled Range (R/S) analysis.
    H > 0.55 -> Persistent / Trending
    H < 0.45 -> Anti-persistent / Mean-reverting
    0.45 <= H <= 0.55 -> Random walk / Neutral
    """
    if len(price_series) < 30:
        return 0.5

    try:
        prices = np.array(price_series, dtype=float)
        returns = np.diff(np.log(prices))
        if len(returns) < 20:
            return 0.5

        lags = [10, 20, 30, 50, max_lag]
        lags = [l for l in lags if l < len(returns) // 2]
        if len(lags) < 2:
            return 0.5

        rs_values = []
        for lag in lags:
            n_chunks = len(returns) // lag
            if n_chunks < 1:
                continue
            rs_chunk_list = []
            for k in range(n_chunks):
                chunk = returns[k * lag: (k + 1) * lag]
                mean_adj = chunk - np.mean(chunk)
                cum_sum = np.cumsum(mean_adj)
                r_val = np.max(cum_sum) - np.min(cum_sum)
                s_val = np.std(chunk)
                if s_val > 1e-12:
                    rs_chunk_list.append(r_val / s_val)
            if rs_chunk_list:
                rs_values.append((lag, np.mean(rs_chunk_list)))

        if len(rs_values) < 2:
            return 0.5

        log_lags = np.log([x[0] for x in rs_values])
        log_rs = np.log([x[1] for x in rs_values])

        poly = np.polyfit(log_lags, log_rs, 1)
        hurst = float(poly[0])
        return float(np.clip(hurst, 0.0, 1.0))
    except Exception as e:
        logger.debug(f"Hurst calculation failed: {e}")
        return 0.5


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

            # Binance returns oldest -> newest
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

            # Gate.io returns oldest -> newest when sorted
            batch = sorted(batch, key=lambda x: x["timestamp"])
            all_candles = batch + all_candles
            current_to = batch[0]["timestamp"]

            if len(data) < limit:
                break

        return all_candles[-n_candles:] if len(all_candles) > n_candles else all_candles

    else:
        raise ValueError(f"Unsupported exchange: {exchange}")


def run_diagnostics(
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str,
    total_candles: int,
    is_candles: int,
    hurst_window: int = 100,
):
    """Run isolated signal polarity diagnostic on In-Sample candles."""
    print("\n" + "=" * 70)
    print(f"  DIAGNOSTIC RESEARCH: SIGNAL POLARITY & REGIME CLASSIFICATION")
    print(f"  {exchange.upper()}/{pair}/{timeframe}")
    print(f"  Total candles: {total_candles} | In-Sample IS candles: {is_candles}")
    print(f"  Rolling Hurst Window: {hurst_window} candles")
    print("=" * 70 + "\n")

    connector = get_connector(exchange)
    logger.info(f"Fetching {total_candles} candles from {exchange}...")
    candles = fetch_historical_candles(connector, pair, timeframe, market_type, total_candles)

    if len(candles) < is_candles:
        logger.error(f"Insufficient candles fetched: got {len(candles)}, need {total_candles}")
        return False

    # Isolate In-Sample slice: candles[0 .. is_candles-1]
    is_slice = candles[:is_candles]
    logger.info(f"In-Sample Isolated Window: {len(is_slice)} candles (Index 0 .. {is_candles - 1})")
    logger.info(f"First TS: {is_slice[0]['timestamp']} | Last TS: {is_slice[-1]['timestamp']}")

    close_prices = [c["close"] for c in is_slice]
    high_prices = [c["high"] for c in is_slice]
    low_prices = [c["low"] for c in is_slice]

    score_history: List[float] = []
    records = []

    HORIZONS = [5, 10, 20, 50]
    BONFERRONI_ALPHA = 0.05 / 12.0  # 3 regimes x 4 horizons = 12 tests -> alpha = 0.00417

    logger.info("Evaluating L2 score distributions and Hurst regimes...")

    for i in range(50, len(is_slice) - 50):
        price_sub = close_prices[:i + 1]
        high_sub = high_prices[:i + 1]
        low_sub = low_prices[:i + 1]

        try:
            window, _ = get_cpd_window(price_sub[-250:], penalty=1.0)
            if len(window) >= 2:
                z = zscore_in_window(window, price_sub[-1])
                score = float(z) if not (np.isnan(z) or np.isinf(z)) else 0.0
            else:
                score = 0.0
        except Exception:
            score = 0.0

        score_history.append(score)
        if len(score_history) < 5:
            continue

        try:
            th_up, th_down = get_score_thresholds(score_history, penalty=1.0)
        except InsufficientScoreHistoryError:
            continue

        h_sub = price_sub[-hurst_window:] if len(price_sub) >= hurst_window else price_sub
        h_val = compute_hurst_exponent(h_sub, max_lag=min(50, len(h_sub) // 2))

        if h_val > 0.55:
            regime = "TRENDING"
        elif h_val < 0.45:
            regime = "MEAN_REVERTING"
        else:
            regime = "NEUTRAL"

        sig_breakout = "LONG" if (score > th_up and score > 0) else ("SHORT" if (score < th_down and score < 0) else "0")
        sig_meanrev = "SHORT" if (score > th_up and score > 0) else ("LONG" if (score < th_down and score < 0) else "0")

        if regime == "TRENDING":
            sig_dynamic = sig_breakout
        elif regime == "MEAN_REVERTING":
            sig_dynamic = sig_meanrev
        else:
            sig_dynamic = "0"

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
            "hurst": h_val,
            "regime": regime,
            "sig_breakout": sig_breakout,
            "sig_meanrev": sig_meanrev,
            "sig_dynamic": sig_dynamic,
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
    print(f"Evaluated {len(df)} total decision points in In-Sample dataset.")
    print(f"Regime Breakdown:")
    print(f"  - TRENDING (H > 0.55)      : {len(df[df['regime']=='TRENDING'])} samples ({len(df[df['regime']=='TRENDING'])/len(df)*100:.1f}%)")
    print(f"  - MEAN_REVERTING (H < 0.45): {len(df[df['regime']=='MEAN_REVERTING'])} samples ({len(df[df['regime']=='MEAN_REVERTING'])/len(df)*100:.1f}%)")
    print(f"  - NEUTRAL (0.45 <= H <= 0.55): {len(df[df['regime']=='NEUTRAL'])} samples ({len(df[df['regime']=='NEUTRAL'])/len(df)*100:.1f}%)")
    print()

    print("=" * 70)
    print("  1. SPEARMAN CORRELATION ANALYSIS (SCORE vs FORWARD RETURN)")
    print(f"  Bonferroni Adjusted Significance Threshold Alpha = {BONFERRONI_ALPHA:.5f}")
    print("=" * 70)

    valid_horizons_count = 0

    for regime_name in ["TRENDING", "MEAN_REVERTING"]:
        sub_df = df[df["regime"] == regime_name].dropna(subset=["score", "fwd_ret_10"])
        print(f"\n--- Regime: {regime_name} (N={len(sub_df)}) ---")
        for h in HORIZONS:
            valid_sub = sub_df.dropna(subset=[f"fwd_ret_{h}"])
            if len(valid_sub) < 10:
                print(f"  Horizon {h:2d}-candle: Insufficient samples")
                continue
            r_stat, p_val = stats.spearmanr(valid_sub["score"], valid_sub[f"fwd_ret_{h}"])
            sig_str = "[SIGNIFICANT]" if (p_val < BONFERRONI_ALPHA and abs(r_stat) > 0.15) else "[Not Significant]"

            if p_val < BONFERRONI_ALPHA and abs(r_stat) > 0.15:
                valid_horizons_count += 1

            print(
                f"  Horizon {h:2d}-candle: Spearman r = {r_stat:+.4f} | "
                f"p-val = {p_val:.6f} | Status: {sig_str}"
            )

    print("\n" + "=" * 70)
    print("  2. EXPECTANCY IN R-MULTIPLE COMPARISON (FORWARD PERFORMANCE)")
    print("=" * 70)

    def calc_expectancy(df_in, sig_col, h):
        act = df_in[df_in[sig_col] != "0"].dropna(subset=[f"fwd_r_{h}"])
        if len(act) == 0:
            return 0.0, 0
        returns = []
        for _, row in act.iterrows():
            mult = 1.0 if row[sig_col] == "LONG" else -1.0
            r_val = mult * row[f"fwd_r_{h}"]
            returns.append(r_val)
        return float(np.mean(returns)), len(returns)

    print(f"\n{'Horizon':<10} | {'Breakout (Static)':<20} | {'Mean-Rev (Static)':<20} | {'Dynamic Hurst (Proposed)':<25}")
    print("-" * 82)

    r_multiples_dynamic = {}
    for h in HORIZONS:
        e_b, n_b = calc_expectancy(df, "sig_breakout", h)
        e_m, n_m = calc_expectancy(df, "sig_meanrev", h)
        e_d, n_d = calc_expectancy(df, "sig_dynamic", h)
        r_multiples_dynamic[h] = e_d

        print(
            f"{h:2d}-candle   | "
            f"{e_b:+.3f}R (N={n_b:2d})         | "
            f"{e_m:+.3f}R (N={n_m:2d})         | "
            f"{e_d:+.3f}R (N={n_d:2d}) {'[PASS]' if e_d > 0.15 else '[FAIL]'}"
        )

    pos_r_horizons = sum(1 for h, r_val in r_multiples_dynamic.items() if r_val > 0.15)
    is_valid = (valid_horizons_count >= 2) or (pos_r_horizons >= 2)

    print("\n" + "=" * 70)
    print("  RESEARCH VALIDATION DECISION SUMMARY")
    print("=" * 70)
    print(f"  Asset / Pair             : {exchange.upper()}/{pair}/{timeframe}")
    print(f"  Bonferroni Significance  : {valid_horizons_count}/4 horizons passed (p < {BONFERRONI_ALPHA:.5f} & |r| > 0.15)")
    print(f"  Positive Expectancy (>0.15R): {pos_r_horizons}/4 horizons passed")
    print(f"  IS DIAGNOSTIC VALID?     : {'YES [PROCEED TO PHASE 2]' if is_valid else 'NO [REJECT HYPOTHESIS]'}")
    print("=" * 70 + "\n")

    return is_valid


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TTE Signal Polarity Diagnostic Research")
    parser.add_argument("--exchange", default="gateio")
    parser.add_argument("--pair", default="ETH_USDT")
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--market", default="perpetual")
    parser.add_argument("--candles", default=5000, type=int)
    parser.add_argument("--is-candles", default=3500, type=int)
    parser.add_argument("--hurst-window", default=100, type=int)
    args = parser.parse_args()

    run_diagnostics(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.tf,
        market_type=args.market,
        total_candles=args.candles,
        is_candles=args.is_candles,
        hurst_window=args.hurst_window,
    )
