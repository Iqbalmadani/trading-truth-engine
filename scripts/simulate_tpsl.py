"""
TTE — TP/SL Method Comparison Simulator

Does NOT touch src/thresholds.py, src/executor.py, or src/risk.py.
Production code stays untouched.

What this does:
1. Runs the same L1->L2->L3 pipeline as run_diagnostic.py to generate
   signals (direction + entry price), using whatever mapping is CURRENTLY
   in production (no flip — this script is only about exits, not direction).
2. For EACH signal, simulates FOUR different exit methods candle-by-candle
   (path-dependent — checks which level gets hit FIRST, not just the
   return at a fixed horizon):

   A. CURRENT (production)  — uses layer3()'s own stop_loss/take_profit
                               (regime-range based, the method proven
                               broken by Test 5 in the earlier diagnostic)
   B. ATR_FIXED              — SL/TP at a fixed ATR multiple from entry
   C. ATR_TRAILING           — ATR-based stop that trails price, no fixed TP
   D. TIME_EXIT              — no SL/TP at all, just exit at a fixed
                               candle count (uses close price)

3. Prints a head-to-head comparison: win rate, avg R-multiple, expectancy,
   timeout rate, for each method.

IMPORTANT — read before trusting the numbers:
- This does NOT change which signals are generated or their direction.
  It only changes how each signal's trade would have been exited.
- Same overlapping-sample caveat as before applies: signals close together
  in time are not fully independent draws.
- ATR/trailing parameters (--sl-atr-mult, --tp-atr-mult) are guesses to
  START from, not tuned values. Try a few combinations, don't just trust
  the first run.
- Whichever method wins here should still go through the same discipline
  as everything else in this project: don't approve multiple production
  file changes off ONE run. Re-run on a second, non-overlapping time window
  before touching src/risk.py.

Usage:
    python scripts/simulate_tpsl.py --exchange gateio --pair ETH_USDT --tf 1h --candles 800
    python scripts/simulate_tpsl.py --pair ETH_USDT --candles 800 --sl-atr-mult 1.5 --tp-atr-mult 3.0 --max-hold 50
"""

import sys
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.layer1.connectors.registry import get_connector
from src.layer2.cpd import get_cpd_window
from src.layer2.zscore import zscore_in_window
from src.layer3.executor import layer3
from src.layer3.thresholds import InsufficientScoreHistoryError

from scripts.run_backtest import (
    fetch_historical_ohlcv,
    fetch_historical_scope,
    compute_bt_penalties,
)
from src.layer3.meta_learning import (
    meta_learn_score_penalty,
    meta_learn_price_penalty,
)

logger = logging.getLogger(__name__)


# =====================================================================
# Fixed-window helper — makes repeated runs use IDENTICAL data
# =====================================================================
_TS_CANDIDATE_KEYS = ["timestamp", "time", "open_time", "datetime", "ts", "date"]


def _detect_timestamp_key(candle: Dict) -> Optional[str]:
    for k in _TS_CANDIDATE_KEYS:
        if k in candle:
            return k
    return None


