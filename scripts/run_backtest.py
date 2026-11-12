"""
Walk-Forward Backtest — Trading Truth Engine (With Layer 4 Simulation)

Simulates TTE's full pipeline chronologically on historical data.
No look-ahead bias: at candle i, only data[0..i-1] is visible.

Flow:
1. Layer 1 (Connectors / Verifier): Fetch N candles of historical data from exchange API
2. Walk forward candle by candle (i = min_warmup → N)
3. At each candle i:
   a. Layer 2 (Aggregator): Run L2 on data[0..i-1] → z-scores & composite score
   b. Layer 3 (Executor Kat A Pure): Run L3 on score & price history → raw signal (LONG/SHORT/0, Entry, SL, TP)
   c. Layer 4 Simulator (Kat A Sizing & OMS):
      - Compute Stop Loss distance Δ_SL = |Entry - SL| / Entry
      - Compute Position Size N = (Equity * Risk%) / Δ_SL
      - Compute Effective Leverage L = Risk% / Δ_SL (capped at exchange max)
      - OMS Guard: check max open positions & duplicate pair signals
      - Check pending positions vs candle i High/Low (resolve TP_HIT / SL_HIT)
      - Apply trading friction (taker fees 0.04% + slippage 0.05% on entry & exit)
      - Update dynamic cash & account equity ledger
4. Print comprehensive performance & risk summary at the end

Usage:
    python scripts/run_backtest.py --exchange gateio --pair ETH_USDT --tf 1h --candles 1000
    python scripts/run_backtest.py --exchange binance --pair BTC_USDT --tf 4h --candles 500
"""

import sys
import argparse
import logging
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

# Layer 1
from src.layer1.connectors.registry import get_connector
from src.layer1.verifier import Verifier

# Layer 2
from src.layer2.aggregator import layer2
from src.layer2.cpd import get_cpd_window
from src.layer3.meta_learning import (
    meta_learn_score_penalty,
    meta_learn_price_penalty,
)

# Layer 3 (Pure Kat A — No Leverage, No Kat B)
from src.layer3.executor import layer3
from src.layer3.thresholds import InsufficientScoreHistoryError

# Utils
from src.utils.config_loader import get_infrastructure_config

logger = logging.getLogger(__name__)


# ============================================================================
# BACKTEST DATABASE — ISOLATED from live tte.db
# ============================================================================

def get_backtest_db_path(exchange: str, pair: str, timeframe: str) -> Path:
    pair_safe = pair.replace("/", "_")
    return Path(__file__).parent.parent / "data" / f"backtest_{exchange}_{pair_safe}_{timeframe}.db"


