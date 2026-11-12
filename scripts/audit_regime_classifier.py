import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from src.layer3.regime_classifier import compute_hurst_rs, compute_variance_ratio
from scripts.run_backtest import fetch_historical_ohlcv

def test_regime_classification_fair():
    print("=" * 80)
    print("  TTE AUDIT ROUND 2 — PART B: REGIME CLASSIFIER FAIR GATE TEST")
    print("=" * 80)

    connector = get_connector("gateio")
    for pair in ["ETH_USDT", "BTC_USDT"]:
        candles = fetch_historical_ohlcv(connector, pair, "1h", "perpetual", 500)
        closes = [c["close"] for c in candles]
        
        # Test 1: with significance_level = 0.0 (the fake gate currently in yaml)
        # Test 2: with significance_level = 1.96 (standard 95% confidence)
        # Test 3: with significance_level = 2.49 (Bonferroni for 4 horizons)

        for sig_name, sig_level in [("Fake Gate (0.0)", 0.0), ("Standard 95% (1.96)", 1.96), ("Bonferroni (2.49)", 2.49)]:
            counts = {"trending": 0, "mean_reverting": 0, "insufficient_data": 0}
            window_size = 40
            total_points = len(closes) - window_size

            for i in range(window_size, len(closes)):
                window = closes[i - window_size : i + 1]
                h = compute_hurst_rs(window)
                vr, vr_z = compute_variance_ratio(window, lag=2)

                if h > 0.58:
                    if vr >= 1.0 and (sig_level <= 0.0 or vr_z >= sig_level):
                        counts["trending"] += 1
                    else:
                        counts["insufficient_data"] += 1
                elif h < 0.48:
                    if vr <= 1.0 and (sig_level <= 0.0 or vr_z <= -sig_level):
                        counts["mean_reverting"] += 1
                    else:
                        counts["insufficient_data"] += 1
                else:
                    counts["insufficient_data"] += 1

            t_pct = counts["trending"] / total_points * 100
            mr_pct = counts["mean_reverting"] / total_points * 100
            in_pct = counts["insufficient_data"] / total_points * 100

            print(f"\n{pair} 1h (N={total_points}) | Significance Level: {sig_name}")
            print(f"  - TRENDING          : {counts['trending']:3d} ({t_pct:.1f}%)")
            print(f"  - MEAN-REVERTING    : {counts['mean_reverting']:3d} ({mr_pct:.1f}%)")
            print(f"  - INSUFFICIENT DATA : {counts['insufficient_data']:3d} ({in_pct:.1f}%) [Signal Return '0']")

if __name__ == "__main__":
    test_regime_classification_fair()
