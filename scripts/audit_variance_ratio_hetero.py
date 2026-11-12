import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.run_backtest import fetch_historical_ohlcv

def compute_vr_heteroskedastic(prices: list, q: int = 2):
    """
    Lo-MacKinlay (1988) Heteroskedasticity-Robust Variance Ratio Test z*(q).
    Accounts for time-varying volatility and conditional heteroskedasticity.
    """
    p = np.array(prices, dtype=float)
    if len(p) < q + 5:
        return 1.0, 0.0, 0.0

    r = np.diff(np.log(p))
    n = len(r)
    mu = np.mean(r)

    # 1-period variance (unbiased)
    var_1 = np.sum((r - mu) ** 2) / n

    # q-period overlapping returns
    # Overlapping sum of returns: r_t(q) = sum_{j=0}^{q-1} r_{t-j}
    r_q = np.array([np.sum(r[t - q + 1 : t + 1]) for t in range(q - 1, n)])
    m = n - q + 1
    # Lo-MacKinlay unbiased multiplier: m = q * (n - q + 1) * (1 - q/n)
    unbiased_scale = m * (1.0 - q / float(n)) if (1.0 - q / float(n)) > 0 else m
    var_q = np.sum((r_q - q * mu) ** 2) / unbiased_scale

    vr = var_q / (q * var_1) if var_1 > 1e-15 else 1.0

    # Homoscedastic asymptotic variance
    phi_homo = (2.0 * (2 * q - 1) * (q - 1)) / (3.0 * q)
    se_homo = np.sqrt(phi_homo / n)
    z_homo = (vr - 1.0) / se_homo if se_homo > 1e-12 else 0.0

    # Heteroskedasticity-consistent asymptotic variance theta(q)
    # theta(q) = sum_{j=1}^{q-1} [ 2*(q-j)/q ]^2 * delta(j)
    # where delta(j) = sum_{t=j+1}^n (r_t - mu)^2 * (r_{t-j} - mu)^2 / [ sum_{t=1}^n (r_t - mu)^2 ]^2
    denom_sq = (np.sum((r - mu) ** 2)) ** 2
    theta = 0.0
    for j in range(1, q):
        weight = ((2.0 * (q - j)) / float(q)) ** 2
        num = np.sum(((r[j:] - mu) ** 2) * ((r[:-j] - mu) ** 2))
        delta_j = (n * num) / denom_sq if denom_sq > 1e-15 else 0.0
        theta += weight * delta_j

    se_hetero = np.sqrt(theta / n) if theta > 0 and n > 0 else 1.0
    z_hetero = (vr - 1.0) / se_hetero if se_hetero > 1e-12 else 0.0

    return float(vr), float(z_homo), float(z_hetero)

def test_variance_ratio_robust():
    print("=" * 85)
    print("  TTE AUDIT ROUND 3 — PART C: VARIANCE RATIO HETEROSKEDASTIC-ROBUST TEST")
    print("  Testing Lo-MacKinlay (1988) Homoscedastic z vs Heteroskedastic-Robust z* across lags q")
    print("=" * 85)

    connector = get_connector("gateio")
    for pair in ["ETH_USDT", "BTC_USDT"]:
        candles = fetch_historical_ohlcv(connector, pair, "1h", "perpetual", 500)
        closes = [c["close"] for c in candles]

        print(f"\nAsset: {pair} 1h (N={len(closes)} candles)")
        print(f"{'Lag q':<8} | {'VR(q)':<10} | {'z (Homo)':<12} | {'z* (Hetero-Robust)':<20} | {'Status at 95% (|z*| > 1.96)':<25}")
        print("-" * 80)

        for q in [2, 4, 8, 16]:
            vr, z_homo, z_hetero = compute_vr_heteroskedastic(closes, q=q)
            if abs(z_hetero) > 1.96:
                status = "REJECT RW: TRENDING" if z_hetero > 0 else "REJECT RW: MEAN-REV"
            else:
                status = "RANDOM WALK (p > 0.05)"
            print(f"q = {q:<5} | {vr:<10.4f} | {z_homo:<+12.4f} | {z_hetero:<+20.4f} | {status}")

if __name__ == "__main__":
    test_variance_ratio_robust()