def init_backtest_db(db_path: Path) -> sqlite3.Connection:
    """Initialize isolated backtest SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bt_signals (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            candle_index        INTEGER NOT NULL,
            candle_ts           INTEGER NOT NULL,
            signal_type         TEXT NOT NULL,
            entry_price         REAL NOT NULL,
            stop_loss           REAL NOT NULL,
            take_profit         REAL NOT NULL,
            sl_distance_pct     REAL NOT NULL,
            effective_leverage  REAL NOT NULL,
            notional_size_usd   REAL NOT NULL,
            margin_usd          REAL NOT NULL,
            score               REAL,
            outcome             TEXT DEFAULT 'PENDING',
            outcome_candle      INTEGER,
            outcome_price       REAL,
            pnl_percent         REAL,
            pnl_usd             REAL,
            friction_fees_usd   REAL,
            account_equity      REAL,
            created_at          TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


# ============================================================================
# BACKTEST SIGNAL STORE — In-memory + DB operations
# ============================================================================

class BacktestSignalStore:
    """Manages virtual signals and Layer 4 simulation states."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log_signal(
        self,
        candle_index: int,
        candle_ts: int,
        signal_type: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        sl_distance_pct: float,
        effective_leverage: float,
        notional_size_usd: float,
        margin_usd: float,
        score: Optional[float],
    ) -> Optional[int]:
        if signal_type not in ("LONG", "SHORT"):
            return None
        cursor = self.conn.execute(
            """INSERT INTO bt_signals
               (candle_index, candle_ts, signal_type, entry_price,
                stop_loss, take_profit, sl_distance_pct, effective_leverage,
                notional_size_usd, margin_usd, score, outcome, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
            (
                candle_index, candle_ts, signal_type, entry_price,
                stop_loss, take_profit, sl_distance_pct, effective_leverage,
                notional_size_usd, margin_usd, score,
                datetime.now(timezone.utc).isoformat(),
            )
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_pending(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM bt_signals WHERE outcome = 'PENDING'"
        ).fetchall()
        return [dict(r) for r in rows]

    def resolve(
        self,
        signal_id: int,
        outcome: str,
        outcome_candle: int,
        outcome_price: float,
        pnl_percent: float,
        pnl_usd: float,
        friction_fees_usd: float,
        account_equity: float,
    ) -> None:
        self.conn.execute(
            """UPDATE bt_signals
               SET outcome=?, outcome_candle=?, outcome_price=?,
                   pnl_percent=?, pnl_usd=?, friction_fees_usd=?, account_equity=?
               WHERE id=?""",
            (outcome, outcome_candle, outcome_price, pnl_percent, pnl_usd, friction_fees_usd, account_equity, signal_id)
        )
        self.conn.commit()

    def get_resolved(self) -> List[Dict]:
        rows = self.conn.execute(
            "SELECT * FROM bt_signals WHERE outcome IN ('TP_HIT','SL_HIT','LIQUIDATED')"
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================================
# HISTORICAL DATA FETCH
# ============================================================================

def fetch_historical_ohlcv(
    connector,
    pair: str,
    timeframe: str,
    market_type: str,
    n_candles: int,
) -> List[Dict]:
    """
    Fetch N candles of full OHLCV data.
    Ordered oldest → newest.
    """
    exchange = connector.exchange_id

    if exchange == "binance":
        from src.layer1.connectors.binance import TIMEFRAME_MAP
        symbol = pair.replace("_", "")
        interval = TIMEFRAME_MAP.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe for Binance: {timeframe}")
        url = f"{connector.BASE_URL}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": interval, "limit": n_candles}
        data = connector._request(url, params)
        if not data:
            return []
        candles = []
        for c in data:
            candles.append({
                "timestamp": int(c[0]) // 1000,
                "open":  float(c[1]),
                "high":  float(c[2]),
                "low":   float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        return candles

    elif exchange == "gateio":
        from src.layer1.connectors.gateio import TIMEFRAME_MAP
        interval = TIMEFRAME_MAP.get(timeframe)
        if not interval:
            raise ValueError(f"Unsupported timeframe for Gate.io: {timeframe}")
        settle = "usdt"
        url = f"{connector.BASE_URL}/futures/{settle}/candlesticks"
        params = {"contract": pair, "interval": interval, "limit": n_candles}
        data = connector._request(url, params)
        if not data:
            return []
        candles = []
        for c in data:
            candles.append({
                "timestamp": int(c["t"]),
                "open":  float(c["o"]),
                "high":  float(c["h"]),
                "low":   float(c["l"]),
                "close": float(c["c"]),
                "volume": float(c.get("v", 0)),
            })
        return candles

    else:
        raise ValueError(f"Unknown exchange: {exchange}")


def fetch_historical_scope(
    connector,
    pair: str,
    timeframe: str,
    market_type: str,
    scope: str,
    n_candles: int,
) -> List[float]:
    """Fetch historical time-series data for a single L2 scope."""
    if scope == "open_interest":
        return []
    return connector.fetch_history(scope, pair, timeframe, market_type, n_candles)


# ============================================================================
# LAYER 4 SIMULATOR — Sizing, OMS Guard, EMS Friction & Equity Ledger
# ============================================================================

class Layer4Simulator:
    """
    Simulates Layer 4 Execution & Portfolio Management Engine inside backtest:
    - Kat A Position Sizing: Risk% / SL_distance
    - Effective Leverage Calculation
    - OMS Concurrency Guard
    - EMS Friction (Taker Fee + Slippage)
    - Dynamic Equity & Margin Management
    """

    def __init__(
        self,
        initial_capital: float = 1000.0,
        risk_per_trade_pct: float = 2.0,
        max_leverage_cap: float = 10.0,
        max_concurrency: int = 3,
        fee_rate: float = 0.0004,
        slippage_rate: float = 0.0005,
        fixed_sizing: bool = False,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.equity = initial_capital
        self.risk_pct = risk_per_trade_pct
        self.max_leverage = max_leverage_cap
        self.max_concurrency = max_concurrency
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.fixed_sizing = fixed_sizing

    def calculate_sizing(self, entry_price: float, stop_loss: float) -> Tuple[float, float, float, float]:
        """
        Calculate Kat A Position Sizing & Effective Leverage:
        sl_distance_pct = |entry - sl| / entry
        notional = (base_capital * risk_pct) / sl_distance_pct
        effective_leverage = risk_pct / sl_distance_pct (capped at max_leverage)

        Margin is capped at base_capital / max_concurrency to prevent single trade
        from consuming all capital and blocking concurrent positions.
        """
        sl_dist_pct = abs(entry_price - stop_loss) / entry_price
        if sl_dist_pct <= 1e-6:
            return 0.0, 1.0, 0.0, 0.0

        base_capital = self.initial_capital if self.fixed_sizing else self.equity

        # Implied leverage based on volatility / SL distance
        raw_eff_lev = self.risk_pct / (sl_dist_pct * 100.0)
        eff_lev = float(np.clip(raw_eff_lev, 1.0, self.max_leverage))

        # Risk amount in USD
        risk_usd = base_capital * (self.risk_pct / 100.0)
        notional_usd = risk_usd / sl_dist_pct

        # Ensure notional does not exceed base_capital * eff_lev
        max_allowed_notional = base_capital * eff_lev
        notional_usd = min(notional_usd, max_allowed_notional)

        margin_usd = notional_usd / eff_lev

        # Cap margin per trade
        max_margin_per_trade = base_capital / self.max_concurrency
        if margin_usd > max_margin_per_trade:
            margin_usd = max_margin_per_trade
            notional_usd = margin_usd * eff_lev

        return sl_dist_pct, eff_lev, notional_usd, margin_usd

    def can_open_position(self, store: BacktestSignalStore, new_signal_type: str = "") -> bool:
        """OMS Guard: check max concurrent open positions & duplicate directional trades."""
        pending = store.get_pending()
        if len(pending) >= self.max_concurrency or self.cash <= 10.0:
            return False
        if new_signal_type:
            for sig in pending:
                if sig["signal_type"] == new_signal_type:
                    return False
        return True

    def recalculate_equity(self, store: BacktestSignalStore, current_price: float) -> float:
        """Re-calculate total account equity: cash + open margins + unrealized PnL."""
        pending = store.get_pending()
        locked_margin = sum(s["margin_usd"] for s in pending)
        unrealized = 0.0
        for s in pending:
            entry = s["entry_price"]
            direction = 1.0 if s["signal_type"] == "LONG" else -1.0
            eff_lev = s["effective_leverage"]
            raw_pnl = direction * (current_price - entry) / entry * s["margin_usd"] * eff_lev
            unrealized += raw_pnl
        self.equity = self.cash + locked_margin + unrealized
        return self.equity

    def update_outcomes(self, store: BacktestSignalStore, candle_index: int, candle: Dict) -> Dict[str, int]:
        """EMS Execution Simulation: Check pending trades vs High/Low & apply friction."""
        pending = store.get_pending()
        stats = {"tp_hit": 0, "sl_hit": 0, "pending": 0}

        for sig in pending:
            signal_type = sig["signal_type"]
            entry = sig["entry_price"]
            sl = sig["stop_loss"]
            tp = sig["take_profit"]
            eff_lev = sig["effective_leverage"]
            notional = sig["notional_size_usd"]
            margin = sig["margin_usd"]
            high = candle["high"]
            low = candle["low"]
            sl_dist_pct = sig["sl_distance_pct"]

            outcome = None
            raw_pnl_pct = 0.0
            risk_usd = entry * sl_dist_pct

            if signal_type == "LONG":
                if low <= sl:
                    outcome = "TP_HIT" if sl > entry else "SL_HIT"
                    raw_pnl_pct = ((sl - entry) / entry) * 100.0 * eff_lev
                elif tp is not None and tp > 0 and high >= tp:
                    outcome = "TP_HIT"
                    raw_pnl_pct = abs((tp - entry) / entry) * 100.0 * eff_lev
                else:
                    # Update ATR trailing stop for open LONG position
                    new_sl = max(sl, high - risk_usd)
                    if new_sl > sl:
                        store.conn.execute(
                            "UPDATE bt_signals SET stop_loss=? WHERE id=?",
                            (new_sl, sig["id"])
                        )
                        store.conn.commit()

            elif signal_type == "SHORT":
                if high >= sl:
                    outcome = "TP_HIT" if sl < entry else "SL_HIT"
                    raw_pnl_pct = ((entry - sl) / entry) * 100.0 * eff_lev
                elif tp is not None and tp > 0 and low <= tp:
                    outcome = "TP_HIT"
                    raw_pnl_pct = abs((entry - tp) / entry) * 100.0 * eff_lev
                else:
                    # Update ATR trailing stop for open SHORT position
                    new_sl = min(sl, low + risk_usd)
                    if new_sl < sl:
                        store.conn.execute(
                            "UPDATE bt_signals SET stop_loss=? WHERE id=?",
                            (new_sl, sig["id"])
                        )
                        store.conn.commit()

            if outcome:
                # Friction calculation: Taker Fee + Slippage on both Entry & Exit notional
                entry_friction_usd = notional * (self.fee_rate + self.slippage_rate)
                
                # Exit price is current SL level (or TP if TP hit)
                exit_price = tp if (outcome == "TP_HIT" and tp is not None and tp > 0) else sl
                exit_notional_usd = (notional / entry) * exit_price
                exit_friction_usd = exit_notional_usd * (self.fee_rate + self.slippage_rate)
                
                total_friction_usd = entry_friction_usd + exit_friction_usd

                # Net USD PnL after friction
                raw_pnl_usd = margin * (raw_pnl_pct / 100.0)
                net_pnl_usd = raw_pnl_usd - total_friction_usd
                net_pnl_pct = (net_pnl_usd / margin) * 100.0 if margin > 0 else 0.0

                # Update cash: return margin + net PnL
                self.cash += margin + net_pnl_usd

                # Resolve the trade FIRST to remove it from pending
                store.resolve(
                    signal_id=sig["id"],
                    outcome=outcome,
                    outcome_candle=candle_index,
                    outcome_price=exit_price,
                    pnl_percent=net_pnl_pct,
                    pnl_usd=net_pnl_usd,
                    friction_fees_usd=total_friction_usd,
                    account_equity=0.0
                )

                self.recalculate_equity(store, candle["close"])

                store.conn.execute(
                    "UPDATE bt_signals SET account_equity=? WHERE id=?",
                    (self.equity, sig["id"])
                )
                store.conn.commit()

                stats[outcome.lower()] += 1
                logger.debug(
                    f"[{candle_index}] Signal #{sig['id']} {signal_type} -> {outcome} | "
                    f"Net PnL: {net_pnl_pct:+.2f}% (${net_pnl_usd:+.2f}) | "
                    f"Friction: ${total_friction_usd:.2f} | Equity: ${self.equity:.2f}"
                )
            else:
                stats["pending"] += 1

        # Re-sync equity for remaining pending trades
        self.recalculate_equity(store, candle["close"])
        return stats


# ============================================================================
# PENALTY LEARNING — Walk-forward calibration (Kat A)
# ============================================================================

def compute_bt_penalties(
    score_history: List[float],
    price_history: List[float],
) -> Tuple[float, float, str, str]:
    """Compute Kat A penalties from accumulated history."""
    MIN_POINTS = 30

    def fallback_penalty(series):
        if len(series) < 3:
            return 1.0, "rbf"
        var = float(np.var(series))
        if var < 1e-15:
            return 1.0, "rbf"
        return float(2.0 * var * np.log(len(series))), "rbf"

    if len(score_history) < MIN_POINTS:
        sp, sm = fallback_penalty(score_history)
        pp, pm = fallback_penalty(price_history)
        return sp, pp, sm, pm

    try:
        penalty_score, model_score, score_ch, score_sig = meta_learn_score_penalty(score_history)
    except Exception as e:
        logger.debug(f"Score meta-learning failed: {e}")
        penalty_score, model_score = fallback_penalty(score_history)

    try:
        penalty_price, model_price, price_ch, price_sig = meta_learn_price_penalty(price_history)
    except Exception as e:
        logger.debug(f"Price meta-learning failed: {e}")
        penalty_price, model_price = fallback_penalty(price_history)

    return penalty_score, penalty_price, model_score, model_price


# ============================================================================
# BACKTEST SUMMARY
# ============================================================================

def print_summary(
    store: BacktestSignalStore,
    l4_sim: Layer4Simulator,
    exchange: str,
    pair: str,
    timeframe: str,
    n_candles: int,
    db_path: Path,
):
    all_signals = store.conn.execute(
        "SELECT * FROM bt_signals ORDER BY candle_index"
    ).fetchall()
    all_signals = [dict(r) for r in all_signals]

    resolved_rows = store.conn.execute(
        "SELECT * FROM bt_signals WHERE outcome IN ('TP_HIT', 'SL_HIT', 'LIQUIDATED') ORDER BY outcome_candle ASC"
    ).fetchall()
    resolved  = [dict(r) for r in resolved_rows]
    total     = len(all_signals)
    longs     = sum(1 for s in all_signals if s["signal_type"] == "LONG")
    shorts    = sum(1 for s in all_signals if s["signal_type"] == "SHORT")
    pending   = [s for s in all_signals if s["outcome"] == "PENDING"]
    tp_hits   = [s for s in resolved if s["outcome"] == "TP_HIT"]
    sl_hits   = [s for s in resolved if s["outcome"] == "SL_HIT"]

    pnl_vals  = [s["pnl_percent"] for s in resolved if s.get("pnl_percent") is not None]
    pnl_usd_vals = [s["pnl_usd"] for s in resolved if s.get("pnl_usd") is not None]
    friction_vals = [s["friction_fees_usd"] for s in resolved if s.get("friction_fees_usd") is not None]
    lev_vals  = [s["effective_leverage"] for s in all_signals if s.get("effective_leverage") is not None]

    total_pnl_usd = sum(pnl_usd_vals)
    total_friction = sum(friction_vals)
    avg_pnl = np.mean(pnl_vals) if pnl_vals else 0.0

    won_trades = [p for p in pnl_usd_vals if p > 0]
    lost_trades = [p for p in pnl_usd_vals if p <= 0]
    win_rate = (len(won_trades) / len(resolved)) * 100.0 if resolved else 0.0
    sum_gains = sum(won_trades)
    sum_losses = sum(lost_trades)
    profit_factor = sum_gains / abs(sum_losses) if abs(sum_losses) > 1e-9 else (sum_gains if sum_gains > 0 else 1.0)

    # Calculate Max Drawdown from equity ledger
    initial_capital = 1000.0
    capital = initial_capital
    peak = initial_capital
    max_dd = 0.0
    for s in resolved:
        if s.get("account_equity") is not None:
            capital = s["account_equity"]
        if capital > peak:
            peak = capital
        dd = (peak - capital) / peak * 100.0 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    compounding_return = ((l4_sim.equity - initial_capital) / initial_capital) * 100.0

    sep = "=" * 65
    print(f"\n{sep}")
    print(f"  TTE WALK-FORWARD BACKTEST (LAYER 4 SIMULATED) — {exchange}/{pair}/{timeframe}")
    print(f"{sep}")
    print(f"  Candles simulated      : {n_candles}")
    print(f"  Signals generated      : {total} ({longs} LONG, {shorts} SHORT)")
    print(f"  Resolved trades        : {len(resolved)} ({len(tp_hits)} TP, {len(sl_hits)} SL)")
    print(f"  Still pending          : {len(pending)}")
    print(f"  Win rate               : {win_rate:.1f}%")
    print(f"  Profit factor          : {profit_factor:.2f}")
    print(f"  Max drawdown           : -{max_dd:.2f}%")
    print(f"  Net Compounded Return  : {compounding_return:+.2f}% (${total_pnl_usd:+.2f})")
    print(f"  Total Friction Paid    : ${total_friction:.2f} (Fees + Slippage)")
    print(f"  Avg P&L per trade      : {avg_pnl:+.2f}%")
    if lev_vals:
        print(f"  Effective Leverage Range: Min {min(lev_vals):.2f}x | Med {np.median(lev_vals):.2f}x | Max {max(lev_vals):.2f}x")

    print(f"\n  Full SQLite DB saved to : {db_path}")

    try:
        from scripts.export_backtest_dashboard import calculate_metrics, generate_html_report
        stats, equity_points = calculate_metrics(all_signals)
        pair_safe = pair.replace("/", "_")
        dashboard_path = db_path.parent / f"dashboard_{exchange}_{pair_safe}_{timeframe}.html"
        generate_html_report(
            exchange=exchange,
            pair=pair,
            timeframe=timeframe,
            stats=stats,
            equity_points=equity_points,
            all_trades=all_signals,
            output_path=dashboard_path
        )
        print(f"  Interactive dashboard   : {dashboard_path.absolute()}")
    except Exception as e:
        print(f"  Warning: Could not export HTML dashboard: {e}")
        
    print(f"{sep}\n")


# ============================================================================
# MAIN BACKTEST LOOP
# ============================================================================

def run_backtest(
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str,
    n_candles: int,
    min_warmup: int,
    recal_interval: int = 100,
    capital: float = 1000.0,
    risk_pct: float = 2.0,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0005,
    max_concurrency: int = 3,
    fixed_sizing: bool = False,
):
    """Walk-forward backtest main loop using real L1-L3 engine and Layer 4 simulator."""
    logger.info(f"Starting Walk-Forward Backtest: {exchange}/{pair}/{timeframe} | {n_candles} candles")

    connector = get_connector(exchange)

    logger.info(f"Fetching {n_candles} candles from {exchange}...")
    candles = fetch_historical_ohlcv(connector, pair, timeframe, market_type, n_candles)

    if len(candles) < min_warmup + 10:
        logger.error(f"Not enough candles: got {len(candles)}, need {min_warmup + 10}")
        return

    logger.info(f"Fetched {len(candles)} candles. First: {candles[0]['timestamp']}, Last: {candles[-1]['timestamp']}")

    logger.info("Fetching historical scope data for Layer 2...")
    scope_histories = {}
    active_scopes = []

    for scope in ["ohlcv", "funding_rate", "volatility_compression"]:
        history = fetch_historical_scope(
            connector, pair, timeframe, market_type, scope, n_candles
        )
        if history and len(history) >= min_warmup:
            scope_histories[scope] = history
            active_scopes.append(scope)
            logger.info(f"  {scope}: {len(history)} points")
        else:
            logger.warning(f"  {scope}: insufficient data ({len(history) if history else 0} points) — skipped")

    if not active_scopes:
        logger.error("No active scopes available — cannot run backtest")
        return

    close_prices = [c["close"] for c in candles]
    high_prices = [c["high"] for c in candles]
    low_prices = [c["low"] for c in candles]

    # Initialize isolated backtest DB
    db_path = get_backtest_db_path(exchange, pair, timeframe)
    if db_path.exists():
        try:
            db_path.unlink()
            logger.info(f"Removed old backtest DB: {db_path}")
        except PermissionError:
            tmp_conn = init_backtest_db(db_path)
            tmp_conn.execute("DELETE FROM bt_signals")
            tmp_conn.commit()
            tmp_conn.close()
            logger.info(f"Wiped existing backtest DB tables: {db_path}")

    conn = init_backtest_db(db_path)
    store = BacktestSignalStore(conn)

    # Get exchange max leverage limit, with hard safety cap at 10x.
    # Exchange APIs may report very high limits (e.g. Gate.io 200x) which are
    # dangerous for automated backtest sizing. We cap at 10x to keep risk sane.
    HARD_LEVERAGE_CAP = 10.0
    max_leverage = connector.get_leverage_limit(pair, market_type)
    if max_leverage is None:
        max_leverage = HARD_LEVERAGE_CAP
    else:
        max_leverage = min(max_leverage, HARD_LEVERAGE_CAP)

    # Initialize Layer 4 Simulator
    l4_sim = Layer4Simulator(
        initial_capital=capital,
        risk_per_trade_pct=risk_pct,
        max_leverage_cap=max_leverage,
        max_concurrency=max_concurrency,
        fee_rate=fee_rate,
        slippage_rate=slippage_rate,
        fixed_sizing=fixed_sizing,
    )

    # State variables
    score_history: List[float] = []
    penalty_score = 1.0
    penalty_price = 1.0
    model_score   = "rbf"
    model_price   = "rbf"

    # ========== CHRONOLOGICAL WALK-FORWARD LOOP ==========
    for i in range(min_warmup, len(candles)):
        candle = candles[i]
        candle_ts = candle["timestamp"]

        # --- Step 1: Layer 4 EMS Simulator — Resolve open trades using current candle ---
        outcome_stats = l4_sim.update_outcomes(store, i, candle)

        # Check for liquidation / margin call
        if l4_sim.equity <= 0:
            logger.error(f"[{i}] MARGIN CALL / LIQUIDATION: Equity hit {l4_sim.equity:.2f}$. Halting backtest.")
            for sig in store.get_pending():
                store.resolve(
                    signal_id=sig["id"],
                    outcome="LIQUIDATED",
                    outcome_candle=i,
                    outcome_price=candle["close"],
                    pnl_percent=-100.0,
                    pnl_usd=-sig["margin_usd"],
                    friction_fees_usd=0.0,
                    account_equity=0.0
                )
            break

        # --- Step 2: Build L2 inputs from data[0..i-1] ---
        scope_slice = {}
        for scope, history in scope_histories.items():
            slice_end = min(i + 1, len(history))
            scope_slice[scope] = history[:slice_end]

        price_slice = close_prices[:i + 1]
        high_slice = high_prices[:i + 1]
        low_slice = low_prices[:i + 1]

        if len(price_slice) < 3:
            continue

        # --- Step 3: Recalibrate L2/L3 penalties periodically ---
        if i % recal_interval == 0 and len(score_history) >= 30:
            penalty_score, penalty_price, model_score, model_price = compute_bt_penalties(
                score_history, price_slice
            )

        # --- Step 4: Run Real Layer 2 (Aggregator & Scoring) ---
        try:
            from src.layer2.cpd import get_cpd_window
            from src.layer2.zscore import zscore_in_window

            z_scores = {}
            for scope in active_scopes:
                data = scope_slice.get(scope, [])
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
            logger.warning(f"[{i}] Real L2 failed: {e}")
            continue

        score_history.append(current_score)

        if len(score_history) < 3:
            continue

        # --- Step 5: Run Real Layer 3 (Pure Structural Signal Generation) ---
        try:
            price_cpd_window_data, _ = get_cpd_window(price_slice, penalty_price, model=model_price)
            layer3_output = layer3(
                current_score=current_score,
                score_history=score_history,
                current_price=candle["close"],
                price_history=price_slice,
                penalty_score=penalty_score,
                penalty_price=penalty_price,
                high_history=high_slice,
                low_history=low_slice,
                score_model=model_score,
                price_model=model_price,
                price_cpd_window=price_cpd_window_data,
            )
        except InsufficientScoreHistoryError:
            continue
        except Exception as e:
            logger.warning(f"[{i}] Real L3 failed: {e}")
            continue

        entry = layer3_output.get("entry", "0")

        # --- Step 6: Layer 4 OMS & Sizing Simulation ---
        if entry in ("LONG", "SHORT"):
            sl = layer3_output.get("stop_loss")
            tp = layer3_output.get("take_profit")
            rr = layer3_output.get("risk_reward_ratio")

            if sl is not None:
                if l4_sim.can_open_position(store, new_signal_type=entry):
                    sl_dist_pct, eff_lev, notional_usd, margin_usd = l4_sim.calculate_sizing(
                        entry_price=layer3_output["entry_price"], stop_loss=sl
                    )

                    if margin_usd >= 5.0 and l4_sim.cash >= margin_usd:
                        l4_sim.cash -= margin_usd
                        l4_sim.recalculate_equity(store, candle["close"])
                        meta = layer3_output.get("metadata", {})

                        signal_id = store.log_signal(
                            candle_index=i,
                            candle_ts=candle_ts,
                            signal_type=entry,
                            entry_price=layer3_output["entry_price"],
                            stop_loss=sl,
                            take_profit=tp if tp is not None else 0.0,
                            sl_distance_pct=sl_dist_pct,
                            effective_leverage=eff_lev,
                            notional_size_usd=notional_usd,
                            margin_usd=margin_usd,
                            score=meta.get("score"),
                        )
                        tp_str = f"{tp:.4f}" if tp is not None else "None"
                        rr_str = f"{rr:.2f}" if rr is not None else "N/A"
                        logger.info(
                            f"[{i}] L4 EXECUTION: {entry} @ {candle['close']:.4f} | "
                            f"SL={sl:.4f} (Δ={sl_dist_pct * 100:.2f}%) | TP={tp_str} | "
                            f"RR={rr_str} | EffLev={eff_lev:.2f}x | Size=${notional_usd:.2f} | Margin=${margin_usd:.2f}"
                        )
                    else:
                        logger.debug(f"[{i}] L4 OMS SKIPPED: Cash balance too low (${l4_sim.cash:.2f})")
                else:
                    logger.info(f"[{i}] L4 OMS SKIPPED: Max concurrency or duplicate direction")

        if i % 50 == 0:
            resolved_n = len(store.get_resolved())
            total_sig  = store.conn.execute("SELECT COUNT(*) FROM bt_signals").fetchone()[0]
            print(
                f"  [{i:4d}/{len(candles)}] "
                f"score={current_score:+.3f} | "
                f"signal={entry} | "
                f"signals={total_sig} | "
                f"resolved={resolved_n} | "
                f"equity=${l4_sim.equity:.2f}"
            )

    # Final summary
    print_summary(store, l4_sim, exchange, pair, timeframe, n_candles, db_path)
    conn.close()


def setup_logging():
    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "backtest.log"

    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)
    ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(ch)

    return log_file


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TTE Walk-Forward Backtest with Layer 4 Simulation — Pure Kat A"
    )
    parser.add_argument("--exchange",  default="gateio",   help="Exchange (gateio/binance)")
    parser.add_argument("--pair",      default="ETH_USDT", help="Trading pair (ETH_USDT/BTC_USDT)")
    parser.add_argument("--tf",        default="1h",       help="Timeframe (1h/4h/1d)")
    parser.add_argument("--candles",   default=500,  type=int, help="Number of historical candles")
    parser.add_argument("--warmup",    default=50,   type=int, help="Minimum warmup candles before signaling")
    parser.add_argument("--market",    default="perpetual",   help="Market type")
    parser.add_argument("--recal-interval", default=100, type=int, help="Recalibrate penalties every N candles")
    parser.add_argument("--risk-pct",   default=2.0, type=float, help="Risk capital %% per trade")
    parser.add_argument("--fee-rate",   default=0.0004, type=float, help="Trading fee rate per side (taker/maker average)")
    parser.add_argument("--slippage-rate", default=0.0005, type=float, help="Slippage rate per trade side")
    parser.add_argument("--max-concurrency", default=3, type=int, help="Maximum concurrent open positions")
    parser.add_argument("--fixed-sizing", action="store_true", help="Use fixed position sizing based on initial capital instead of compounding equity")
    args = parser.parse_args()

    log_file = setup_logging()

    print(f"\n{'='*65}")
    print(f"  TTE WALK-FORWARD BACKTEST (PURE KAT A + L4 SIMULATOR)")
    print(f"  {args.exchange}/{args.pair}/{args.tf}")
    print(f"  Candles: {args.candles} | Warmup: {args.warmup} | Recal: {args.recal_interval}")
    print(f"  Risk Target: {args.risk_pct:.1f}% | Fee: {args.fee_rate * 100:.3f}% | Slippage: {args.slippage_rate * 100:.3f}% | Max Positions: {args.max_concurrency}")
    print(f"  Sizing Mode: {'FIXED (Non-compounding)' if args.fixed_sizing else 'COMPOUNDING (Equity-based)'}")
    print(f"  Log: {log_file}")
    print(f"{'='*65}\n")

    run_backtest(
        exchange=args.exchange,
        pair=args.pair,
        timeframe=args.tf,
        market_type=args.market,
        n_candles=args.candles,
        min_warmup=args.warmup,
        recal_interval=args.recal_interval,
        risk_pct=args.risk_pct,
        fee_rate=args.fee_rate,
        slippage_rate=args.slippage_rate,
        max_concurrency=args.max_concurrency,
        fixed_sizing=args.fixed_sizing,
    )
