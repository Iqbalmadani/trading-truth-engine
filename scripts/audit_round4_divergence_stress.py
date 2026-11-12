import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_round4_stress_test():
    connector = get_connector("gateio")
    print("=" * 95)
    print("  TTE AUDIT ROUND 4 — STRESS-TEST CROSS-ASSET DIVERGENCE (INSTITUTIONAL PROTOCOL)")
    print("=" * 95)

    # =========================================================================
    # PART A: TRUE OUT-OF-SAMPLE TIME HOLDOUT
    # =========================================================================
    print("\n" + "=" * 95)
    print("  [PART A] TRUE OUT-OF-SAMPLE TIME HOLDOUT (AUG 2025 -> FEB 2026)")
    print("=" * 95)
    
    # In Sprint Fase 2, data was Feb 10, 2026 -> Sep 06, 2026.
    # The holdout end timestamp is 1770688800 - 3600 (Feb 10, 2026 01:00 UTC).
    HOLDOUT_END_TS = 1770688800 - 3600
    
    def fetch_holdout_candles(pair, n_target=4000):
        all_candles = []
        current_to = HOLDOUT_END_TS
        while len(all_candles) < n_target:
            limit = min(1000, n_target - len(all_candles))
            params = {"contract": pair, "interval": "1h", "limit": limit, "to": current_to}
            data = connector._request(f"{connector.BASE_URL}/futures/usdt/candlesticks", params)
            if not data or not isinstance(data, list) or len(data) == 0:
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
            batch = sorted(batch, key=lambda x: x["timestamp"])
            all_candles = batch + all_candles
            current_to = batch[0]["timestamp"] - 1
            if len(data) < limit:
                break
        return all_candles

    print("Fetching OOS holdout candles (pre-Feb 2026)...")
    eth_holdout = fetch_holdout_candles("ETH_USDT", 4000)
    btc_holdout = fetch_holdout_candles("BTC_USDT", 4000)
    
    t_start = datetime.datetime.fromtimestamp(eth_holdout[0]["timestamp"], tz=datetime.timezone.utc)
    t_end = datetime.datetime.fromtimestamp(eth_holdout[-1]["timestamp"], tz=datetime.timezone.utc)
    print(f"  ETH Holdout: {len(eth_holdout)} candles ({t_start} -> {t_end})")
    print(f"  BTC Holdout: {len(btc_holdout)} candles")

    def process_divergence_df(candles_alt, candles_btc):
        df_alt = pd.DataFrame(candles_alt).set_index("timestamp")
        df_btc = pd.DataFrame(candles_btc).set_index("timestamp")
        
        df = pd.DataFrame({
            "alt_close": df_alt["close"],
            "btc_close": df_btc["close"],
        }).dropna()
        
        # Returns
        df["alt_ret_12"] = np.log(df["alt_close"] / df["alt_close"].shift(12))
        df["btc_ret_12"] = np.log(df["btc_close"] / df["btc_close"].shift(12))
        df["alt_ret_24"] = np.log(df["alt_close"] / df["alt_close"].shift(24))
        df["btc_ret_24"] = np.log(df["btc_close"] / df["btc_close"].shift(24))
        
        # Predictors: Divergence (Alt - BTC)
        df["div_12c"] = df["alt_ret_12"] - df["btc_ret_12"]
        df["div_24c"] = df["alt_ret_24"] - df["btc_ret_24"]
        
        # Targets: Absolute Alt return AND Forward Return Spread (Alt_fwd - BTC_fwd)
        for h in [4, 12, 24]:
            df[f"alt_fwd_{h}"] = (df["alt_close"].shift(-h) - df["alt_close"]) / df["alt_close"]
            df[f"btc_fwd_{h}"] = (df["btc_close"].shift(-h) - df["btc_close"]) / df["btc_close"]
            df[f"spread_fwd_{h}"] = df[f"alt_fwd_{h}"] - df[f"btc_fwd_{h}"]
            
        return df.dropna()

    df_holdout = process_divergence_df(eth_holdout, btc_holdout)

    print("\n--- A.1 Holdout: Target = Absolute ETH Forward Return ---")
    for div_col, div_lbl in [("div_12c", "12c Divergence"), ("div_24c", "24c Divergence")]:
        for h in [4, 12, 24]:
            x = df_holdout[div_col].values
            y = df_holdout[f"alt_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  Holdout {div_lbl} -> ETH Fwd {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # =========================================================================
    # PART B: TARGET = FORWARD RETURN SPREAD (DOLLAR-NEUTRAL PAIR)
    # =========================================================================
    print("\n" + "=" * 95)
    print("  [PART B] SPREAD RETURN TARGET: Return_Spread = Return_ETH - Return_BTC")
    print("=" * 95)
    
    # 1. On Holdout Dataset (Aug 2025 -> Feb 2026)
    print("--- B.1 SPREAD Target on Holdout Dataset (N=3980, Aug 2025 -> Feb 2026) ---")
    for div_col, div_lbl in [("div_12c", "12c Divergence"), ("div_24c", "24c Divergence")]:
        for h in [4, 12, 24]:
            x = df_holdout[div_col].values
            y = df_holdout[f"spread_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  Holdout {div_lbl} -> Spread Fwd {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # 2. On Sprint Dataset (Feb 2026 -> Sep 2026, N=5000)
    print("\nFetching Sprint 2026 Dataset (ETH & BTC N=5000)...")
    eth_sprint = fetch_historical_candles(connector, "ETH_USDT", "1h", "perpetual", 5000)
    btc_sprint = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", 5000)
    df_sprint = process_divergence_df(eth_sprint, btc_sprint)
    
    print("\n--- B.2 SPREAD Target on Sprint Dataset (N=4976, Feb 2026 -> Sep 2026) ---")
    for div_col, div_lbl in [("div_12c", "12c Divergence"), ("div_24c", "24c Divergence")]:
        for h in [4, 12, 24]:
            x = df_sprint[div_col].values
            y = df_sprint[f"spread_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  Sprint {div_lbl} -> Spread Fwd {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # =========================================================================
    # PART C: NET FRICTION CHECK (TAKER FEES + SLIPPAGE + REAL FUNDING CASHFLOW)
    # =========================================================================
    print("\n" + "=" * 95)
    print("  [PART C] REALISTIC FRICTION CHECK INCLUDING HISTORICAL FUNDING RATES")
    print("=" * 95)
    # Fetch historical funding rates for ETH and BTC across Sprint period
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
            for r in res:
                records.append({"timestamp": int(r["t"]), "funding_rate": float(r["r"])})
            sorted_t = sorted([r["timestamp"] for r in records])
            earliest_t = sorted_t[0]
            if len(res) < 100:
                break
        df_fr = pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
        return df_fr

    df_eth_fr = fetch_all_funding("ETH_USDT", 1000)
    df_btc_fr = fetch_all_funding("BTC_USDT", 1000)

    # Merge funding rates
    df_sprint_friction = df_sprint.reset_index()
    df_sprint_friction = pd.merge_asof(df_sprint_friction, df_eth_fr.rename(columns={"funding_rate": "fr_eth"}), on="timestamp", direction="backward")
    df_sprint_friction = pd.merge_asof(df_sprint_friction, df_btc_fr.rename(columns={"funding_rate": "fr_btc"}), on="timestamp", direction="backward")
    df_sprint_friction["fr_eth"] = df_sprint_friction["fr_eth"].ffill().bfill()
    df_sprint_friction["fr_btc"] = df_sprint_friction["fr_btc"].ffill().bfill()

    # In a dollar-neutral mean-reversion trade:
    # When ETH has surged over BTC (Top 10% div_24c > p90), signal is SHORT ETH + LONG BTC.
    # Holding for 24 hours = 3 funding periods (each 8h).
    # Short ETH earns/pays: + fr_eth (if positive, short receives payment)
    # Long BTC earns/pays: - fr_btc (if positive, long pays payment)
    # Net Funding per period = (fr_eth - fr_btc)
    # Over 24h (~3 periods) = 3 * (fr_eth - fr_btc)
    # Gross trade return from short ETH + long BTC = -(alt_fwd_24) + (btc_fwd_24) = - spread_fwd_24
    
    p90 = np.percentile(df_sprint_friction["div_24c"], 90)
    trades = df_sprint_friction[df_sprint_friction["div_24c"] >= p90].copy()
    
    trades["gross_return"] = - trades["spread_fwd_24"] # profit when spread contracts
    trades["funding_pnl"] = 3.0 * (trades["fr_eth"] - trades["fr_btc"]) # 3 intervals of 8h funding
    
    # Friction costs:
    # Taker fee: 0.05% per leg per side. Entry (short ETH 0.05% + long BTC 0.05%) = 0.10%. Exit = 0.10%. Total = 0.20% (0.0020)
    # Slippage: 0.02% per leg per side = 0.08% (0.0008)
    TAKER_FEE = 0.0020
    SLIPPAGE = 0.0008
    TOTAL_EXECUTION_COST = TAKER_FEE + SLIPPAGE # 0.0028 = 28 bps
    
    trades["net_return"] = trades["gross_return"] + trades["funding_pnl"] - TOTAL_EXECUTION_COST
    
    n_trades = len(trades)
    win_rate = (trades["net_return"] > 0).mean() * 100
    mean_gross = trades["gross_return"].mean() * 100
    mean_funding = trades["funding_pnl"].mean() * 100
    mean_net = trades["net_return"].mean() * 100
    sum_net = trades["net_return"].sum() * 100
    
    gross_wins = trades[trades["gross_return"] > 0]["gross_return"].sum()
    gross_losses = abs(trades[trades["gross_return"] < 0]["gross_return"].sum())
    gross_pf = gross_wins / gross_losses if gross_losses > 0 else 0
    
    net_wins = trades[trades["net_return"] > 0]["net_return"].sum()
    net_losses = abs(trades[trades["net_return"] < 0]["net_return"].sum())
    net_pf = net_wins / net_losses if net_losses > 0 else 0

    print(f"Signal Definition: Top 10% 24c Divergence (div_24c >= {p90*100:+.2f}%) -> Short ETH / Long BTC (Hold 24h)")
    print(f"Total Trade Occurrences (N): {n_trades}")
    print(f"Average Gross Return: {mean_gross:+.4f}%")
    print(f"Average Net Funding PnL (3 periods): {mean_funding:+.4f}%")
    print(f"Execution Cost (Taker 20 bps + Slippage 8 bps): -{TOTAL_EXECUTION_COST*100:.2f}%")
    print(f"Average Net Return per Trade: {mean_net:+.4f}%")
    print(f"Gross Profit Factor: {gross_pf:.2f} | Net Profit Factor: {net_pf:.2f}")
    print(f"Net Win Rate: {win_rate:.1f}% | Cumulative Net Return: {sum_net:+.2f}%")

    # =========================================================================
    # PART D: TEST ON LESS CROWDED PAIRS (SOL/BTC and DOGE/BTC)
    # =========================================================================
    print("\n" + "=" * 95)
    print("  [PART D] DIVERGENCE TEST ON LESS CROWDED PAIRS (SOL/BTC & DOGE/BTC, N=5000)")
    print("=" * 95)
    print("Fetching SOL_USDT and DOGE_USDT candles...")
    sol_candles = fetch_historical_candles(connector, "SOL_USDT", "1h", "perpetual", 5000)
    doge_candles = fetch_historical_candles(connector, "DOGE_USDT", "1h", "perpetual", 5000)
    
    df_sol = process_divergence_df(sol_candles, btc_sprint)
    df_doge = process_divergence_df(doge_candles, btc_sprint)
    
    print("\n>>> D.1 Pair: SOL / BTC (N=4976) <<<")
    for div_col, div_lbl in [("div_12c", "12c SOL-BTC Div"), ("div_24c", "24c SOL-BTC Div")]:
        for h in [4, 12, 24]:
            x = df_sol[div_col].values
            y = df_sol[f"spread_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  {div_lbl} -> Spread Fwd {h:2d}c: rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    print("\n>>> D.2 Pair: DOGE / BTC (N=4976) <<<")
    for div_col, div_lbl in [("div_12c", "12c DOGE-BTC Div"), ("div_24c", "24c DOGE-BTC Div")]:
        for h in [4, 12, 24]:
            x = df_doge[div_col].values
            y = df_doge[f"spread_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  {div_lbl} -> Spread Fwd {h:2d}c: rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # =========================================================================
    # PART E: DATA GAP RESOLUTION (BTC CONTRACT_STATS & TEST RECONCILIATION)
    # =========================================================================
    print("\n" + "=" * 95)
    print("  [PART E] DATA GAP RESOLUTION: BTC CONTRACT_STATS & BONFERRONI RECONCILIATION")
    print("=" * 95)
    # Fetch BTC contract_stats
    print("Fetching BTC contract_stats from Gate.io...")
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
            sorted_t = sorted([r["timestamp"] for r in records])
            earliest_t = sorted_t[0]
            if len(res) < 1000:
                break
        return pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

    df_btc_stats = fetch_contract_stats("BTC_USDT", 2000)
    print(f"  BTC contract_stats fetched: {len(df_btc_stats)} rows")
    
    # Process BTC candles with contract stats
    df_btc_candles = pd.DataFrame(btc_sprint).sort_values("timestamp")
    df_btc_full = pd.merge_asof(df_btc_candles, df_btc_stats, on="timestamp", direction="backward")
    df_btc_full["oi_change_4c"] = (df_btc_full["open_interest"] - df_btc_full["open_interest"].shift(4)) / df_btc_full["open_interest"].shift(4)
    df_btc_full["net_liq_usd"] = df_btc_full["long_liq_usd"] - df_btc_full["short_liq_usd"]
    for h in [1, 4, 12, 24]:
        df_btc_full[f"fwd_ret_{h}"] = (df_btc_full["close"].shift(-h) - df_btc_full["close"]) / df_btc_full["close"]

    print("\n--- BTC Open Interest Change (4c) on contract_stats ---")
    for h in [1, 4, 12, 24]:
        sub = df_btc_full[["oi_change_4c", f"fwd_ret_{h}"]].dropna()
        if len(sub) > 10:
            rho, p = stats.spearmanr(sub["oi_change_4c"], sub[f"fwd_ret_{h}"])
            p90 = np.percentile(sub["oi_change_4c"], 90)
            p50 = np.percentile(sub["oi_change_4c"], 50)
            top10 = sub[sub["oi_change_4c"] >= p90][f"fwd_ret_{h}"]
            bot50 = sub[sub["oi_change_4c"] <= p50][f"fwd_ret_{h}"]
            mw_s, mw_p = stats.mannwhitneyu(top10, bot50, alternative='two-sided')
            print(f"  Horizon {h:2d}c: N={len(sub):2d} | rho={rho:+.4f} (p={p:.4e}) | Top10={top10.mean()*100:+.2f}% vs Bot50={bot50.mean()*100:+.2f}% (MW p={mw_p:.4e})")

    print("\n--- BTC Net Liquidation USD on contract_stats ---")
    for h in [1, 4, 12, 24]:
        sub = df_btc_full[["net_liq_usd", f"fwd_ret_{h}"]].dropna()
        if len(sub) > 10:
            rho, p = stats.spearmanr(sub["net_liq_usd"], sub[f"fwd_ret_{h}"])
            p90 = np.percentile(sub["net_liq_usd"], 90)
            p50 = np.percentile(sub["net_liq_usd"], 50)
            top10 = sub[sub["net_liq_usd"] >= p90][f"fwd_ret_{h}"]
            bot50 = sub[sub["net_liq_usd"] <= p50][f"fwd_ret_{h}"]
            mw_s, mw_p = stats.mannwhitneyu(top10, bot50, alternative='two-sided')
            print(f"  Horizon {h:2d}c: N={len(sub):2d} | rho={rho:+.4f} (p={p:.4e}) | Top10={top10.mean()*100:+.2f}% vs Bot50={bot50.mean()*100:+.2f}% (MW p={mw_p:.4e})")

if __name__ == "__main__":
    run_round4_stress_test()
