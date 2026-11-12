import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_fase2_sprint():
    connector = get_connector("gateio")
    print("=" * 90)
    print("  TTE v2 FINAL RESEARCH SPRINT — INDIVIDUAL SCOPE & DEEP DATASET AUDIT (N=5000)")
    print("=" * 90)

    N_CANDLES = 5000
    HORIZONS = [1, 4, 12, 24]
    
    # 1. Fetch 5,000 candles for ETH and BTC 1h
    print(f"\n[1/5] Fetching {N_CANDLES} candles (1h) for ETH_USDT and BTC_USDT from Gate.io...")
    eth_candles = fetch_historical_candles(connector, "ETH_USDT", "1h", "perpetual", N_CANDLES)
    btc_candles = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", N_CANDLES)
    print(f"  ETH: {len(eth_candles)} candles ({datetime.datetime.fromtimestamp(eth_candles[0]['timestamp'], tz=datetime.timezone.utc)} to {datetime.datetime.fromtimestamp(eth_candles[-1]['timestamp'], tz=datetime.timezone.utc)})")
    print(f"  BTC: {len(btc_candles)} candles ({datetime.datetime.fromtimestamp(btc_candles[0]['timestamp'], tz=datetime.timezone.utc)} to {datetime.datetime.fromtimestamp(btc_candles[-1]['timestamp'], tz=datetime.timezone.utc)})")

    # 2. Fetch historical funding rates with pagination
    print("\n[2/5] Fetching historical funding rates (paginated)...")
    def fetch_all_funding(contract, target_count=1000):
        records = []
        earliest_t = None
        while len(records) < target_count:
            params = {"contract": contract, "limit": 100}
            if earliest_t:
                params["to"] = earliest_t - 1
            res = connector._request(f"{connector.BASE_URL}/futures/usdt/funding_rate", params)
            if not res or not isinstance(res, list) or len(res) == 0:
                break
            # res is newest to oldest
            for r in res:
                records.append({"timestamp": int(r["t"]), "funding_rate": float(r["r"])})
            earliest_t = records[-1]["timestamp"]
            if len(res) < 100:
                break
        df_fr = pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return df_fr

    df_eth_fr = fetch_all_funding("ETH_USDT", 1000)
    df_btc_fr = fetch_all_funding("BTC_USDT", 1000)
    print(f"  ETH Funding Rate records: {len(df_eth_fr)} | BTC Funding Rate records: {len(df_btc_fr)}")

    # 3. Fetch contract_stats for Open Interest & Liquidations (Gate.io)
    print("\n[3/5] Fetching historical Open Interest & Liquidations (contract_stats)...")
    def fetch_contract_stats(contract, target_count=2000):
        records = []
        earliest_t = None
        while len(records) < target_count:
            params = {"contract": contract, "limit": 1000}
            if earliest_t:
                params["to"] = earliest_t - 1
            res = connector._request(f"{connector.BASE_URL}/futures/usdt/contract_stats", params)
            if not res or not isinstance(res, list) or len(res) == 0:
                break
            for r in res:
                records.append({
                    "timestamp": int(r["time"]),
                    "open_interest": float(r.get("open_interest", 0)),
                    "long_liq_usd": float(r.get("long_liq_usd", 0)),
                    "short_liq_usd": float(r.get("short_liq_usd", 0)),
                })
            earliest_t = records[0]["timestamp"] if records[0]["timestamp"] < records[-1]["timestamp"] else records[-1]["timestamp"]
            # Gate.io returns oldest to newest or newest to oldest? Let's sort
            sorted_t = sorted([r["timestamp"] for r in records])
            earliest_t = sorted_t[0]
            if len(res) < 1000:
                break
        df_stats = pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return df_stats

    df_eth_stats = fetch_contract_stats("ETH_USDT", 2000)
    print(f"  ETH contract_stats fetched: {len(df_eth_stats)} rows (Span: {len(df_eth_stats)*5/60:.1f} hours)")

    # 4. Process datasets
    def build_dataset(candles, df_fr, df_stats=None):
        df = pd.DataFrame(candles).sort_values("timestamp")
        
        # Scopes: Price Momentum
        df["ret_1c"] = np.log(df["close"] / df["close"].shift(1))
        df["mom_12c"] = np.log(df["close"] / df["close"].shift(12))
        df["mom_24c"] = np.log(df["close"] / df["close"].shift(24))
        
        # Scope: Volatility Compression (20c rolling std)
        df["vol_comp_20"] = df["ret_1c"].rolling(20).std()
        
        # Merge Funding Rate (asof backward)
        df = pd.merge_asof(df, df_fr, on="timestamp", direction="backward")
        df["funding_rate"] = df["funding_rate"].ffill().bfill()
        
        # Merge contract_stats if provided
        if df_stats is not None and not df_stats.empty:
            df = pd.merge_asof(df, df_stats, on="timestamp", direction="backward")
            df["oi_change_4c"] = (df["open_interest"] - df["open_interest"].shift(4)) / df["open_interest"].shift(4)
            df["net_liq_usd"] = df["long_liq_usd"] - df["short_liq_usd"]
        
        # Forward returns
        for h in HORIZONS:
            df[f"fwd_ret_{h}"] = (df["close"].shift(-h) - df["close"]) / df["close"]
            
        return df

    df_eth = build_dataset(eth_candles, df_eth_fr, df_eth_stats)
    df_btc = build_dataset(btc_candles, df_btc_fr)

    print("\n" + "=" * 90)
    print("  PART 1: INDIVIDUAL SCOPE FORWARD-RETURN CORRELATION & TAIL TEST (FULL N=5000)")
    print("=" * 90)
    
    # 2 Assets (ETH, BTC) x 4 scopes x 4 horizons = 32 tests
    # Bonferroni Alpha = 0.05 / 32 = 0.00156
    BONF_ALPHA = 0.05 / 32
    print(f"Bonferroni threshold (32 tests): p < {BONF_ALPHA:.6f}, Target |rho| >= 0.05\n")

    for asset_name, df_data in [("ETH_USDT 1h", df_eth), ("BTC_USDT 1h", df_btc)]:
        print(f">>> ASSET: {asset_name} (N={len(df_data)}) <<<")
        scopes = [
            ("Price Momentum (12c)", "mom_12c"),
            ("Price Momentum (24c)", "mom_24c"),
            ("Volatility Compression (20c)", "vol_comp_20"),
            ("Funding Rate (8h)", "funding_rate"),
        ]
        if "oi_change_4c" in df_data.columns:
            scopes.append(("Open Interest Change (4c)", "oi_change_4c"))
            scopes.append(("Net Liquidation USD", "net_liq_usd"))
            
        for scope_label, col in scopes:
            print(f"  [Scope: {scope_label}]")
            for h in HORIZONS:
                sub = df_data[[col, f"fwd_ret_{h}"]].dropna()
                if len(sub) < 50:
                    continue
                x = sub[col].values
                y = sub[f"fwd_ret_{h}"].values
                
                rho, p_spearman = stats.spearmanr(x, y)
                
                p90 = np.percentile(x, 90)
                p50 = np.percentile(x, 50)
                top10_y = y[x >= p90]
                bot50_y = y[x <= p50]
                
                mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
                pass_bonf = (p_spearman < BONF_ALPHA) and (abs(rho) >= 0.05)
                pass_tail = (mw_p < BONF_ALPHA)
                
                status = "PASS EDGE" if (pass_bonf and pass_tail) else ("WEAK CORR" if pass_bonf else "NO EDGE")
                print(f"    Horizon {h:2d}c: N={len(x):4d} | Spearman rho={rho:+.4f} (p={p_spearman:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e}) | {status}")
            print()

    print("\n" + "=" * 90)
    print("  PART 2: CROSS-ASSET SCOPE — ETH vs BTC MOMENTUM DIVERGENCE")
    print("=" * 90)
    df_cross = pd.merge(
        df_eth[["timestamp", "mom_12c", "mom_24c", "fwd_ret_4", "fwd_ret_12", "fwd_ret_24"]],
        df_btc[["timestamp", "mom_12c", "mom_24c"]],
        on="timestamp",
        suffixes=("_eth", "_btc")
    ).dropna()
    df_cross["divergence_12c"] = df_cross["mom_12c_eth"] - df_cross["mom_12c_btc"]
    df_cross["divergence_24c"] = df_cross["mom_24c_eth"] - df_cross["mom_24c_btc"]

    for div_col, div_name in [("divergence_12c", "12-Candle ETH-BTC Divergence"), ("divergence_24c", "24-Candle ETH-BTC Divergence")]:
        print(f"  [Cross-Asset: {div_name}]")
        for h in [4, 12, 24]:
            x = df_cross[div_col].values
            y = df_cross[f"fwd_ret_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            pass_bonf = (p_val < BONF_ALPHA) and (abs(rho) >= 0.05)
            print(f"    Forward {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e}) | {'PASS' if pass_bonf else 'NO EDGE'}")
        print()

    print("\n" + "=" * 90)
    print("  PART 3: CANONICAL LAYER 2 AGGREGATE (SUM OF Z-SCORE) ON FULL N=5000 DATASET")
    print("=" * 90)
    for asset_name, df_data in [("ETH_USDT 1h", df_eth), ("BTC_USDT 1h", df_btc)]:
        # Calculate canonical rolling 40 zscore
        z_mom = (df_data["ret_1c"] - df_data["ret_1c"].rolling(40).mean()) / df_data["ret_1c"].rolling(40).std()
        z_vol = (df_data["vol_comp_20"] - df_data["vol_comp_20"].rolling(40).mean()) / df_data["vol_comp_20"].rolling(40).std()
        z_fr = (df_data["funding_rate"] - df_data["funding_rate"].rolling(40).mean()) / df_data["funding_rate"].rolling(40).std()
        z_fr = z_fr.fillna(0)
        
        df_data["raw_sum_zscore"] = z_mom - z_vol + z_fr
        
        print(f">>> Canonical Sum of Z-Scores: {asset_name} (N={len(df_data)}) <<<")
        for h in HORIZONS:
            sub = df_data[["raw_sum_zscore", f"fwd_ret_{h}"]].dropna()
            x = sub["raw_sum_zscore"].values
            y = sub[f"fwd_ret_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            pass_bonf = (p_val < BONF_ALPHA) and (abs(rho) >= 0.05)
            print(f"    Horizon {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e}) | {'PASS' if pass_bonf else 'NO EDGE'}")
        print()

    print("\n" + "=" * 90)
    print("  PART 4: HETEROSKEDASTIC VARIANCE RATIO TEST (N=5000, 208 DAYS)")
    print("=" * 90)
    for pair_name, df_asset in [("ETH_USDT 1h", df_eth), ("BTC_USDT 1h", df_btc)]:
        p = df_asset["close"].values
        returns = np.diff(np.log(p))
        n = len(returns)
        mu = np.mean(returns)
        
        print(f"\n{pair_name} (N={n} returns, Feb 10, 2026 -> Sep 06, 2026):")
        for q in [2, 4, 8, 16, 24]:
            q_returns = np.array([np.sum(returns[i:i+q]) for i in range(n - q + 1)])
            sigma_a = np.sum((returns - mu) ** 2) / n
            sigma_c = np.sum((q_returns - q * mu) ** 2) / ((n - q + 1) * q)
            vr = sigma_c / sigma_a
            
            delta = []
            for j in range(1, q):
                num = np.sum(((returns[j:] - mu) ** 2) * ((returns[:-j] - mu) ** 2))
                den = (np.sum((returns - mu) ** 2)) ** 2
                delta.append(((2 * (q - j) / q) ** 2) * (num / den) * n)
            theta_q = sum(delta)
            
            z_star = (vr - 1.0) / np.sqrt(theta_q / n)
            p_val = 2 * (1 - stats.norm.cdf(abs(z_star)))
            print(f"  q={q:2d}: VR={vr:.4f} | z*(q)={z_star:+.4f} | p-value={p_val:.4f} | Reject Random Walk? {'YES' if p_val < 0.05 else 'NO (Pure Random Walk)'}")

if __name__ == "__main__":
    run_fase2_sprint()
