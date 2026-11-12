import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_round5_validation():
    connector = get_connector("gateio")
    print("=" * 100)
    print("  TTE AUDIT ROUND 5 — RIGOROUS VALIDATION OF DOGE/BTC & CATEGORY DIVERGENCE")
    print("=" * 100)

    # =========================================================================
    # PART A: HOLDOUT WAKTU MURNI UNTUK DOGE/BTC (AUG 2025 -> FEB 2026)
    # =========================================================================
    print("\n" + "=" * 100)
    print("  [PART A] TRUE TIME HOLDOUT: DOGE/BTC (AUG 27, 2025 -> FEB 10, 2026)")
    print("=" * 100)
    
    HOLDOUT_END_TS = 1770688800 - 3600 # Feb 10, 2026 01:00 UTC
    
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

    print("Fetching DOGE and BTC holdout candles...")
    doge_holdout = fetch_holdout_candles("DOGE_USDT", 4000)
    btc_holdout = fetch_holdout_candles("BTC_USDT", 4000)
    print(f"  DOGE Holdout fetched: {len(doge_holdout)} candles")
    print(f"  BTC Holdout fetched: {len(btc_holdout)} candles")

    def process_pair_df(candles_alt, candles_btc):
        df_alt = pd.DataFrame(candles_alt).set_index("timestamp")
        df_btc = pd.DataFrame(candles_btc).set_index("timestamp")
        df = pd.DataFrame({
            "alt_close": df_alt["close"],
            "btc_close": df_btc["close"],
        }).dropna()
        
        df["alt_ret_12"] = np.log(df["alt_close"] / df["alt_close"].shift(12))
        df["btc_ret_12"] = np.log(df["btc_close"] / df["btc_close"].shift(12))
        df["alt_ret_24"] = np.log(df["alt_close"] / df["alt_close"].shift(24))
        df["btc_ret_24"] = np.log(df["btc_close"] / df["btc_close"].shift(24))
        
        df["div_12c"] = df["alt_ret_12"] - df["btc_ret_12"]
        df["div_24c"] = df["alt_ret_24"] - df["btc_ret_24"]
        
        for h in [4, 12, 24]:
            df[f"alt_fwd_{h}"] = (df["alt_close"].shift(-h) - df["alt_close"]) / df["alt_close"]
            df[f"btc_fwd_{h}"] = (df["btc_close"].shift(-h) - df["btc_close"]) / df["btc_close"]
            df[f"spread_fwd_{h}"] = df[f"alt_fwd_{h}"] - df[f"btc_fwd_{h}"]
            
        return df.dropna()

    df_doge_holdout = process_pair_df(doge_holdout, btc_holdout)

    print("\n--- A.1 DOGE/BTC Forward Return Spread on Holdout (Aug 2025 -> Feb 2026, N=3953) ---")
    for div_col, div_lbl in [("div_12c", "12c Divergence"), ("div_24c", "24c Divergence")]:
        for h in [4, 12, 24]:
            x = df_doge_holdout[div_col].values
            y = df_doge_holdout[f"spread_fwd_{h}"].values
            rho, p_val = stats.spearmanr(x, y)
            p90 = np.percentile(x, 90)
            p50 = np.percentile(x, 50)
            top10_y = y[x >= p90]
            bot50_y = y[x <= p50]
            mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
            print(f"  Holdout {div_lbl} -> Spread Fwd {h:2d}c: N={len(x)} | rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # =========================================================================
    # PART B: FRICTION CHECK SPESIFIK DOGE (ORDERBOOK DEPTH & HISTORICAL FUNDING)
    # =========================================================================
    print("\n" + "=" * 100)
    print("  [PART B] REALISTIC FRICTION CHECK SPESIFIK DOGE/BTC")
    print("=" * 100)
    
    # 1. Order book depth check
    ob = connector._request(f"{connector.BASE_URL}/futures/usdt/order_book", {"contract": "DOGE_USDT", "limit": 20})
    if ob and "bids" in ob and "asks" in ob:
        best_bid = float(ob["bids"][0]["p"])
        best_ask = float(ob["asks"][0]["p"])
        spread_bps = (best_ask - best_bid) / best_bid * 10000
        
        # Calculate cumulative depth up to 10 bps from best price
        # contract size on Gate.io is 10 DOGE per contract
        quanto = 10.0
        bid_depth_10bps = sum(float(b["s"]) * quanto * float(b["p"]) for b in ob["bids"] if (best_bid - float(b["p"]))/best_bid <= 0.001)
        ask_depth_10bps = sum(float(a["s"]) * quanto * float(a["p"]) for a in ob["asks"] if (float(a["p"]) - best_ask)/best_ask <= 0.001)
        print(f"DOGE Order Book Analysis:")
        print(f"  Best Bid: {best_bid:.5f} | Best Ask: {best_ask:.5f} | Raw Spread: {spread_bps:.2f} bps")
        print(f"  Available Liquidity within 10 bps (~0.1%): Bids=${bid_depth_10bps:,.2f} | Asks=${ask_depth_10bps:,.2f}")
        
    # 2. Historical funding rates for DOGE and BTC
    print("\nFetching historical funding rates for DOGE_USDT...")
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
        return pd.DataFrame(records).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

    df_doge_fr = fetch_all_funding("DOGE_USDT", 1000)
    df_btc_fr = fetch_all_funding("BTC_USDT", 1000)
    print(f"  DOGE Funding Rate records: {len(df_doge_fr)} | BTC Funding Rate records: {len(df_btc_fr)}")

    # Fetch Sprint 5000 candles for DOGE and BTC
    print("Fetching Sprint 5000 candles for DOGE and BTC...")
    doge_sprint = fetch_historical_candles(connector, "DOGE_USDT", "1h", "perpetual", 5000)
    btc_sprint = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", 5000)
    df_doge_sprint = process_pair_df(doge_sprint, btc_sprint).reset_index()

    # Merge funding rates
    df_doge_sprint = pd.merge_asof(df_doge_sprint, df_doge_fr.rename(columns={"funding_rate": "fr_doge"}), on="timestamp", direction="backward")
    df_doge_sprint = pd.merge_asof(df_doge_sprint, df_btc_fr.rename(columns={"funding_rate": "fr_btc"}), on="timestamp", direction="backward")
    df_doge_sprint["fr_doge"] = df_doge_sprint["fr_doge"].ffill().bfill()
    df_doge_sprint["fr_btc"] = df_doge_sprint["fr_btc"].ffill().bfill()

    # Simulate Short DOGE + Long BTC when div_24c in Top 10%
    p90 = np.percentile(df_doge_sprint["div_24c"], 90)
    trades = df_doge_sprint[df_doge_sprint["div_24c"] >= p90].copy()
    
    trades["gross_return"] = - trades["spread_fwd_24"]
    trades["funding_pnl"] = 3.0 * (trades["fr_doge"] - trades["fr_btc"]) # 3 funding periods (24h)
    
    # Gate.io standard taker fee is 0.075% per side.
    # 4 executions (Entry Short DOGE + Long BTC, Exit Cover DOGE + Sell BTC):
    # 4 * 0.075% = 0.30%
    # Slippage: DOGE spread is ~1.5 bps + price impact ~2.5 bps = ~4 bps per leg.
    # Total slippage for 4 executions = 4 * 0.04% = 0.16%
    TAKER_FEE_4LEGS = 0.0030 # 30 bps
    SLIPPAGE_4LEGS = 0.0016   # 16 bps
    TOTAL_FRICTION = TAKER_FEE_4LEGS + SLIPPAGE_4LEGS # 46 bps (0.0046)
    
    trades["net_return_taker"] = trades["gross_return"] + trades["funding_pnl"] - TOTAL_FRICTION
    
    # Also calculate Maker Execution (Maker rebate -0.01% on Gate.io or 0.015% retail):
    # If retail maker fee is 0.015% per leg = 0.06% + 0 slippage:
    MAKER_FEE_4LEGS = 0.0006 # 6 bps
    trades["net_return_maker"] = trades["gross_return"] + trades["funding_pnl"] - MAKER_FEE_4LEGS

    n_trades = len(trades)
    mean_gross = trades["gross_return"].mean() * 100
    mean_fr = trades["funding_pnl"].mean() * 100
    mean_net_taker = trades["net_return_taker"].mean() * 100
    mean_net_maker = trades["net_return_maker"].mean() * 100
    
    gross_pf = trades[trades["gross_return"] > 0]["gross_return"].sum() / abs(trades[trades["gross_return"] < 0]["gross_return"].sum())
    net_taker_pf = trades[trades["net_return_taker"] > 0]["net_return_taker"].sum() / abs(trades[trades["net_return_taker"] < 0]["net_return_taker"].sum())
    net_maker_pf = trades[trades["net_return_maker"] > 0]["net_return_maker"].sum() / abs(trades[trades["net_return_maker"] < 0]["net_return_maker"].sum())
    
    taker_wr = (trades["net_return_taker"] > 0).mean() * 100
    maker_wr = (trades["net_return_maker"] > 0).mean() * 100

    print(f"\nDOGE/BTC Friction Stress-Test Results (N={n_trades} trades):")
    print(f"  Signal Threshold: div_24c >= {p90*100:+.2f}%")
    print(f"  Average Gross Return: {mean_gross:+.4f}%")
    print(f"  Average Net Funding PnL (3 periods): {mean_fr:+.4f}%")
    print(f"  Taker Execution Cost (Fee 30 bps + Slippage 16 bps = 46 bps): -{TOTAL_FRICTION*100:.2f}%")
    print(f"  >>> Average Net Return (Taker Execution): {mean_net_taker:+.4f}% | PF: {net_taker_pf:.2f} | Win Rate: {taker_wr:.1f}%")
    print(f"  >>> Average Net Return (Maker Execution, 6 bps fee): {mean_net_maker:+.4f}% | PF: {net_maker_pf:.2f} | Win Rate: {maker_wr:.1f}%")

    # =========================================================================
    # PART C: PERLUAS SAMPLE KATEGORI (RETAIL/MEME VS INSTITUSIONAL/LARGE-CAP)
    # =========================================================================
    print("\n" + "=" * 100)
    print("  [PART C] CATEGORY EXPANSION: RETAIL/MEME VS INSTITUTIONAL/LARGE-CAP (N=5000)")
    print("=" * 100)
    
    pairs_retail = ["PEPE_USDT", "SHIB_USDT"]
    pairs_inst = ["AVAX_USDT", "LINK_USDT"]
    
    print("Fetching candles for category testing...")
    category_dfs = {}
    for p in pairs_retail + pairs_inst:
        print(f"  Fetching {p}...")
        candles = fetch_historical_candles(connector, p, "1h", "perpetual", 5000)
        category_dfs[p] = process_pair_df(candles, btc_sprint)
        
    print("\n>>> Category 1: Retail / Meme (High-Beta Pump & Dump) <<<")
    for p in ["DOGE_USDT"] + pairs_retail:
        df_p = df_doge_sprint if p == "DOGE_USDT" else category_dfs[p]
        print(f"\n--- Asset: {p} vs BTC ---")
        for div_col, div_lbl in [("div_12c", "12c Div"), ("div_24c", "24c Div")]:
            for h in [12, 24]:
                x = df_p[div_col].values
                y = df_p[f"spread_fwd_{h}"].values
                rho, p_val = stats.spearmanr(x, y)
                p90 = np.percentile(x, 90)
                p50 = np.percentile(x, 50)
                top10_y = y[x >= p90]
                bot50_y = y[x <= p50]
                mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
                print(f"  {div_lbl} -> Fwd {h:2d}c Spread: rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    print("\n>>> Category 2: Institutional / Large-Cap (High-Liquidity / Trend Continuity) <<<")
    for p in ["SOL_USDT"] + pairs_inst:
        if p == "SOL_USDT":
            # fetch SOL
            sol_candles = fetch_historical_candles(connector, "SOL_USDT", "1h", "perpetual", 5000)
            df_p = process_pair_df(sol_candles, btc_sprint)
        else:
            df_p = category_dfs[p]
        print(f"\n--- Asset: {p} vs BTC ---")
        for div_col, div_lbl in [("div_12c", "12c Div"), ("div_24c", "24c Div")]:
            for h in [12, 24]:
                x = df_p[div_col].values
                y = df_p[f"spread_fwd_{h}"].values
                rho, p_val = stats.spearmanr(x, y)
                p90 = np.percentile(x, 90)
                p50 = np.percentile(x, 50)
                top10_y = y[x >= p90]
                bot50_y = y[x <= p50]
                mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
                print(f"  {div_lbl} -> Fwd {h:2d}c Spread: rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

if __name__ == "__main__":
    run_round5_validation()
