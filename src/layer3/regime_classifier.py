"""
Layer 3 — Structural Regime Classifier

Determines whether the market is currently in a trending or mean-reverting regime
using multi-scale Rescaled Range (R/S) Hurst Exponent and Lo-MacKinlay (1988)
Variance Ratio Test with analytical z-statistics.

Regimes:
- 'trending': Hurst > H_trend AND Variance Ratio > 1.0 (z > +1.96) -> Breakout entry
- 'mean_reverting': Hurst < H_mr AND Variance Ratio < 1.0 (z < -1.96) -> Reversal entry
- 'insufficient_data': Short series, ambiguous Hurst, or inconsistent VR z-statistic -> Skip
"""

import logging
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any
import numpy as np
import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "regime_classifier.yaml"

_DEFAULT_CONFIG = {
    "hurst": {
        "trending_threshold": 0.58,
        "mean_revert_threshold": 0.52,
        "min_window_length": 30,
    },
    "variance_ratio": {
        "lag": 2,
        "significance_level": 1.96,
    }
}


def load_regime_config() -> Dict[str, Any]:
    """Load regime classifier configuration from YAML or fallback to defaults."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f)
            if isinstance(cfg, dict):
                return cfg
        except Exception as e:
            logger.warning(f"Failed to load {CONFIG_PATH}: {e}, using defaults")
    return _DEFAULT_CONFIG


def compute_hurst_rs(series: List[float], min_sub_n: int = 8) -> float:
    """
    Multi-scale Rescaled Range (R/S) Hurst Exponent.

    Algoritma:
    1. Convert ke log returns: r_t = ln(P_t / P_{t-1})
    2. Untuk setiap sub-window size n in {min_sub_n, ..., N//2}:
       a. Bagi series jadi non-overlapping chunks of size n
       b. Untuk setiap chunk:
          - Hitung mean-adjusted cumulative deviation: Y_t = sum(r_i - r_mean)
          - R = max(Y) - min(Y)
          - S = std(r_i)
          - R/S ratio = R / S
       c. Rata-ratakan R/S ratio dari semua chunks -> expected R/S untuk size n
    3. Regresi log-log: ln(E[R/S]) = H * ln(n) + c
       H = slope dari regresi = Hurst Exponent

    Args:
        series: Price series (bukan returns)
        min_sub_n: Minimum sub-window size (default 8)

    Returns:
        H: Hurst Exponent bounded in [0.0, 1.0]
    """
    prices = np.array(series, dtype=float)
    if len(prices) < min_sub_n * 2:
        return 0.5

    returns = np.diff(np.log(prices))
    N = len(returns)
    if N < min_sub_n * 2:
        return 0.5

    # Sub-window sizes n
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
            std = float(np.std(chunk, ddof=1)) if len(chunk) > 1 else float(np.std(chunk))
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


def compute_variance_ratio(
    series: List[float], lag: int = 2
) -> Tuple[float, float]:
    """
    Lo-MacKinlay (1988) Variance Ratio Test WITH analytical homoscedastic z-statistic.

    Algoritma:
    1. Hitung log returns: r_t = ln(P_t / P_{t-1})
    2. VR(q) = Var(q-period returns) / (q * Var(1-period returns))
    3. Z-statistic (homoscedastic, Lo-MacKinlay 1988):
       phi = 2 * (2q - 1) * (q - 1) / (3 * q)
       z = (VR - 1) / sqrt(phi / n)
       Null hypothesis: VR = 1 (random walk)
       |z| > 1.96 -> reject null di 95% confidence

    Args:
        series: Price series (bukan returns)
        lag: Aggregation period q (default 2)

    Returns:
        Tuple[float, float]: (variance_ratio, z_statistic)
        z > +1.96 -> significantly trending
        z < -1.96 -> significantly mean-reverting
        |z| <= 1.96 -> random walk (insignificant)
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


def classify_regime(
    price_window: List[float],
    min_window_length: Optional[int] = None,
) -> str:
    """
    Classify current price window as 'trending', 'mean_reverting', or 'insufficient_data'.

    Args:
        price_window: Price data from Layer 2 CPD window
        min_window_length: Minimum data points for reliable classification

    Returns:
        'trending' | 'mean_reverting' | 'insufficient_data'
    """
    cfg = load_regime_config()
    h_cfg = cfg.get("hurst", {})
    vr_cfg = cfg.get("variance_ratio", {})

    trending_threshold = float(h_cfg.get("trending_threshold", 0.58))
    mean_revert_threshold = float(h_cfg.get("mean_revert_threshold", 0.52))
    min_len = min_window_length if min_window_length is not None else int(h_cfg.get("min_window_length", 30))
    lag = int(vr_cfg.get("lag", 2))
    sig_level = float(vr_cfg.get("significance_level", 1.96))

    if price_window is None or len(price_window) < min_len:
        return "insufficient_data"

    h = compute_hurst_rs(price_window)
    vr, vr_z = compute_variance_ratio(price_window, lag=lag)

    logger.debug(f"Regime classification: H={h:.4f}, VR={vr:.4f}, z={vr_z:.4f} (N={len(price_window)})")

    if h > trending_threshold:
        if vr >= 1.0 and (sig_level <= 0.0 or vr_z >= sig_level):
            return "trending"
        return "insufficient_data"  # Inconsistent with VR confirmation
    elif h < mean_revert_threshold:
        if vr <= 1.0 and (sig_level <= 0.0 or vr_z <= -sig_level):
            return "mean_reverting"
        return "insufficient_data"  # Inconsistent with VR confirmation
    else:
        return "insufficient_data"  # Ambiguous zone
