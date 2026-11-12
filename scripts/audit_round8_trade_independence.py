import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy import stats
import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.layer1.connectors.registry import get_connector
from scripts.diagnose_production_layer2 import fetch_historical_candles

def run_round8_independence():
    connector = get_connector("gateio")
    print("=" * 105)
    print("  TTE AUDIT ROUND 8 — TRADE INDEPENDENCE & EPISODE DEDUPLICATION AUDIT")
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

    # 2. Fetch Funding Rates
    print("\n[2/4] Fetching Holdout Funding Rates...")
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

    # 3. Process Pair DataFrame
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
        
        # 24c forward returns
        df["alt_fwd_24"] = (df["alt_close"].shift(-24) - df["alt_close"]) / df["alt_close"]
        df["btc_fwd_24"] = (df["btc_close"].shift(-24) - df["btc_close"]) / df["btc_close"]
        df["spread_fwd_24"] = df["alt_fwd_24"] - df["btc_fwd_24"]
        
        return df.dropna().reset_index()

    df_avax_h = process_pair_df(avax_holdout, btc_holdout)
    df_link_h = process_pair_df(link_holdout, btc_holdout)

    # Merge funding rates
    def attach_funding(df_pair, df_alt_fr, df_btc_fr):
        df = pd.merge_asof(df_pair, df_alt_fr.rename(columns={"funding_rate": "fr_alt"}), on="timestamp", direction="backward")
        df = pd.merge_asof(df, df_btc_fr.rename(columns={"funding_rate": "fr_btc"}), on="timestamp", direction="backward")
        df["fr_alt"] = df["fr_alt"].ffill().bfill()
        df["fr_btc"] = df["fr_btc"].ffill().bfill()
        sample_step = df_alt_fr["timestamp"].diff().median()
        mult = 24.0 if sample_step <= 3600 else 3.0
        df["funding_pnl"] = mult * (df["fr_alt"] - df["fr_btc"])
        return df

    df_avax_h = attach_funding(df_avax_h, df_avax_fr, df_btc_fr)
    df_link_h = attach_funding(df_link_h, df_link_fr, df_btc_fr)

    # Locked Sprint thresholds
    THRESH_AVAX = 0.0221
    THRESH_LINK = 0.0201

    print("\n" + "=" * 105)
    print("  [PART A.1] OVERLAP & CONCURRENCY ANALYSIS")
    print("=" * 105)

    def analyze_overlap(df_pair, thresh, symbol):
        raw_signals = df_pair[df_pair["div_24c"] >= thresh].copy()
        raw_signals["dt"] = pd.to_datetime(raw_signals["timestamp"], unit="s", utc=True)
        raw_signals["time_since_prev_h"] = raw_signals["timestamp"].diff() / 3600.0
        
        # Overlapping count: signals that occurred < 24 hours after previous signal
        overlap_cnt = (raw_signals["time_since_prev_h"] < 24).sum()
        overlap_pct = (overlap_cnt / len(raw_signals)) * 100
        consecutive_1h = (raw_signals["time_since_prev_h"] == 1).sum()
        
        print(f"\n>>> Overlap Audit: {symbol} (Threshold: div_24c >= {thresh*100:+.2f}%) <<<")
        print(f"  Total Raw Triggered Signals: {len(raw_signals)}")
        print(f"  Signals occurring within 24h of previous signal: {overlap_cnt} ({overlap_pct:.1f}%)")
        print(f"  Signals occurring on the IMMEDIATELY NEXT candle (1h gap): {consecutive_1h} ({consecutive_1h/len(raw_signals)*100:.1f}%)")
        print(f"  -> Concurrency Confirmation: {overlap_pct:.1f}% of signals are part of continuous rolling clusters!")
        return raw_signals

    raw_avax = analyze_overlap(df_avax_h, THRESH_AVAX, "AVAX/BTC")
    raw_link = analyze_overlap(df_link_h, THRESH_LINK, "LINK/BTC")

    print("\n" + "=" * 105)
    print("  [PART A.2 & A.3] DEDUPLICATED EPISODE PROFITABILITY SIMULATION")
    print("=" * 105)

    def run_deduplicated_simulation(df_pair, thresh, symbol, slippage_4legs, cooldown_h=0):
        # We simulate sequential state:
        # If position is closed at t_exit, can enter new position at t >= t_exit + cooldown_h
        HOLD_HOURS = 24
        current_exit_ts = 0
        episodes = []
        
        for idx, row in df_pair.iterrows():
            ts = int(row["timestamp"])
            div = row["div_24c"]
            
            # Check if condition met AND no position currently open
            if div >= thresh and ts >= current_exit_ts:
                # Enter episode!
                entry_ts = ts
                exit_ts = ts + (HOLD_HOURS * 3600)
                current_exit_ts = exit_ts + (cooldown_h * 3600)
                
                gross_ret = - row["spread_fwd_24"]
                funding_pnl = row["funding_pnl"]
                
                episodes.append({
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "entry_dt": datetime.datetime.fromtimestamp(entry_ts, tz=datetime.timezone.utc),
                    "exit_dt": datetime.datetime.fromtimestamp(exit_ts, tz=datetime.timezone.utc),
                    "div_24c": div,
                    "gross_return": gross_ret,
                    "funding_pnl": funding_pnl,
                })
                
        df_ep = pd.DataFrame(episodes)
        if len(df_ep) == 0:
            print(f"[{symbol}] No episodes triggered!")
            return df_ep
            
        TAKER_FEE_4LEGS = 0.0030 # 30 bps
        TOTAL_TAKER_FRICTION = TAKER_FEE_4LEGS + slippage_4legs
        df_ep["net_taker"] = df_ep["gross_return"] + df_ep["funding_pnl"] - TOTAL_TAKER_FRICTION
        
        MAKER_FEE_4LEGS = 0.0006 # 6 bps
        df_ep["net_maker"] = df_ep["gross_return"] + df_ep["funding_pnl"] - MAKER_FEE_4LEGS
        
        n_ep = len(df_ep)
        mean_gross = df_ep["gross_return"].mean() * 100
        mean_fr = df_ep["funding_pnl"].mean() * 100
        mean_taker = df_ep["net_taker"].mean() * 100
        mean_maker = df_ep["net_maker"].mean() * 100
        
        taker_wins = df_ep[df_ep["net_taker"] > 0]["net_taker"].sum()
        taker_losses = abs(df_ep[df_ep["net_taker"] < 0]["net_taker"].sum())
        taker_pf = taker_wins / taker_losses if taker_losses > 0 else 0
        
        maker_wins = df_ep[df_ep["net_maker"] > 0]["net_maker"].sum()
        maker_losses = abs(df_ep[df_ep["net_maker"] < 0]["net_maker"].sum())
        maker_pf = maker_wins / maker_losses if maker_losses > 0 else 0
        
        taker_wr = (df_ep["net_taker"] > 0).mean() * 100
        maker_wr = (df_ep["net_maker"] > 0).mean() * 100
        
        SL_DIST = 0.015
        taker_exp_r = (df_ep["net_taker"] / SL_DIST).mean()
        maker_exp_r = (df_ep["net_maker"] / SL_DIST).mean()
        
        print(f"\n>>> DEDUPLICATED EPISODES: {symbol} (Cooldown={cooldown_h}h) <<<")
        print(f"  Raw Signals: {len(df_pair[df_pair['div_24c'] >= thresh])} -> Independent Episodes: {n_ep} episodes")
        print(f"  Average Holding Period: {HOLD_HOURS}h per episode")
        print(f"  Average Gross Spread Move: {mean_gross:+.4f}%")
        print(f"  Average Funding Cashflow: {mean_fr:+.4f}%")
        print(f"  [TAKER EXECUTION (Fee 30 bps + Slippage {slippage_4legs*10000:.1f} bps = -{TOTAL_TAKER_FRICTION*100:.2f}%)]")
        print(f"    Net Return per Episode: {mean_taker:+.4f}%")
        print(f"    Net Profit Factor (PF): {taker_pf:.2f}")
        print(f"    Win Rate: {taker_wr:.1f}% ({int(taker_wr/100*n_ep)} wins / {n_ep - int(taker_wr/100*n_ep)} losses)")
        print(f"    Expectancy (R per episode, SL=1.5%): {taker_exp_r:+.3f}R")
        print(f"  [MAKER EXECUTION (Fee 6 bps)]")
        print(f"    Net Return per Episode: {mean_maker:+.4f}%")
        print(f"    Net Profit Factor (PF): {maker_pf:.2f}")
        print(f"    Win Rate: {maker_wr:.1f}%")
        print(f"    Expectancy (R per episode, SL=1.5%): {maker_exp_r:+.3f}R")
        
        return df_ep

    # Test 0h cooldown (re-enter next available candle if signal present)
    ep_avax_0 = run_deduplicated_simulation(df_avax_h, THRESH_AVAX, "AVAX/BTC Holdout (Aug 2025-Feb 2026)", 0.0012, cooldown_h=0)
    ep_link_0 = run_deduplicated_simulation(df_link_h, THRESH_LINK, "LINK/BTC Holdout (Aug 2025-Feb 2026)", 0.0020, cooldown_h=0)

    # Test 12h cooldown
    print("\n--- Testing 12h Cooldown Post-Exit ---")
    ep_avax_12 = run_deduplicated_simulation(df_avax_h, THRESH_AVAX, "AVAX/BTC Holdout (Aug 2025-Feb 2026)", 0.0012, cooldown_h=12)
    ep_link_12 = run_deduplicated_simulation(df_link_h, THRESH_LINK, "LINK/BTC Holdout (Aug 2025-Feb 2026)", 0.0020, cooldown_h=12)

    # Also run deduplicated simulation on Sprint dataset (Feb -> Sep 2026) for complete comparison
    print("\n" + "=" * 105)
    print("  [PART A.4] DEDUPLICATED SIMULATION ON SPRINT 2026 DATASET (FEB -> SEP 2026)")
    print("=" * 105)
    btc_sprint = fetch_historical_candles(connector, "BTC_USDT", "1h", "perpetual", 5000)
    avax_sprint = fetch_historical_candles(connector, "AVAX_USDT", "1h", "perpetual", 5000)
    link_sprint = fetch_historical_candles(connector, "LINK_USDT", "1h", "perpetual", 5000)
    
    # Historical funding sprint
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

    df_btc_s_fr = fetch_all_funding("BTC_USDT", 1000)
    df_avax_s_fr = fetch_all_funding("AVAX_USDT", 1000)
    df_link_s_fr = fetch_all_funding("LINK_USDT", 1000)

    df_avax_s = process_pair_df(avax_sprint, btc_sprint)
    df_link_s = process_pair_df(link_sprint, btc_sprint)
    df_avax_s = attach_funding(df_avax_s, df_avax_s_fr, df_btc_s_fr)
    df_link_s = attach_funding(df_link_s, df_link_s_fr, df_btc_s_fr)

    ep_avax_sprint = run_deduplicated_simulation(df_avax_s, THRESH_AVAX, "AVAX/BTC Sprint 2026 (Feb-Sep 2026)", 0.0012, cooldown_h=0)
    ep_link_sprint = run_deduplicated_simulation(df_link_s, THRESH_LINK, "LINK/BTC Sprint 2026 (Feb-Sep 2026)", 0.0020, cooldown_h=0)

if __name__ == "__main__":
    run_round8_independence()