def _to_epoch_seconds(value) -> Optional[float]:
    """Best-effort conversion of a candle timestamp field to epoch seconds."""
    import datetime as _dt
    if value is None:
        return None
    if isinstance(value, (int, float)):
        # Heuristic: ms epoch is ~13 digits, s epoch is ~10 digits
        return value / 1000.0 if value > 10_000_000_000 else float(value)
    if isinstance(value, _dt.datetime):
        return value.replace(tzinfo=value.tzinfo or _dt.timezone.utc).timestamp()
    if isinstance(value, str):
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def compute_fixed_window_indices(candles: List[Dict], n_candles: int, end_date: Optional[str]):
    """
    Returns (start_idx, end_idx) into `candles` for the window to use.
    If end_date is given, anchors on that fixed point so repeated runs see
    identical data. If None, falls back to "last n_candles as of now" —
    the window WILL shift between runs; don't use for sensitivity testing.

    IMPORTANT: the SAME (start_idx, end_idx) must be applied to candles
    AND to every scope history, or L2 scoring will silently misalign
    (candle at position i no longer matches scope data at position i).
    """
    if end_date is None:
        end_idx = len(candles)
        start_idx = max(0, end_idx - n_candles)
        return start_idx, end_idx

    ts_key = _detect_timestamp_key(candles[0])
    if ts_key is None:
        print("  ERROR: Could not find a timestamp field on candle objects.")
        print(f"  Candle keys available: {list(candles[0].keys())}")
        print("  --end-date needs a timestamp field to anchor on. Please report")
        print("  the keys above so this script can be adjusted to the right field name.")
        sys.exit(1)

    import datetime as _dt
    try:
        end_dt = _dt.datetime.fromisoformat(end_date)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=_dt.timezone.utc)
        end_epoch = end_dt.timestamp()
    except ValueError:
        print(f"  ERROR: Could not parse --end-date '{end_date}'. Use format YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS")
        sys.exit(1)

    end_idx = len(candles)
    for idx, c in enumerate(candles):
        ts = _to_epoch_seconds(c.get(ts_key))
        if ts is not None and ts > end_epoch:
            end_idx = idx
            break

    start_idx = max(0, end_idx - n_candles)
    if end_idx - start_idx < n_candles:
        print(f"  ERROR: Only {end_idx - start_idx} candles available before --end-date {end_date},")
        print(f"  but {n_candles} were requested. Increase --fetch-buffer and try again.")
        sys.exit(1)

    first_ts = candles[start_idx].get(ts_key)
    last_ts = candles[end_idx - 1].get(ts_key)
    print(f"  Fixed window: {end_idx - start_idx} candles, from {first_ts} to {last_ts} (anchored at --end-date {end_date})")
    return start_idx, end_idx


def apply_window(data: List, start_idx: int, end_idx: int, label: str) -> List:
    """Applies the SAME window indices to any per-candle-aligned series
    (candles, or a scope history). Aborts loudly if lengths don't match
    what's expected — a silent misalignment here would corrupt L2 scoring
    without any visible error, which is worse than crashing."""
    if end_idx > len(data):
        print(f"  ERROR: '{label}' has only {len(data)} points but window needs index {end_idx}.")
        print(f"  This means '{label}' is NOT fetched at the same cadence/count as the OHLCV")
        print(f"  candles, and this script cannot safely align them. Please report this so")
        print(f"  the fetch logic can be adjusted (this must not be silently ignored).")
        sys.exit(1)
    return data[start_idx:end_idx]


# =====================================================================
# ATR helper
# =====================================================================
def compute_atr(candles: List[Dict], end_idx: int, period: int = 14) -> Optional[float]:
    """ATR as of candles[end_idx], using the `period` candles before it.
    Does NOT look at candles[end_idx] itself or beyond (no lookahead)."""
    start = end_idx - period
    if start < 1:
        return None
    trs = []
    for k in range(start, end_idx):
        h = candles[k]["high"]
        l = candles[k]["low"]
        pc = candles[k - 1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if not trs:
        return None
    return float(np.mean(trs))


# =====================================================================
# Exit simulators — each returns a dict: outcome, exit_candle_offset, r_multiple
# =====================================================================
def simulate_current(candles, i, direction, entry_price, sl, tp, max_hold):
    """Method A: production stop_loss/take_profit, path-dependent check."""
    if sl is None or tp is None:
        return None
    risk = abs(entry_price - sl)
    if risk == 0:
        return None
    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]
        if direction == "LONG":
            hit_sl = c["low"] <= sl
            hit_tp = c["high"] >= tp
        else:
            hit_sl = c["high"] >= sl
            hit_tp = c["low"] <= tp
        if hit_sl and hit_tp:
            # Both touched same candle — conservative assumption: SL first
            return {"outcome": "LOSS", "exit_offset": offset, "r": -1.0}
        if hit_sl:
            return {"outcome": "LOSS", "exit_offset": offset, "r": -1.0}
        if hit_tp:
            reward = abs(tp - entry_price)
            return {"outcome": "WIN", "exit_offset": offset, "r": reward / risk}
    # Timeout — mark-to-market at max_hold using close
    j = min(i + max_hold, len(candles) - 1)
    close = candles[j]["close"]
    raw = (close - entry_price) if direction == "LONG" else (entry_price - close)
    return {"outcome": "TIMEOUT", "exit_offset": max_hold, "r": raw / risk}


