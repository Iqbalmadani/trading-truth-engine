"""
LIVE Process — Trading Signal Generation & Execution Management (TTE v2)

Reads active combos from config/trading_pairs.yaml.
For each combo:
1. L4 Circuit Breaker Check (Effective Sample Size & Kill Switch)
2. L1 Existence Check (Verifier)
3. L2 Structural Scoring (CPD + Z-Score)
4. L3 Regime-Adaptive Signal Generation (Hurst + Variance Ratio + ATR SL)
5. L4 Signal Logging & Pending Outcome Evaluation

Usage:
    python scripts/run_live.py
    python scripts/run_live.py --once
    python scripts/run_live.py --exchange gateio --pair ETH_USDT --tf 1h --once
"""

import sys
import argparse
import logging
import time
import yaml
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Layer 1
from src.layer1.connectors.registry import get_connector
from src.layer1.verifier import Verifier

# Layer 2
from src.layer2.aggregator import layer2
from src.layer2.penalty_manager import PenaltyManager

# Layer 3
from src.layer3.executor import layer3
from src.layer3.penalty_manager import PenaltyManagerLayer3
from src.layer3.thresholds import InsufficientScoreHistoryError

# Layer 4
from src.layer4.circuit_breaker import evaluate_performance
from src.layer4.signal_logger import log_signal, check_outcomes

# Utils
from src.utils.history_manager import HistoryManager
from src.utils import database as db
from src.utils.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)

TRADING_PAIRS_PATH = Path(__file__).resolve().parent.parent / "config" / "trading_pairs.yaml"


def setup_logging() -> Path:
    """Configure logging with file and console handlers."""
    log_dir = Path(__file__).resolve().parent.parent / "logs" / "scheduled_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "live.log"

    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger.addHandler(console_handler)

    return log_file


