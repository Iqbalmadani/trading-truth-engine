import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from src.layer2.zscore import zscore_in_window
from scripts.run_backtest import fetch_historical_ohlcv, fetch_historical_scope
from scripts.audit_l2_alpha import fast_cpd_window

DATASETS = [
    ("gateio", "ETH_USDT", "1h", 400),
    ("gateio", "ETH_USDT", "4h", 400),
    ("gateio", "BTC_USDT", "1h", 400),
    ("gateio", "BTC_USDT", "4h", 400),
]

HORIZONS = [1, 4, 12, 24]
TOTAL_TESTS = len(DATASETS) * len(HORIZONS)  # 16 tests
BONFERRONI_ALPHA = 0.05 / TOTAL_TESTS        # 0.003125

def run_tail_test():
    print("=" * 85, flush=True)
    print("  TTE AUDIT ROUND 3 — PART B: TAIL-CONDITIONAL FORWARD RETURN TEST", flush=True)
    print("  Comparing Top 10% |Score| Extremes vs Bottom 50% Neutral/Middle", flush=True)
    print(f"  Bonferroni Adjusted Alpha Threshold: {BONFERRONI_ALPHA:.6f} (0.05 / {TOTAL_TESTS})", flush=True)
    print("=" * 85, flush=True)

    summary_rows = []

    for exchange, pair, tf, n_candles in DATASETS:
        connector = get_connector(exchange)
        candles = fetch_historical_ohlcv(connector, pair, tf, "perpetual", n_candles)
        if not candles or len(candles) < 100:
            continue

        scope_histories = {}
        for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
            hist = fetch_historical_scope(connector, pair, tf, "perpetual", scope, n_candles)
            if hist and len(hist) >= 40:
                scope_histories[scope] = hist

        close_prices = [c["close"] for c in candles]
        min_warmup = 40
        records = []

        for i in range(min_warmup, len(candles) - max(HORIZONS)):
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

            raw_score = sum(z_scores.values())
            curr_price = close_prices[i]
            rec = {
                "candle_idx": i,
                "score": raw_score,
                "abs_score": abs(raw_score),
            }
            for h in HORIZONS:
                future_price = close_prices[i + h]
                # Directional aligned return: if score > 0 -> LONG return; if score < 0 -> SHORT return
                raw_ret = (future_price - curr_price) / curr_price
                aligned_ret = (np.sign(raw_score) * raw_ret) if abs(raw_score) > 1e-6 else 0.0
                rec[f"fwd_ret_{h}"] = raw_ret
                rec[f"aligned_ret_{h}"] = aligned_ret
                rec[f"abs_ret_{h}"] = abs(raw_ret)

            records.append(rec)

        df = pd.DataFrame(records)
        q90 = df["abs_score"].quantile(0.90)
        q50 = df["abs_score"].quantile(0.50)

        top_10 = df[df["abs_score"] >= q90]
        bottom_50 = df[df["abs_score"] <= q50]

        print(f"\n{pair} {tf} (Total N={len(df)}) | Top 10% Threshold |Score| >= {q90:.2f} (N={len(top_10)}) vs Bottom 50% <= {q50:.2f} (N={len(bottom_50)})", flush=True)

        for h in HORIZONS:
            # 1. Test Absolute Volatility Excursion (Does extreme score predict bigger move regardless of direction?)
            u_vol, p_vol = stats.mannwhitneyu(top_10[f"abs_ret_{h}"], bottom_50[f"abs_ret_{h}"], alternative="two-sided")
            mean_vol_top = top_10[f"abs_ret_{h}"].mean() * 100
            mean_vol_bot = bottom_50[f"abs_ret_{h}"].mean() * 100

            # 2. Test Directionally Aligned Return (Does extreme score predict correct direction move?)
            u_dir, p_dir = stats.mannwhitneyu(top_10[f"aligned_ret_{h}"], bottom_50[f"aligned_ret_{h}"], alternative="two-sided")
            mean_dir_top = top_10[f"aligned_ret_{h}"].mean() * 100
            mean_dir_bot = bottom_50[f"aligned_ret_{h}"].mean() * 100

            sig_dir = "[PASS]" if p_dir < BONFERRONI_ALPHA else "[FAIL]"

            print(f"  Horizon {h:2d}c:", flush=True)
            print(f"    - Move Magnitude |Ret| : Top10% = {mean_vol_top:.2f}% vs Bot50% = {mean_vol_bot:.2f}% | p = {p_vol:.6f} {'[SIG]' if p_vol < BONFERRONI_ALPHA else ''}", flush=True)
            print(f"    - Directional Return   : Top10% = {mean_dir_top:+.2f}% vs Bot50% = {mean_dir_bot:+.2f}% | Mann-Whitney p = {p_dir:.6f} | {sig_dir}", flush=True)

            summary_rows.append({
                "dataset": f"{pair} {tf}",
                "horizon": f"{h}c",
                "top10_dir_ret": f"{mean_dir_top:+.2f}%",
                "bot50_dir_ret": f"{mean_dir_bot:+.2f}%",
                "dir_p_value": p_dir,
                "bonferroni_sig": p_dir < BONFERRONI_ALPHA,
                "top10_mag": f"{mean_vol_top:.2f}%",
                "bot50_mag": f"{mean_vol_bot:.2f}%",
                "mag_p_value": p_vol,
                "mag_sig": p_vol < BONFERRONI_ALPHA,
            })

    print("\n" + "=" * 85, flush=True)
    print("  TAIL-CONDITIONAL AUDIT SUMMARY", flush=True)
    print("=" * 85, flush=True)
    passed_dir = sum(1 for r in summary_rows if r["bonferroni_sig"])
    passed_mag = sum(1 for r in summary_rows if r["mag_sig"])
    print(f"  1. Directional Edge in Extremes (Top 10% vs Bot 50%): {passed_dir} / {TOTAL_TESTS} passed Bonferroni", flush=True)
    print(f"  2. Volatility / Magnitude Edge in Extremes          : {passed_mag} / {TOTAL_TESTS} passed Bonferroni", flush=True)

if __name__ == "__main__":
    run_tail_test()
