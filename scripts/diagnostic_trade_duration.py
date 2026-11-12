"""
Phase 1.1 Diagnostic: Trade Duration Distribution

Reads all existing backtest SQLite databases in data/ and calculates the
distribution of trade durations (in candles) for resolved trades.

Determines the empirical horizon for Hurst Exponent and regime validation.
"""

import sqlite3
import glob
from pathlib import Path
from typing import List, Dict, Any
import numpy as np

def analyze_trade_durations(data_dir: Path = None) -> Dict[str, Any]:
    if data_dir is None:
        data_dir = Path(__file__).resolve().parent.parent / "data"

    db_files = list(data_dir.glob("backtest_*.db"))
    if not db_files:
        print(f"No backtest databases found in {data_dir}")
        return {}

    all_durations = []
    db_results = {}

    for db_path in db_files:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT candle_index, outcome_candle, outcome, signal_type, pnl_percent
                FROM bt_signals
                WHERE outcome IN ('TP_HIT', 'SL_HIT', 'EXPIRED', 'LIQUIDATED')
                  AND outcome_candle IS NOT NULL
            """)
            rows = cursor.fetchall()
            conn.close()

            durations = []
            for r in rows:
                dur = r["outcome_candle"] - r["candle_index"]
                if dur >= 0:
                    durations.append(dur)

            all_durations.extend(durations)
            db_results[db_path.name] = {
                "count": len(durations),
                "durations": durations,
            }
        except Exception as e:
            print(f"Error reading {db_path.name}: {e}")

    if not all_durations:
        print("No resolved trades found across databases.")
        return {}

    durations_arr = np.array(all_durations)
    stats = {
        "total_trades": len(durations_arr),
        "mean": float(np.mean(durations_arr)),
        "std": float(np.std(durations_arr)),
        "min": int(np.min(durations_arr)),
        "p10": float(np.percentile(durations_arr, 10)),
        "p25": float(np.percentile(durations_arr, 25)),
        "median": float(np.median(durations_arr)),
        "p75": float(np.percentile(durations_arr, 75)),
        "p90": float(np.percentile(durations_arr, 90)),
        "max": int(np.max(durations_arr)),
        "by_db": {}
    }

    for name, res in db_results.items():
        if res["count"] > 0:
            arr = np.array(res["durations"])
            stats["by_db"][name] = {
                "count": len(arr),
                "mean": float(np.mean(arr)),
                "median": float(np.median(arr)),
                "p25": float(np.percentile(arr, 25)),
                "p75": float(np.percentile(arr, 75)),
                "p90": float(np.percentile(arr, 90)),
            }

    return stats

def main():
    print("=" * 60)
    print("PHASE 1.1: TRADE DURATION DISTRIBUTION DIAGNOSTIC")
    print("=" * 60)

    stats = analyze_trade_durations()
    if not stats:
        return

    print(f"\nTotal Resolved Trades Analyzed: {stats['total_trades']}")
    print("-" * 60)
    print(f"  Mean Duration:    {stats['mean']:.2f} candles")
    print(f"  Std Deviation:    {stats['std']:.2f} candles")
    print(f"  Min Duration:     {stats['min']} candles")
    print(f"  P10:              {stats['p10']:.2f} candles")
    print(f"  P25:              {stats['p25']:.2f} candles")
    print(f"  Median (P50):     {stats['median']:.2f} candles")
    print(f"  P75:              {stats['p75']:.2f} candles")
    print(f"  P90:              {stats['p90']:.2f} candles")
    print(f"  Max Duration:     {stats['max']} candles")
    print("-" * 60)

    print("\nPer-Database Breakdown:")
    for db_name, db_stat in stats["by_db"].items():
        print(f"  {db_name:35s} | N={db_stat['count']:3d} | Median={db_stat['median']:.1f} | Mean={db_stat['mean']:.1f} | P75={db_stat['p75']:.1f} | P90={db_stat['p90']:.1f}")

    print("=" * 60)
    print(f"IMPLICATION FOR REGIME CLASSIFIER:")
    print(f"  Relevant holding horizon for regime stability is ~{stats['median']:.0f}-{stats['p75']:.0f} candles.")
    print("=" * 60)

if __name__ == "__main__":
    main()
