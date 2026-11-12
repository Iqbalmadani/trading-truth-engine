"""
Phase 1.3 Diagnostic: Scope Correlation Analysis

Fetches historical data across all active scopes (ohlcv, funding_rate, volatility_compression),
computes time-series of z-scores per scope using the Layer 2 pipeline, and calculates
the Pearson correlation matrix between scopes.

Validates the assumption that scope signals are sufficiently independent to support
simple sum aggregation in Layer 2.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from typing import Dict, List, Any
from src.layer1.connectors.registry import get_connector
from src.layer1.verifier import Verifier
from src.layer2.cpd import get_cpd_window
from src.layer2.zscore import zscore_in_window
from src.layer2.meta_learning import meta_learn_all_scopes
from src.utils.config_loader import get_sources_config


def analyze_scope_correlations(
    exchange_name: str = "gateio",
    pair: str = "ETH_USDT",
    timeframe: str = "1h",
    candles: int = 500,
) -> Dict[str, Any]:
    print(f"\n--- Scope Correlation Analysis: {exchange_name.upper()} {pair} {timeframe} ({candles} candles) ---")
    connector = get_connector(exchange_name)
    verifier = Verifier()
    
    # 1. Existence check across scopes
    l1_results = verifier.verify_all(connector, pair=pair, timeframe=timeframe, market_type="perpetual")
    active_scopes = [s for s, ok in l1_results.items() if ok == 1]
    print(f"Active scopes ({len(active_scopes)}): {active_scopes}")

    if len(active_scopes) < 2:
        print("Less than 2 active scopes available for correlation analysis.")
        return {}

    # 2. Fetch full historical data per scope
    scope_data = {}
    sources_config = get_sources_config()
    scopes_cfg = sources_config.get("scopes", {})

    for scope in active_scopes:
        data_type = scopes_cfg.get(scope, {}).get("data_type", "historical")
        if data_type == "historical":
            history = connector.fetch_history(scope, pair, timeframe, "perpetual", candles)
            if history:
                if scope == "ohlcv":
                    scope_data[scope] = [float(c["close"]) if isinstance(c, dict) else float(c) for c in history]
                elif scope == "volatility_compression":
                    # numeric values
                    scope_data[scope] = [float(c["volatility_percent"]) if isinstance(c, dict) else float(c) for c in history]
                else:
                    scope_data[scope] = [float(c["value"]) if isinstance(c, dict) else float(c) for c in history]
        else:
            # Snapshot data: fetch history
            history = connector.fetch_history(scope, pair, timeframe, "perpetual", candles)
            if history:
                scope_data[scope] = [float(c["value"]) if isinstance(c, dict) else float(c) for c in history]

    # Verify matching lengths
    available_scopes = [s for s, d in scope_data.items() if len(d) >= 100]
    if len(available_scopes) < 2:
        print(f"Not enough historical scope data (available: {available_scopes})")
        return {}

    min_len = min(len(scope_data[s]) for s in available_scopes)
    print(f"Usable scope series length: {min_len} points across scopes: {available_scopes}")
    for s in available_scopes:
        scope_data[s] = scope_data[s][-min_len:]

    # 3. Meta-learn penalties for active scopes
    calib = meta_learn_all_scopes(connector, exchange_name, pair, timeframe, "perpetual", lookback_periods=min(min_len, 200))
    penalties = calib.get("penalties", {})
    models = calib.get("models", {})

    # 4. Compute walk-forward z-score series for each scope
    z_series = {s: [] for s in available_scopes}
    
    warmup = 30
    for i in range(warmup, min_len):
        for s in available_scopes:
            sub = scope_data[s][:i]
            pen = penalties.get(s, 1.0)
            mod = models.get(s, "rbf")
            try:
                window, _ = get_cpd_window(sub, pen, model=mod)
                if len(window) > 2:
                    ref = window[:-1]
                    z = zscore_in_window(ref, sub[-1])
                else:
                    z = zscore_in_window(window, sub[-1])
                z_series[s].append(float(z) if not np.isnan(z) and not np.isinf(z) else 0.0)
            except Exception:
                z_series[s].append(0.0)

    # 5. Calculate Pearson Correlation Matrix
    scope_names = list(available_scopes)
    matrix = np.zeros((len(scope_names), len(scope_names)))
    
    for i, s1 in enumerate(scope_names):
        for j, s2 in enumerate(scope_names):
            if i == j:
                matrix[i, j] = 1.0
            else:
                arr1 = np.array(z_series[s1])
                arr2 = np.array(z_series[s2])
                corr = float(np.corrcoef(arr1, arr2)[0, 1]) if len(arr1) > 10 else 0.0
                matrix[i, j] = 0.0 if np.isnan(corr) else corr

    print("\n--- Pearson Correlation Matrix of Scope Z-Scores ---")
    header = f"{'Scope':25s} | " + " | ".join(f"{s[:12]:12s}" for s in scope_names)
    print(header)
    print("-" * len(header))
    
    max_cross_corr = 0.0
    for i, s1 in enumerate(scope_names):
        row_str = f"{s1:25s} | " + " | ".join(f"{matrix[i, j]:12.4f}" for j in range(len(scope_names)))
        print(row_str)
        for j in range(len(scope_names)):
            if i != j:
                max_cross_corr = max(max_cross_corr, abs(matrix[i, j]))

    print("-" * len(header))
    print(f"Maximum absolute cross-correlation: {max_cross_corr:.4f}")
    if max_cross_corr < 0.3:
        print("VERDICT: All cross-correlations < 0.3 -> Simple sum aggregation is fully justified.")
    elif max_cross_corr < 0.6:
        print("VERDICT: Moderate cross-correlation (< 0.6) -> Simple sum remains valid, monitor for v2.1.")
    else:
        print("VERDICT: High cross-correlation (>= 0.6) -> Flagged for Information Coefficient (IC) weighting in v2.1.")

    return {
        "scopes": scope_names,
        "matrix": matrix.tolist(),
        "max_cross_corr": max_cross_corr,
    }


def main():
    print("=" * 60)
    print("PHASE 1.3: SCOPE CORRELATION DIAGNOSTIC")
    print("=" * 60)

    res_eth = analyze_scope_correlations("gateio", "ETH_USDT", "1h", 500)
    res_btc = analyze_scope_correlations("gateio", "BTC_USDT", "1h", 500)

if __name__ == "__main__":
    main()
