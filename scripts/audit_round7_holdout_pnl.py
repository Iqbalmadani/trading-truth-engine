import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_round7_holdout_pnl():
    connector = get_connector("gateio")
    print("=" * 105)
    print("  TTE AUDIT ROUND 7 — HOLDOUT PROFITABILITY STRESS-TEST & FORWARD VALIDATION SETUP")
    print("=" * 105)

    HOLDOUT_END_TS = 1770688800 - 3600 # Feb 10, 2026 01:00 UTC

    # 1. Fetch Holdout Candles (Aug 2025 -> Feb 2026)
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

    print("\n[1/4] Fetching Holdout Candles (Aug 2025 -> Feb 2026)...")
    btc_holdout = fetch_holdout_candles("BTC_USDT", 4000)
    avax_holdout = fetch_holdout_candles("AVAX_USDT", 4000)
    link_holdout = fetch_holdout_candles("LINK_USDT", 4000)
    print(f"  Holdout Candles: BTC={len(btc_holdout)}, AVAX={len(avax_holdout)}, LINK={len(link_holdout)}")

    # 2. Fetch Holdout Funding Rates
    print("\n[2/4] Fetching Historical Funding Rates across Holdout Period (Aug 2025 -> Feb 2026)...")
    def fetch_holdout_funding(contract, target_count=4000):
        records = {}
        earliest_t = HOLDOUT_END_TS
        while len(records) < target_count:
            params = {"contract": contract, "limit": 1000, "to": earliest_t - 1}
            res = connector._request(f"{connector.BASE_URL}/futures/usdt/funding_rate", params)
            if not res or not isinstance(res, list) or len(res) == 0:
                break
            for r in res:
                records[int(r["t"])] = float(r["r"])
            sorted_ts = sorted(records.keys())
            earliest_t = sorted_ts[0]
            if len(res) < 100:
                break
        df_fr = pd.DataFrame([{"timestamp": t, "funding_rate": r} for t, r in sorted(records.items())]).sort_values("timestamp")
        return df_fr

    df_btc_fr = fetch_holdout_funding("BTC_USDT", 4000)
    df_avax_fr = fetch_holdout_funding("AVAX_USDT", 4000)
    df_link_fr = fetch_holdout_funding("LINK_USDT", 4000)
    print(f"  Holdout Funding Points: BTC={len(df_btc_fr)}, AVAX={len(df_avax_fr)}, LINK={len(df_link_fr)}")

    # 3. Build Holdout Pair Datasets
    def process_pair_df(candles_alt, candles_btc):
        df_alt = pd.DataFrame(candles_alt).set_index("timestamp")
        df_btc = pd.DataFrame(candles_btc).set_index("timestamp")
        df = pd.DataFrame({
            "alt_close": df_alt["close"],
            "btc_close": df_btc["close"],
        }).dropna()
        
        df["alt_ret_24"] = np.log(df["alt_close"] / df["alt_close"].shift(24))
        df["btc_ret_24"] = np.log(df["btc_close"] / df["btc_close"].shift(24))
        df["div_24c"] = df["alt_ret_24"] - df["btc_ret_24"]
        
        for h in [4, 12, 24]:
            df[f"alt_fwd_{h}"] = (df["alt_close"].shift(-h) - df["alt_close"]) / df["alt_close"]
            df[f"btc_fwd_{h}"] = (df["btc_close"].shift(-h) - df["btc_close"]) / df["btc_close"]
            df[f"spread_fwd_{h}"] = df[f"alt_fwd_{h}"] - df[f"btc_fwd_{h}"]
            
        return df.dropna().reset_index()

    df_avax_h = process_pair_df(avax_holdout, btc_holdout)
    df_link_h = process_pair_df(link_holdout, btc_holdout)

    # 4. Also fetch Sprint candles to lock exact Sprint p90 thresholds
    print("\n[3/4] Fetching Sprint Candles (Feb -> Sep 2026) to calibrate locked Sprint thresholds...")
    btc_sprint = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", 5000)
    avax_sprint = fetch_historical_candles(connector, "AVAX_USDT", "1h", "perpetual", 5000)
    link_sprint = fetch_historical_candles(connector, "LINK_USDT", "1h", "perpetual", 5000)
    df_avax_s = process_pair_df(avax_sprint, btc_sprint)
    df_link_s = process_pair_df(link_sprint, btc_sprint)

    threshold_avax_sprint = np.percentile(df_avax_s["div_24c"], 90) # +2.21%
    threshold_link_sprint = np.percentile(df_link_s["div_24c"], 90) # +2.01%
    print(f"  Locked Sprint p90 Thresholds: AVAX={threshold_avax_sprint*100:+.2f}% | LINK={threshold_link_sprint*100:+.2f}%")

    # 5. Run Exact Holdout Friction Simulation
    print("\n" + "=" * 105)
    print("  [PART A] EXACT HOLDOUT PROFITABILITY SIMULATION (AUG 2025 -> FEB 2026)")
    print("=" * 105)

    def evaluate_holdout_pnl(df_pair, df_alt_fr, df_btc_fr, symbol, locked_threshold, slippage_4legs):
        df_sim = pd.merge_asof(df_pair, df_alt_fr.rename(columns={"funding_rate": "fr_alt"}), on="timestamp", direction="backward")
        df_sim = pd.merge_asof(df_sim, df_btc_fr.rename(columns={"funding_rate": "fr_btc"}), on="timestamp", direction="backward")
        df_sim["fr_alt"] = df_sim["fr_alt"].ffill().bfill()
        df_sim["fr_btc"] = df_sim["fr_btc"].ffill().bfill()

        native_p90 = np.percentile(df_sim["div_24c"], 90)

        for mode, thresh in [("LOCKED SPRINT THRESHOLD", locked_threshold), ("NATIVE HOLDOUT P90", native_p90)]:
            trades = df_sim[df_sim["div_24c"] >= thresh].copy()
            if len(trades) == 0:
                print(f"  [{symbol} | {mode}] No trades triggered!")
                continue

            trades["gross_return"] = - trades["spread_fwd_24"] # Short Alt + Long BTC
            # If funding rate is recorded hourly or 8-hourly, 24h = 24 hourly points or 3 8h points.
            # Let's inspect step between funding points
            sample_step = df_alt_fr["timestamp"].diff().median()
            mult = 24.0 if sample_step <= 3600 else 3.0
            trades["funding_pnl"] = mult * (trades["fr_alt"] - trades["fr_btc"])

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

            taker_wins = trades[trades["net_return_taker"] > 0]["net_return_taker"].sum()
            taker_losses = abs(trades[trades["net_return_taker"] < 0]["net_return_taker"].sum())
            taker_pf = taker_wins / taker_losses if taker_losses > 0 else 0

            maker_wins = trades[trades["net_return_maker"] > 0]["net_return_maker"].sum()
            maker_losses = abs(trades[trades["net_return_maker"] < 0]["net_return_maker"].sum())
            maker_pf = maker_wins / maker_losses if maker_losses > 0 else 0

            taker_wr = (trades["net_return_taker"] > 0).mean() * 100
            maker_wr = (trades["net_return_maker"] > 0).mean() * 100
            
            # Expectancy in R (assuming 1R risk = 1.5% max SL)
            SL_DIST = 0.015
            taker_exp_r = (trades["net_return_taker"] / SL_DIST).mean()

            print(f"\n>>> {symbol} — {mode} (Threshold: div_24c >= {thresh*100:+.2f}%) <<<")
            print(f"  Trades Triggered (N): {n_trades} trades across 167 days")
            print(f"  Average Gross Spread Move: {mean_gross:+.4f}%")
            print(f"  Average Net Funding Cashflow (24h): {mean_fr:+.4f}%")
            print(f"  Total Taker Friction (Fee 30 bps + Slippage {slippage_4legs*10000:.1f} bps): -{TOTAL_TAKER_FRICTION*100:.2f}%")
            print(f"  [TAKER EXECUTION]")
            print(f"    Average Net Return: {mean_net_taker:+.4f}% per trade")
            print(f"    Net Profit Factor (PF): {taker_pf:.2f}")
            print(f"    Win Rate: {taker_wr:.1f}%")
            print(f"    Expectancy (R per trade, SL=1.5%): {taker_exp_r:+.3f}R")
            print(f"  [MAKER EXECUTION]")
            print(f"    Average Net Return: {mean_net_maker:+.4f}% per trade")
            print(f"    Net Profit Factor (PF): {maker_pf:.2f}")
            print(f"    Win Rate: {maker_wr:.1f}%")

    evaluate_holdout_pnl(df_avax_h, df_avax_fr, df_btc_fr, "AVAX/BTC", threshold_avax_sprint, 0.0012)
    evaluate_holdout_pnl(df_link_h, df_link_fr, df_btc_fr, "LINK/BTC", threshold_link_sprint, 0.0020)

if __name__ == "__main__":
    run_round7_holdout_pnl()