def simulate_atr_fixed(candles, i, direction, entry_price, atr, sl_mult, tp_mult, max_hold):
    """Method B: fixed SL/TP at ATR multiples from entry."""
    if atr is None or atr <= 0:
        return None
    if direction == "LONG":
        sl = entry_price - sl_mult * atr
        tp = entry_price + tp_mult * atr
    else:
        sl = entry_price + sl_mult * atr
        tp = entry_price - tp_mult * atr
    risk = abs(entry_price - sl)
    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]
        if direction == "LONG":
            hit_sl = c["low"] <= sl
            hit_tp = c["high"] >= tp
        else:
            hit_sl = c["high"] >= sl
            hit_tp = c["low"] <= tp
        if hit_sl and hit_tp:
            return {"outcome": "LOSS", "exit_offset": offset, "r": -1.0}
        if hit_sl:
            return {"outcome": "LOSS", "exit_offset": offset, "r": -1.0}
        if hit_tp:
            reward = abs(tp - entry_price)
            return {"outcome": "WIN", "exit_offset": offset, "r": reward / risk}
    j = min(i + max_hold, len(candles) - 1)
    close = candles[j]["close"]
    raw = (close - entry_price) if direction == "LONG" else (entry_price - close)
    return {"outcome": "TIMEOUT", "exit_offset": max_hold, "r": raw / risk}


def simulate_atr_trailing(candles, i, direction, entry_price, atr, trail_mult, max_hold):
    """Method C: ATR-based trailing stop, no fixed TP."""
    if atr is None or atr <= 0:
        return None
    risk = trail_mult * atr
    if direction == "LONG":
        stop = entry_price - risk
        extreme = entry_price
    else:
        stop = entry_price + risk
        extreme = entry_price

    for offset in range(1, max_hold + 1):
        j = i + offset
        if j >= len(candles):
            break
        c = candles[j]
        if direction == "LONG":
            if c["low"] <= stop:
                r = (stop - entry_price) / risk
                outcome = "WIN" if stop > entry_price else "LOSS"
                return {"outcome": outcome, "exit_offset": offset, "r": r}
            extreme = max(extreme, c["high"])
            stop = max(stop, extreme - risk)
        else:
            if c["high"] >= stop:
                r = (entry_price - stop) / risk
                outcome = "WIN" if stop < entry_price else "LOSS"
                return {"outcome": outcome, "exit_offset": offset, "r": r}
            extreme = min(extreme, c["low"])
            stop = min(stop, extreme + risk)

    j = min(i + max_hold, len(candles) - 1)
    close = candles[j]["close"]
    raw = (close - entry_price) if direction == "LONG" else (entry_price - close)
    return {"outcome": "TIMEOUT", "exit_offset": max_hold, "r": raw / risk}


def simulate_time_exit(candles, i, direction, entry_price, atr, hold_candles):
    """Method D: no SL/TP — exit purely on a fixed time horizon.
    R-multiple here is normalized by ATR just so it's comparable to the
    other methods' R units; it does NOT mean a stop was actually placed."""
    j = i + hold_candles
    if j >= len(candles) or atr is None or atr <= 0:
        return None
    close = candles[j]["close"]
    raw = (close - entry_price) if direction == "LONG" else (entry_price - close)
    outcome = "WIN" if raw > 0 else "LOSS"
    return {"outcome": outcome, "exit_offset": hold_candles, "r": raw / atr}


