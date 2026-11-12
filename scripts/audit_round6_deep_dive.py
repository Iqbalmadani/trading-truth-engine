import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_round6_audit():
    connector = get_connector("gateio")
    print("=" * 105)
    print("  TTE AUDIT ROUND 6 — DEEP DIVE VALIDATION & DATA INTEGRITY: AVAX/BTC & LINK/BTC")
    print("=" * 105)

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

    # =========================================================================
    # PART A: TRUE TIME HOLDOUT (AUG 2025 -> FEB 2026)
    # =========================================================================
    print("\n" + "=" * 105)
    print("  [PART A] TRUE TIME HOLDOUT VALIDATION (AUG 27, 2025 -> FEB 10, 2026)")
    print("=" * 105)
    print("Fetching holdout candles for BTC, AVAX, and LINK...")
    btc_holdout = fetch_holdout_candles("BTC_USDT", 4000)
    avax_holdout = fetch_holdout_candles("AVAX_USDT", 4000)
    link_holdout = fetch_holdout_candles("LINK_USDT", 4000)
    print(f"  Holdout candles fetched: BTC={len(btc_holdout)}, AVAX={len(avax_holdout)}, LINK={len(link_holdout)}")

    df_avax_holdout = process_pair_df(avax_holdout, btc_holdout)
    df_link_holdout = process_pair_df(link_holdout, btc_holdout)

    for pair_name, df_h in [("AVAX / BTC", df_avax_holdout), ("LINK / BTC", df_link_holdout)]:
        print(f"\n>>> Holdout: {pair_name} (N={len(df_h)}) <<<")
        for div_col, div_lbl in [("div_12c", "12c Div"), ("div_24c", "24c Div")]:
            for h in [4, 12, 24]:
                x = df_h[div_col].values
                y = df_h[f"spread_fwd_{h}"].values
                rho, p_val = stats.spearmanr(x, y)
                p90 = np.percentile(x, 90)
                p50 = np.percentile(x, 50)
                top10_y = y[x >= p90]
                bot50_y = y[x <= p50]
                mw_stat, mw_p = stats.mannwhitneyu(top10_y, bot50_y, alternative='two-sided')
                print(f"  Holdout {div_lbl} -> Fwd {h:2d}c Spread: rho={rho:+.4f} (p={p_val:.4e}) | Top10 Spread={np.mean(top10_y)*100:+.2f}% vs Bot50={np.mean(bot50_y)*100:+.2f}% (MW p={mw_p:.4e})")

    # =========================================================================
    # PART B: DATA INTEGRITY, ANOMALIES & TEMPORAL CLUSTERING AUDIT (FEB-SEP 2026)
    # =========================================================================
    print("\n" + "=" * 105)
    print("  [PART B] DATA INTEGRITY, ANOMALIES & TEMPORAL CLUSTERING AUDIT")
    print("=" * 105)
    print("Fetching Sprint 5000 candles for BTC, AVAX, LINK...")
    btc_sprint = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", 5000)
    avax_sprint = fetch_historical_candles(connector, "AVAX_USDT", "1h", "perpetual", 5000)
    link_sprint = fetch_historical_candles(connector, "LINK_USDT", "1h", "perpetual", 5000)

    def audit_candle_integrity(candles, symbol):
        df = pd.DataFrame(candles).sort_values("timestamp")
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        # Check gaps
        time_diffs = df["timestamp"].diff().dropna()
        gaps = df[df["timestamp"].diff() > 3600]
        # Check extreme 1-candle return (|ret| > 15%)
        df["ret_1c"] = (df["close"] - df["open"]) / df["open"]
        extreme_candles = df[df["ret_1c"].abs() > 0.15]
        # Max 1c return
        max_ret = df["ret_1c"].abs().max() * 100
        print(f"\n[Integrity: {symbol}]")
        print(f"  Candle count: {len(df)} | Date range: {df['dt'].iloc[0]} -> {df['dt'].iloc[-1]}")
        print(f"  Time gaps (>1h): {len(gaps)} gaps found")
        print(f"  Extreme 1-candle moves (>15%): {len(extreme_candles)} occurrences (Max 1c move: {max_ret:.2f}%)")
        return df

    df_avax_raw = audit_candle_integrity(avax_sprint, "AVAX_USDT")
    df_link_raw = audit_candle_integrity(link_sprint, "LINK_USDT")
    df_btc_raw = audit_candle_integrity(btc_sprint, "BTC_USDT")

    # Temporal Clustering of Top 10% Divergence Points
    df_avax_sprint = process_pair_df(avax_sprint, btc_sprint).reset_index()
    df_link_sprint = process_pair_df(link_sprint, btc_sprint).reset_index()

    def audit_clustering(df_pair, symbol):
        df_pair["dt"] = pd.to_datetime(df_pair["timestamp"], unit="s", utc=True)
        df_pair["month"] = df_pair["dt"].dt.to_period("M")
        df_pair["week"] = df_pair["dt"].dt.to_period("W")
        
        p90 = np.percentile(df_pair["div_24c"], 90)
        top10_df = df_pair[df_pair["div_24c"] >= p90].copy()
        
        print(f"\n[Temporal Distribution of Top 10% Divergence: {symbol}] (Total Events: {len(top10_df)})")
        monthly_counts = top10_df["month"].value_counts().sort_index()
        monthly_total = df_pair["month"].value_counts().sort_index()
        
        for m in monthly_counts.index:
            cnt = monthly_counts[m]
            pct_of_month = (cnt / monthly_total[m]) * 100
            pct_of_events = (cnt / len(top10_df)) * 100
            print(f"  Month {m}: {cnt:3d} events ({pct_of_month:.1f}% of month, {pct_of_events:.1f}% of total Top 10% signals)")
            
        # Check weekly concentration
        weekly_counts = top10_df["week"].value_counts().sort_values(ascending=False)
        top_week = weekly_counts.index[0]
        top_week_cnt = weekly_counts.iloc[0]
        print(f"  Peak Single Week: {top_week} with {top_week_cnt} events ({top_week_cnt/len(top10_df)*100:.1f}% of all events)")

    audit_clustering(df_avax_sprint, "AVAX/BTC")
    audit_clustering(df_link_sprint, "LINK/BTC")

    # =========================================================================
    # PART C: BESPOKE FRICTION CHECK (DEPTH & REAL HISTORICAL FUNDING)
    # =========================================================================
    print("\n" + "=" * 105)
    print("  [PART C] BESPOKE FRICTION CHECK (ORDERBOOK DEPTH, SPREAD & HISTORICAL FUNDING)")
    print("=" * 105)

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

    print("Fetching historical funding rates for AVAX, LINK, BTC...")
    df_btc_fr = fetch_all_funding("BTC_USDT", 1000)
    df_avax_fr = fetch_all_funding("AVAX_USDT", 1000)
    df_link_fr = fetch_all_funding("LINK_USDT", 1000)

    def run_pair_friction(df_pair, df_alt_fr, df_btc_fr, symbol, slippage_4legs):
        df_sim = pd.merge_asof(df_pair, df_alt_fr.rename(columns={"funding_rate": "fr_alt"}), on="timestamp", direction="backward")
        df_sim = pd.merge_asof(df_sim, df_btc_fr.rename(columns={"funding_rate": "fr_btc"}), on="timestamp", direction="backward")
        df_sim["fr_alt"] = df_sim["fr_alt"].ffill().bfill()
        df_sim["fr_btc"] = df_sim["fr_btc"].ffill().bfill()
        
        p90 = np.percentile(df_sim["div_24c"], 90)
        trades = df_sim[df_sim["div_24c"] >= p90].copy()
        
        trades["gross_return"] = - trades["spread_fwd_24"] # Short Alt + Long BTC
        trades["funding_pnl"] = 3.0 * (trades["fr_alt"] - trades["fr_btc"]) # 3 funding periods (24h)
        
        TAKER_FEE_4LEGS = 0.0030 # 0.075% * 4 = 30 bps
        TOTAL_TAKER_FRICTION = TAKER_FEE_4LEGS + slippage_4legs
        trades["net_return_taker"] = trades["gross_return"] + trades["funding_pnl"] - TOTAL_TAKER_FRICTION
        
        MAKER_FEE_4LEGS = 0.0006 # 0.015% * 4 = 6 bps
        trades["net_return_maker"] = trades["gross_return"] + trades["funding_pnl"] - MAKER_FEE_4LEGS
        
        n_trades = len(trades)
        mean_gross = trades["gross_return"].mean() * 100
        mean_fr = trades["funding_pnl"].mean() * 100
        mean_net_taker = trades["net_return_taker"].mean() * 100
        mean_net_maker = trades["net_return_maker"].mean() * 100
        
        gross_pf = trades[trades["gross_return"] > 0]["gross_return"].sum() / abs(trades[trades["gross_return"] < 0]["gross_return"].sum())
        taker_pf = trades[trades["net_return_taker"] > 0]["net_return_taker"].sum() / abs(trades[trades["net_return_taker"] < 0]["net_return_taker"].sum())
        maker_pf = trades[trades["net_return_maker"] > 0]["net_return_maker"].sum() / abs(trades[trades["net_return_maker"] < 0]["net_return_maker"].sum())
        
        taker_wr = (trades["net_return_taker"] > 0).mean() * 100
        maker_wr = (trades["net_return_maker"] > 0).mean() * 100
        
        print(f"\n--- Friction Simulation: {symbol} (N={n_trades} trades, div_24c >= {p90*100:+.2f}%) ---")
        print(f"  Average Gross Spread Move: {mean_gross:+.4f}%")
        print(f"  Average Net Funding Cashflow (24h): {mean_fr:+.4f}%")
        print(f"  Total Taker Friction (Fee 30 bps + Slippage {slippage_4legs*10000:.1f} bps): -{TOTAL_TAKER_FRICTION*100:.2f}%")
        print(f"  >>> TAKER Net Return per Trade: {mean_net_taker:+.4f}% | PF: {taker_pf:.2f} | Win Rate: {taker_wr:.1f}%")
        print(f"  >>> MAKER Net Return per Trade (Fee 6 bps): {mean_net_maker:+.4f}% | PF: {maker_pf:.2f} | Win Rate: {maker_wr:.1f}%")

    # Slippage estimates from orderbook:
    # AVAX: raw spread 1.3 bps + 1.5 bps depth impact = ~3 bps per leg -> 12 bps for 4 legs (0.0012)
    # LINK: raw spread 3.3 bps + 2.0 bps depth impact = ~5 bps per leg -> 20 bps for 4 legs (0.0020)
    run_pair_friction(df_avax_sprint, df_avax_fr, df_btc_fr, "AVAX/BTC", 0.0012)
    run_pair_friction(df_link_sprint, df_link_fr, df_btc_fr, "LINK/BTC", 0.0020)

if __name__ == "__main__":
    run_round6_audit()