def load_active_pairs() -> Dict:
    """Load active trading pairs from config/trading_pairs.yaml."""
    if not TRADING_PAIRS_PATH.exists():
        logger.error(f"trading_pairs.yaml not found at {TRADING_PAIRS_PATH}")
        return {}

    with open(TRADING_PAIRS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    return data.get("active_pairs", {})


def _build_output(
    signal_type: str,
    entry_price: Optional[float],
    stop_loss: Optional[float],
    take_profit: Optional[float],
    scope_count: int,
    timestamp: str,
    note: Optional[str] = None,
    regime_type: str = "insufficient_data",
) -> Dict[str, Any]:
    return {
        "signal": signal_type,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "scope_count": scope_count,
        "timestamp": timestamp,
        "note": note,
        "regime_type": regime_type,
    }


def run_live_single(
    exchange: str,
    pair: str,
    timeframe: str,
    market_type: str = "perpetual",
    lookback_periods: int = 50,
) -> Dict[str, Any]:
    """
    Run LIVE pipeline for a single combo.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    combo_id = f"{exchange}/{pair}/{timeframe}"

    try:
        # ===== STEP 1: LAYER 4 CIRCUIT BREAKER =====
        perf = evaluate_performance(exchange, pair, timeframe)
        if not perf["should_trade"]:
            logger.warning(f"[{combo_id}] Circuit breaker ACTIVE: {perf['reason']}")
            return _build_output(
                "0", None, None, None, 0, timestamp,
                note=f"Circuit Breaker: {perf['reason']}"
            )

        # ===== STEP 2: CONNECTOR & LAYER 1 EXISTENCE =====
        try:
            connector = get_connector(exchange)
        except ValueError as e:
            logger.error(f"[{combo_id}] Connector error: {e}")
            return _build_output("0", None, None, None, 0, timestamp, note=str(e))

        verifier = Verifier()
        layer1_results = verifier.verify_all(connector, pair, timeframe, market_type)
        scope_count = sum(1 for v in layer1_results.values() if v == 1)

        if scope_count == 0:
            logger.warning(f"[{combo_id}] No active scopes detected by Layer 1")
            return _build_output("0", None, None, None, 0, timestamp, note="No active scopes")

        # ===== STEP 3: LAYER 2 SCORING =====
        penalty_manager_l2 = PenaltyManager(exchange=exchange, pair=pair, timeframe=timeframe)
        if not penalty_manager_l2.get_all_penalties():
            logger.warning(f"[{combo_id}] No L2 penalties found. Run calibration first.")
            return _build_output(
                "0", None, None, None, scope_count, timestamp,
                note="No L2 penalties. Run calibration first."
            )

        current_score, layer2_metadata = layer2(
            connector, exchange, layer1_results, pair, timeframe,
            market_type=market_type,
            penalty_manager=penalty_manager_l2,
            lookback_periods=lookback_periods,
        )

        logger.info(f"[{combo_id}] Layer 2 Score: {current_score:.4f}")

        # Record score in history
        history_mgr = HistoryManager(exchange=exchange, pair=pair, timeframe=timeframe)
        history_mgr.append_score(current_score, pair=pair, timeframe=timeframe, candle_timestamp=int(time.time()))
        score_history = history_mgr.get_score_history()

        # Fetch price series for L3
        ohlcv_history = connector.fetch_history(
            "ohlcv", pair, timeframe, market_type, lookback_periods
        )
        if not ohlcv_history or len(ohlcv_history) < 3:
            logger.warning(f"[{combo_id}] Insufficient OHLCV history for L3")
            return _build_output("0", None, None, None, scope_count, timestamp, note="Insufficient OHLCV data")

        price_history = [float(c["close"]) if isinstance(c, dict) else float(c) for c in ohlcv_history]
        high_history = [float(c["high"]) for c in ohlcv_history] if isinstance(ohlcv_history[0], dict) else None
        low_history = [float(c["low"]) for c in ohlcv_history] if isinstance(ohlcv_history[0], dict) else None
        current_price = price_history[-1]
        current_high = high_history[-1] if high_history else current_price
        current_low = low_history[-1] if low_history else current_price

        # Check outcomes of pending trades for this combo
        outcomes_res = check_outcomes(
            exchange, pair, timeframe,
            current_price=current_price,
            current_high=current_high,
            current_low=current_low,
        )
        if outcomes_res["checked"] > 0:
            logger.info(
                f"[{combo_id}] Checked {outcomes_res['checked']} pending trades: "
                f"TP={outcomes_res['tp_hit']}, SL={outcomes_res['sl_hit']}, Expired={outcomes_res['expired']}"
            )

        # ===== STEP 4: LAYER 3 REGIME-ADAPTIVE EXECUTION =====
        penalty_manager_l3 = PenaltyManagerLayer3(exchange=exchange, pair=pair, timeframe=timeframe)
        try:
            penalty_score = penalty_manager_l3.get_score_penalty()
            penalty_price = penalty_manager_l3.get_price_penalty()
            score_model = penalty_manager_l3.get_score_model()
            price_model = penalty_manager_l3.get_price_model()
        except RuntimeError as e:
            logger.warning(f"[{combo_id}] {e}")
            return _build_output("0", current_price, None, None, scope_count, timestamp, note=str(e))

        price_cpd_window = layer2_metadata.get("price_cpd_window")

        layer3_output = layer3(
            current_score=current_score,
            score_history=score_history,
            current_price=current_price,
            price_history=price_history,
            penalty_score=penalty_score,
            penalty_price=penalty_price,
            high_history=high_history,
            low_history=low_history,
            score_model=score_model,
            price_model=price_model,
            price_cpd_window=price_cpd_window,
        )

        entry = layer3_output.get("entry", "0")
        sl = layer3_output.get("stop_loss")
        tp = layer3_output.get("take_profit")
        meta = layer3_output.get("metadata", {})
        regime_type = meta.get("regime_type", "insufficient_data")

        # ===== STEP 5: LAYER 4 SIGNAL LOGGING =====
        if entry in ("LONG", "SHORT"):
            log_signal(exchange, pair, timeframe, layer3_output)

        logger.info(
            f"[{combo_id}] Signal: {entry} @ {current_price:.6f} "
            f"(Regime: {regime_type}, SL={sl}, TP={tp})"
        )

        return _build_output(
            entry, current_price, sl, tp, scope_count, timestamp,
            note=None, regime_type=regime_type
        )

    except Exception as e:
        logger.error(f"[{combo_id}] Live processing failed: {e}", exc_info=True)
        return _build_output("0", None, None, None, 0, timestamp, note=str(e))


def run_live_cycle(active_pairs: Dict) -> None:
    """Run one full pass across all active trading pairs."""
    db.init_schema()
    start_time = time.time()
    combos_processed = 0

    for exchange, pairs in active_pairs.items():
        for pair, config in pairs.items():
            timeframes = config.get("timeframes", [])
            market_type = config.get("market_type", "perpetual")

            for tf in timeframes:
                run_live_single(exchange, pair, tf, market_type)
                combos_processed += 1

    elapsed = time.time() - start_time
    logger.info(f"Live cycle finished: {combos_processed} combos in {elapsed:.2f}s")


def main():
    parser = argparse.ArgumentParser(description="Trading Truth Engine v2 — Live Signal Generator")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--exchange", type=str, help="Override exchange (e.g. gateio)")
    parser.add_argument("--pair", type=str, help="Override pair (e.g. ETH_USDT)")
    parser.add_argument("--tf", type=str, help="Override timeframe (e.g. 1h)")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds (default: 60)")
    args = parser.parse_args()

    setup_logging()
    logger.info("Starting Trading Truth Engine v2 Live Process")

    if args.exchange and args.pair and args.tf:
        logger.info(f"Running single combo override: {args.exchange}/{args.pair}/{args.tf}")
        result = run_live_single(args.exchange, args.pair, args.tf)
        print(f"\nResult: {result}\n")
        return

    active_pairs = load_active_pairs()
    if not active_pairs:
        logger.error("No active trading pairs configured in trading_pairs.yaml")
        return

    if args.once:
        run_live_cycle(active_pairs)
        return

    logger.info(f"Entering continuous live loop (interval: {args.interval}s)")
    try:
        while True:
            run_live_cycle(active_pairs)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("Live process stopped by user")


if __name__ == "__main__":
    main()