# =====================================================================
# Main
# =====================================================================
def run_simulation(
    exchange, pair, timeframe, market_type, n_candles,
    min_warmup=50, recal_interval=100,
    sl_atr_mult=1.5, tp_atr_mult=3.0, trail_atr_mult=1.5,
    time_exit_candles=20, max_hold=50, atr_period=14,
    end_date=None, fetch_buffer=3000,
):
    print(f"\n{'='*70}")
    print(f"  TTE TP/SL METHOD COMPARISON — {exchange}/{pair}/{timeframe}")
    print(f"  {n_candles} candles | max_hold={max_hold} | ATR period={atr_period}")
    if end_date:
        print(f"  FIXED WINDOW anchored at: {end_date}")
    else:
        print(f"  WARNING: no --end-date given — window shifts between runs.")
        print(f"  Do NOT compare parameter sensitivity across runs without --end-date.")
    print(f"{'='*70}\n")

    connector = get_connector(exchange)
    print("  Fetching candles...")
    fetch_count = fetch_buffer if end_date else n_candles
    # Gate.io candlestick API hard limit = 2000. Cap to avoid 400 Bad Request.
    EXCHANGE_MAX_CANDLES = 2000
    if fetch_count > EXCHANGE_MAX_CANDLES:
        print(f"  NOTE: fetch_count={fetch_count} exceeds API limit ({EXCHANGE_MAX_CANDLES}), capping.")
        fetch_count = EXCHANGE_MAX_CANDLES
    raw_candles = fetch_historical_ohlcv(connector, pair, timeframe, market_type, fetch_count)
    if len(raw_candles) < min_warmup + 10:
        print(f"  ERROR: Not enough candles ({len(raw_candles)})")
        return

    start_idx, end_idx = compute_fixed_window_indices(raw_candles, n_candles, end_date)
    candles = apply_window(raw_candles, start_idx, end_idx, "OHLCV candles")
    if len(candles) < min_warmup + 10:
        print(f"  ERROR: Not enough candles after windowing ({len(candles)})")
        return
    print(f"  Using {len(candles)} candles")

    # Scopes that are 1:1 aligned with OHLCV candles (same cadence)
    # can be positionally windowed with the same start_idx/end_idx.
    # Scopes with DIFFERENT cadence (e.g. funding_rate = 8h update on
    # a 1h timeframe) return far fewer points than candles — they can't
    # be positionally aligned and must be used as-is. The L2 scoring
    # loop already handles this via min(i, len(history)).
    CANDLE_ALIGNED_SCOPES = {"ohlcv", "volatility_compression"}

    scope_histories = {}
    active_scopes = []
    for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
        history = fetch_historical_scope(connector, pair, timeframe, market_type, scope, fetch_count)
        if not history or len(history) < 2:
            print(f"    {scope}: insufficient data ({len(history) if history else 0} points) — skipped")
            continue

        if scope in CANDLE_ALIGNED_SCOPES:
            # Same cadence as candles — positional window is safe
            if end_idx <= len(history):
                history = apply_window(history, start_idx, end_idx, f"scope:{scope}")
            else:
                # Windowed OHLCV-derived scope may have fewer points due to
                # internal warmup (e.g. vol_compression loses 20 for rolling window).
                # Use all available data and let the scoring loop handle it.
                print(f"    {scope}: {len(history)} points (fewer than window, using all)")
        else:
            # Different cadence (e.g. funding_rate: 8h on 1h timeframe).
            # Cannot positionally align — use ALL data as-is.
            # The scoring loop's min(i, len(history)) freezes at the last
            # available point once the candle index exceeds the series length,
            # exactly matching run_diagnostic.py behavior.
            print(f"    {scope}: {len(history)} points (different cadence, using all — no positional window)")

        scope_histories[scope] = history
        active_scopes.append(scope)
        print(f"    {scope}: {len(scope_histories[scope])} points active")
    if not active_scopes:
        print("  ERROR: No active scopes")
        return

    close_prices = [c["close"] for c in candles]

    score_history: List[float] = []
    penalty_score = 1.0
    penalty_price = 1.0
    model_score = "rbf"
    model_price = "rbf"

    signals = []

    print(f"\n  Walking forward from candle {min_warmup} to {len(candles)-1}...\n")

    for i in range(min_warmup, len(candles)):
        candle = candles[i]
        price_slice = close_prices[:i]
        if len(price_slice) < 3:
            continue

        if i % recal_interval == 0 and len(score_history) >= 30:
            penalty_score, penalty_price, model_score, model_price = compute_bt_penalties(
                score_history, price_slice
            )

        try:
            z_scores = {}
            for scope in active_scopes:
                data = scope_histories[scope][:min(i, len(scope_histories[scope]))]
                if len(data) > 500:
                    data = data[-500:]
                if len(data) < 2:
                    z_scores[scope] = 0.0
                    continue
                pen = penalty_score if scope == "ohlcv" else penalty_price
                try:
                    window, _ = get_cpd_window(data, pen, model=model_score)
                    if len(window) < 2:
                        z_scores[scope] = 0.0
                    else:
                        z = zscore_in_window(window, data[-1])
                        z_scores[scope] = float(z) if not (np.isnan(z) or np.isinf(z)) else 0.0
                except Exception:
                    z_scores[scope] = 0.0
            current_score = sum(z_scores.values())
            if np.isnan(current_score) or np.isinf(current_score):
                current_score = 0.0
        except Exception as e:
            logger.warning(f"[{i}] L2 failed: {e}")
            continue

        score_history.append(current_score)
        if len(score_history) < 3:
            continue

        try:
            layer3_output = layer3(
                current_score=current_score,
                score_history=score_history,
                current_price=candle["close"],
                price_history=price_slice,
                penalty_score=penalty_score,
                penalty_price=penalty_price,
                score_model=model_score,
                price_model=model_price,
            )
        except InsufficientScoreHistoryError:
            continue
        except Exception:
            continue

        entry = layer3_output.get("entry", "0")
        if entry not in ("LONG", "SHORT"):
            continue

        atr = compute_atr(candles, i, atr_period)

        signals.append({
            "candle_index": i,
            "direction": entry,
            "entry_price": layer3_output["entry_price"],
            "prod_sl": layer3_output.get("stop_loss"),
            "prod_tp": layer3_output.get("take_profit"),
            "atr": atr,
        })

        if i % 100 == 0:
            print(f"  [{i:4d}/{len(candles)}] score={current_score:+.3f} signal={entry:5s} total_signals={len(signals)}")

    print(f"\n  Total signals collected: {len(signals)}\n")
    if not signals:
        print("  No signals — cannot simulate.")
        return

    # =================================================================
    # Run all 4 methods over every signal
    # =================================================================
    results = {"CURRENT (regime-range)": [], "ATR_FIXED": [], "ATR_TRAILING": [], "TIME_EXIT": []}

    for s in signals:
        i = s["candle_index"]
        d = s["direction"]
        entry_price = s["entry_price"]
        atr = s["atr"]

        r = simulate_current(candles, i, d, entry_price, s["prod_sl"], s["prod_tp"], max_hold)
        if r:
            results["CURRENT (regime-range)"].append(r)

        r = simulate_atr_fixed(candles, i, d, entry_price, atr, sl_atr_mult, tp_atr_mult, max_hold)
        if r:
            results["ATR_FIXED"].append(r)

        r = simulate_atr_trailing(candles, i, d, entry_price, atr, trail_atr_mult, max_hold)
        if r:
            results["ATR_TRAILING"].append(r)

        r = simulate_time_exit(candles, i, d, entry_price, atr, time_exit_candles)
        if r:
            results["TIME_EXIT"].append(r)

    # =================================================================
    # Report
    # =================================================================
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"  TP/SL METHOD COMPARISON — RESULTS")
    print(f"{sep}\n")
    print(f"  {'Method':<24}{'N':>6}{'WinRate':>10}{'Timeout%':>10}{'AvgR':>10}{'Expectancy(R)':>16}")
    print(f"  {'-'*76}")

    for name, trades in results.items():
        if not trades:
            print(f"  {name:<24}{'no data':>6}")
            continue
        n = len(trades)
        wins = sum(1 for t in trades if t["outcome"] == "WIN")
        timeouts = sum(1 for t in trades if t["outcome"] == "TIMEOUT")
        avg_r = float(np.mean([t["r"] for t in trades]))
        win_rate = wins / n * 100
        timeout_rate = timeouts / n * 100
        # Expectancy per trade in R, using actual outcomes (not a
        # theoretical win_rate*avg_win formula — this uses realized r
        # for every trade including timeouts, so it's already the
        # expectancy).
        print(f"  {name:<24}{n:>6}{win_rate:>9.1f}%{timeout_rate:>9.1f}%{avg_r:>10.3f}{avg_r:>16.3f}")

    print(f"\n{sep}")
    print("  READING THIS TABLE")
    print(f"{sep}")
    print("""
  - AvgR / Expectancy = average realized R-multiple per trade (this
    already accounts for wins, losses, AND timeouts — it's the bottom
    line, not a partial metric).
  - Positive expectancy = method has an edge BEFORE fees/slippage.
  - A method can have a low win rate but positive expectancy if wins
    are big relative to losses (or vice versa) — don't judge by win
    rate alone.
  - CURRENT (regime-range) is what src/risk.py does today. If it's
    worse than ATR_FIXED or ATR_TRAILING here, that's the evidence
    needed before touching risk.py — but re-run on a second, different
    time window before acting on it.
  - This does not include fees/slippage. A method that wins here can
    still be unprofitable after realistic friction — check that
    separately before deploying.
""")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TTE TP/SL Method Comparison Simulator")
    parser.add_argument("--exchange", default="gateio")
    parser.add_argument("--pair", default="ETH_USDT")
    parser.add_argument("--tf", default="1h")
    parser.add_argument("--candles", default=800, type=int)
    parser.add_argument("--warmup", default=50, type=int)
    parser.add_argument("--market", default="perpetual")
    parser.add_argument("--recal-interval", default=100, type=int)
    parser.add_argument("--sl-atr-mult", default=1.5, type=float, help="ATR_FIXED: SL distance in ATR multiples")
    parser.add_argument("--tp-atr-mult", default=3.0, type=float, help="ATR_FIXED: TP distance in ATR multiples")
    parser.add_argument("--trail-atr-mult", default=1.5, type=float, help="ATR_TRAILING: trail distance in ATR multiples")
    parser.add_argument("--time-exit-candles", default=20, type=int, help="TIME_EXIT: fixed holding period")
    parser.add_argument("--max-hold", default=50, type=int, help="Max candles to hold before forced timeout exit")
    parser.add_argument("--atr-period", default=14, type=int)
    parser.add_argument("--end-date", default=None,
                         help="ISO date (e.g. 2026-07-20) to anchor the data window. "
                              "REQUIRED if you want to compare different --trail-atr-mult "
                              "values apples-to-apples — otherwise the window shifts every run.")
    parser.add_argument("--fetch-buffer", default=2000, type=int,
                         help="How many candles to fetch before slicing to --end-date. "
                              "Capped at 2000 (Gate.io API limit). Must be large enough "
                              "to cover today back to --end-date, plus --candles.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    run_simulation(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.tf,
        market_type=args.market,
        n_candles=args.candles,
        min_warmup=args.warmup,
        recal_interval=args.recal_interval,
        sl_atr_mult=args.sl_atr_mult,
        tp_atr_mult=args.tp_atr_mult,
        trail_atr_mult=args.trail_atr_mult,
        time_exit_candles=args.time_exit_candles,
        max_hold=args.max_hold,
        atr_period=args.atr_period,
        end_date=args.end_date,
        fetch_buffer=args.fetch_buffer,
    )