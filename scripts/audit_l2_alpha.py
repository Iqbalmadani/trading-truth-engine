import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import ruptures as rpt

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from src.layer2.zscore import zscore_in_window
from scripts.run_backtest import fetch_historical_ohlcv, fetch_historical_scope

DATASETS = [
    ("gateio", "ETH_USDT", "1h", 400),
    ("gateio", "ETH_USDT", "4h", 400),
    ("gateio", "BTC_USDT", "1h", 400),
    ("gateio", "BTC_USDT", "4h", 400),
]

HORIZONS = [1, 4, 12, 24]
TOTAL_TESTS = len(DATASETS) * len(HORIZONS)  # 16 tests
BONFERRONI_ALPHA = 0.05 / TOTAL_TESTS        # 0.003125

def fast_cpd_window(data, pen=2.0):
    if len(data) < 3:
        return data, [len(data)]
    arr = np.array(data, dtype=float)
    jump_val = 3 if len(arr) > 60 else 1
    algo = rpt.Pelt(model="l2", min_size=3, jump=jump_val).fit(arr)
    cps = algo.predict(pen=pen)
    last_cp = cps[-2] if len(cps) > 1 else 0
    return data[last_cp:], cps

def run_l2_alpha_audit():
    print("=" * 80, flush=True)
    print("  TTE AUDIT ROUND 2 — PART A: LAYER 2 RAW ALPHA FOUNDATION TEST", flush=True)
    print(f"  Bonferroni Adjusted Alpha Threshold: {BONFERRONI_ALPHA:.6f} (0.05 / {TOTAL_TESTS})", flush=True)
    print("=" * 80, flush=True)

    overall_results = []

    for exchange, pair, tf, n_candles in DATASETS:
        print(f"\nEvaluating: {exchange.upper()} | {pair} | {tf} | {n_candles} candles...", flush=True)
        connector = get_connector(exchange)
        candles = fetch_historical_ohlcv(connector, pair, tf, "perpetual", n_candles)
        if not candles or len(candles) < 100:
            print(f"  FAILED to fetch candles for {pair} {tf}", flush=True)
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
            scope_slice = {}
            for scope, history in scope_histories.items():
                slice_end = min(i + 1, len(history))
                scope_slice[scope] = history[:slice_end]

            z_scores = {}
            for scope, data in scope_slice.items():
                recent_data = data[-150:] if len(data) > 150 else data
                if len(recent_data) < 3:
                    continue
                try:
                    var = float(np.var(recent_data))
                    pen = max(1.0, 2.0 * var * np.log(len(recent_data))) if var > 1e-12 else 1.0
                    window, _ = fast_cpd_window(recent_data, pen=pen)
                    if len(window) >= 2:
                        z = zscore_in_window(window, recent_data[-1])
                        z_scores[scope] = float(z) if not (np.isnan(z) or np.isinf(z)) else 0.0
                except Exception:
                    pass

            raw_score = sum(z_scores.values())
            curr_price = close_prices[i]
            rec = {
                "candle_idx": i,
                "score": raw_score,
            }
            for h in HORIZONS:
                future_price = close_prices[i + h]
                fwd_ret = (future_price - curr_price) / curr_price
                rec[f"fwd_ret_{h}"] = fwd_ret

            records.append(rec)

        df = pd.DataFrame(records)
        print(f"  Valid sample points: N = {len(df)}", flush=True)
        print(f"  Raw Score Stats: Mean = {df['score'].mean():+.4f} | Std = {df['score'].std():.4f} | Range = [{df['score'].min():+.2f}, {df['score'].max():+.2f}]", flush=True)

        dataset_res = {"dataset": f"{pair} {tf}", "N": len(df), "horizons": {}}
        for h in HORIZONS:
            r_stat, p_val = stats.spearmanr(df["score"], df[f"fwd_ret_{h}"])
            is_bonferroni_sig = (p_val < BONFERRONI_ALPHA)
            has_effect_size = (abs(r_stat) >= 0.10)
            is_valid_alpha = is_bonferroni_sig and has_effect_size

            dataset_res["horizons"][h] = {
                "spearman_r": r_stat,
                "p_value": p_val,
                "bonferroni_sig": is_bonferroni_sig,
                "valid_alpha": is_valid_alpha,
            }
            if is_valid_alpha:
                sig_label = "[SIGNIFICANT ALPHA]"
            elif is_bonferroni_sig:
                sig_label = "[p < alpha but EFFECT SIZE WEAK (|r|<0.10)]"
            else:
                sig_label = "[NOT SIGNIFICANT]"

            print(f"    Horizon {h:2d}c: Spearman r = {r_stat:+.4f} | p-value = {p_val:.6f} | {sig_label}", flush=True)

        overall_results.append(dataset_res)

    print("\n" + "=" * 80, flush=True)
    print("  FINAL EVALUATION OF LAYER 2 ALPHA FOUNDATION", flush=True)
    print("=" * 80, flush=True)
    passed_alpha = 0
    for res in overall_results:
        for h, v in res["horizons"].items():
            if v["valid_alpha"]:
                passed_alpha += 1

    print(f"  Tests passing Bonferroni correction (p < {BONFERRONI_ALPHA:.6f} AND |r| >= 0.10): {passed_alpha} / {TOTAL_TESTS}", flush=True)
    if passed_alpha == 0:
        print("\n  >>> KESIMPULAN AUDIT: GAGAL TOTAL. Raw Layer 2 Z-Score TIDAK memiliki predictive alpha signifikan terhadap forward return.", flush=True)
        print("  >>> Ini bukti empiris bahwa Layer 2 saat ini TIDAK memiliki edge/informasi arah harga.", flush=True)
        print("  >>> Seluruh riset Layer 3 (threshold, regime classifier, polarity) sedang membangun di atas fondasi tanpa alpha.", flush=True)
    else:
        print(f"\n  >>> KESIMPULAN AUDIT: Ditemukan alpha signifikan pada {passed_alpha} / {TOTAL_TESTS} pengujian.", flush=True)

if __name__ == "__main__":
    run_l2_alpha_audit()
