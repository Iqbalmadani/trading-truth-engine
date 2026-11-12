"""
Phase 1.2 Diagnostic: Hurst Exponent & Variance Ratio Calibration

Fetches historical data (ETH_USDT + BTC_USDT, Gate.io, 1h, 500-1000 candles),
extracts CPD regime windows with PELT, calculates Multi-scale R/S Hurst and
Lo-MacKinlay Variance Ratio Test, and evaluates forward return predictability.

Determines empirical thresholds for config/regime_classifier.yaml.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from typing import List, Tuple, Dict, Any
from src.layer1.connectors.registry import get_connector
from src.layer2.cpd import get_cpd_window
from src.layer3.meta_learning import meta_learn_price_penalty


def compute_hurst_rs(series: List[float], min_sub_n: int = 8) -> float:
    """
    Multi-scale Rescaled Range (R/S) Hurst Exponent.
    
    1. Convert prices to log returns
    2. Divide series into sub-windows of size n
    3. Calculate cumulative mean-adjusted deviations, Range R and Std S
    4. Average R/S across chunks
    5. Regress ln(E[R/S]) on ln(n) -> slope = H
    """
    prices = np.array(series, dtype=float)
    if len(prices) < min_sub_n * 2:
        return 0.5
        
    returns = np.diff(np.log(prices))
    N = len(returns)
    if N < min_sub_n * 2:
        return 0.5

    # Generate sub-window sizes n
    sizes = []
    curr = min_sub_n
    max_size = N // 2
    while curr <= max_size:
        sizes.append(curr)
        curr = int(curr * 1.5) if int(curr * 1.5) > curr else curr + 1
    if max_size not in sizes and max_size >= min_sub_n:
        sizes.append(max_size)

    rs_values = []
    valid_sizes = []
    for n in sizes:
        n_chunks = N // n
        if n_chunks < 1:
            continue
        chunk_rs = []
        for i in range(n_chunks):
            chunk = returns[i * n : (i + 1) * n]
            std = np.std(chunk, ddof=1) if len(chunk) > 1 else np.std(chunk)
            if std < 1e-12:
                continue
            mean_adj = chunk - np.mean(chunk)
            cum_dev = np.cumsum(mean_adj)
            R = float(np.max(cum_dev) - np.min(cum_dev))
            chunk_rs.append(R / std)
        if chunk_rs:
            rs_values.append(float(np.mean(chunk_rs)))
            valid_sizes.append(n)

    if len(valid_sizes) < 2:
        return 0.5

    log_n = np.log(valid_sizes)
    log_rs = np.log(rs_values)
    poly = np.polyfit(log_n, log_rs, 1)
    H = float(poly[0])
    return max(0.0, min(1.0, H))


def compute_variance_ratio(series: List[float], lag: int = 2) -> Tuple[float, float]:
    """
    Lo-MacKinlay (1988) Variance Ratio Test with homoscedastic z-statistic.
    
    VR(q) = Var(q-period returns) / (q * Var(1-period returns))
    z = (VR - 1) / sqrt(phi / n)
    where phi = 2*(2q-1)*(q-1) / (3*q)
    """
    prices = np.array(series, dtype=float)
    if len(prices) < lag + 3:
        return 1.0, 0.0
        
    returns = np.diff(np.log(prices))
    n = len(returns)
    if n < lag + 2:
        return 1.0, 0.0

    var_1 = float(np.var(returns, ddof=1))
    if var_1 < 1e-15:
        return 1.0, 0.0

    q = lag
    # Overlapping q-period returns
    q_returns = [np.sum(returns[i : i + q]) for i in range(n - q + 1)]
    var_q = float(np.var(q_returns, ddof=1))

    vr = var_q / (q * var_1)

    phi = (2.0 * (2 * q - 1) * (q - 1)) / (3.0 * q)
    se = np.sqrt(phi / n) if phi > 0 and n > 0 else 1.0
    z = (vr - 1.0) / se if se > 1e-12 else 0.0

    return float(vr), float(z)


def analyze_pair(exchange_name: str, pair: str, timeframe: str = "1h", candles: int = 1000) -> Dict[str, Any]:
    print(f"\n--- Analyzing {exchange_name.upper()} {pair} {timeframe} ({candles} candles) ---")
    connector = get_connector(exchange_name)
    ohlcv = connector.fetch_history("ohlcv", pair, timeframe, "perpetual", candles)
    
    if not ohlcv or len(ohlcv) < 100:
        print(f"Error: Insufficient data fetched ({len(ohlcv) if ohlcv else 0})")
        return {}

    # Extract closes
    closes = [float(c["close"]) if isinstance(c, dict) else float(c) for c in ohlcv]
    
    # Run penalty optimization on price series
    penalty, ch, model = meta_learn_price_penalty(closes)
    print(f"Meta-learned price penalty: {penalty:.6f}, model: {model}, CH: {ch:.4f}")

    # Extract all CPD windows using a rolling walk-forward or sequential segment detection
    # We will slide window across the series to collect CPD windows of length >= 20
    window_data = []
    
    # Step size of 20 candles
    step = 10
    min_window_len = 20
    
    for i in range(100, len(closes), step):
        sub_series = closes[:i]
        try:
            regime_window, num_cps = get_cpd_window(sub_series, penalty, model=model)
            w_len = len(regime_window)
            if w_len >= min_window_len:
                h = compute_hurst_rs(regime_window)
                vr, z = compute_variance_ratio(regime_window, lag=2)
                
                # Check forward return over next 5 candles (empirical median duration)
                fwd_5 = (closes[min(i + 5, len(closes) - 1)] - closes[i - 1]) / closes[i - 1] if i + 5 < len(closes) else None
                # Check regime trend direction
                regime_dir = 1 if regime_window[-1] > regime_window[0] else -1
                
                window_data.append({
                    "index": i,
                    "length": w_len,
                    "hurst": h,
                    "vr": vr,
                    "z": z,
                    "fwd_5": fwd_5,
                    "regime_dir": regime_dir,
                })
        except Exception as e:
            continue

    if not window_data:
        print("No valid CPD windows extracted.")
        return {}

    hursts = [w["hurst"] for w in window_data]
    vrs = [w["vr"] for w in window_data]
    zs = [w["z"] for w in window_data]
    lengths = [w["length"] for w in window_data]

    print(f"Total Regime Windows Analyzed: {len(window_data)}")
    print(f"  CPD Window Length: Mean={np.mean(lengths):.1f}, Median={np.median(lengths):.1f}, Min={np.min(lengths)}, Max={np.max(lengths)}")
    print(f"  Hurst Exponent (H): Mean={np.mean(hursts):.4f}, Std={np.std(hursts):.4f}, P25={np.percentile(hursts, 25):.4f}, Median={np.median(hursts):.4f}, P75={np.percentile(hursts, 75):.4f}")
    print(f"  Variance Ratio (VR): Mean={np.mean(vrs):.4f}, Median={np.median(vrs):.4f}")
    print(f"  Lo-MacKinlay Z-stat: Mean={np.mean(zs):.4f}, Median={np.median(zs):.4f}")

    # Forward predictability check:
    # When H > 0.55 & z > 1.96, does forward return continue in regime_dir?
    trending_hits = []
    mr_hits = []
    for w in window_data:
        if w["fwd_5"] is not None:
            fwd_dir = 1 if w["fwd_5"] > 0 else -1
            if w["hurst"] > 0.55 and w["z"] > 1.96:
                trending_hits.append(1 if fwd_dir == w["regime_dir"] else 0)
            elif w["hurst"] < 0.45 and w["z"] < -1.96:
                mr_hits.append(1 if fwd_dir != w["regime_dir"] else 0)

    print(f"\nEmpirical Directional Alignment:")
    if trending_hits:
        print(f"  Trending Regime (H > 0.55, z > 1.96): N={len(trending_hits)}, Continuation Accuracy={np.mean(trending_hits)*100:.1f}%")
    else:
        print(f"  Trending Regime (H > 0.55, z > 1.96): N=0")
        
    if mr_hits:
        print(f"  Mean-Reverting Regime (H < 0.45, z < -1.96): N={len(mr_hits)}, Reversal Accuracy={np.mean(mr_hits)*100:.1f}%")
    else:
        print(f"  Mean-Reverting Regime (H < 0.45, z < -1.96): N=0")

    return {
        "pair": pair,
        "n_windows": len(window_data),
        "hurst_mean": float(np.mean(hursts)),
        "hurst_median": float(np.median(hursts)),
        "hurst_p25": float(np.percentile(hursts, 25)),
        "hurst_p75": float(np.percentile(hursts, 75)),
        "vr_mean": float(np.mean(vrs)),
        "window_len_median": float(np.median(lengths)),
        "window_len_min": int(np.min(lengths)),
        "trending_acc": float(np.mean(trending_hits)) if trending_hits else None,
        "mr_acc": float(np.mean(mr_hits)) if mr_hits else None,
    }


def main():
    print("=" * 60)
    print("PHASE 1.2: HURST EXPONENT & VARIANCE RATIO DIAGNOSTIC")
    print("=" * 60)

    res_eth = analyze_pair("gateio", "ETH_USDT", "1h", 1000)
    res_btc = analyze_pair("gateio", "BTC_USDT", "1h", 1000)

    print("\n" + "=" * 60)
    print("SUMMARY OF EMPIRICAL THRESHOLDS:")
    print("=" * 60)
    
    all_p25 = [r["hurst_p25"] for r in [res_eth, res_btc] if "hurst_p25" in r]
    all_p75 = [r["hurst_p75"] for r in [res_eth, res_btc] if "hurst_p75" in r]
    
    h_mr = float(np.mean(all_p25)) if all_p25 else 0.45
    h_trend = float(np.mean(all_p75)) if all_p75 else 0.55
    min_w = 30  # Standard statistical stability minimum for R/S multi-scale

    print(f"Calibrated H_TRENDING_THRESHOLD:   {h_trend:.2f}")
    print(f"Calibrated H_MEAN_REVERT_THRESHOLD: {h_mr:.2f}")
    print(f"Calibrated MIN_HURST_WINDOW:        {min_w}")
    print("=" * 60)

if __name__ == "__main__":
    main()
