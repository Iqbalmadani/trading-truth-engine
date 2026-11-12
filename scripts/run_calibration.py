"""
CALIBRATION Process — Continuous Meta-Learning Loop

Reads active combos from config/trading_pairs.yaml.
Loops continuously, recalibrating penalties when interval_candles new data is available.

Uses 2 infrastructure parameters from config/infrastructure.yaml:
  - window_days: how much data PELT looks at (auto-adjust per TF)
  - interval_candles: how often to recalibrate (auto-adjust per TF)

Usage:
    python scripts/run_calibration.py          # Loop forever
    python scripts/run_calibration.py --once   # Single pass then exit
"""

import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import logging
import yaml
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from src.layer1.connectors.registry import get_connector

# Meta-learning
from src.layer2.meta_learning import meta_learn_all_scopes
from src.layer3.meta_learning import meta_learn_all_layer3_penalties
from src.layer2.meta_learning import _apply_window_cap

# Database
from src.utils import database as db
from src.utils.config_loader import get_infrastructure_config
from src.utils.constants import TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)

# Config paths
TRADING_PAIRS_PATH = Path(__file__).parent.parent / "config" / "trading_pairs.yaml"


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calibrate TTE from configured public market data."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one calibration cycle and exit instead of looping.",
    )
    return parser


def setup_logging():
    """Configure logging with file output."""
    log_dir = Path(__file__).parent.parent / "logs" / "scheduled_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "calibration.log"

    root_logger = logging.getLogger()
    root_logger.handlers = []
    root_logger.setLevel(logging.INFO)

    file_handler = logging.FileHandler(log_file, mode='a')
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

    with open(TRADING_PAIRS_PATH, "r") as f:
        data = yaml.safe_load(f)

    return data.get("active_pairs", {})


def should_recalibrate(
    exchange: str, pair: str, timeframe: str, interval_candles: int
) -> bool:
    """
    Check if enough new candles have arrived since last calibration.

    Uses the penalties table's updated_at timestamp to determine
    how many candles worth of time have passed.

    Returns True if recalibration is needed.
    """
    combo_id = f"{exchange}/{pair}/{timeframe}"

    # Check if any penalty exists for this combo
    existing = db.get_all_penalties(exchange, pair, timeframe, "l2")
    if not existing:
        logger.info(f"[{combo_id}] No penalties found — first calibration needed")
        return True

    # Get the most recent update time
    penalty_data = db.get_penalty(exchange, pair, timeframe, "l2", "ohlcv")
    if not penalty_data:
        # Try any scope
        for scope_data in existing.values():
            penalty_data = scope_data
            break

    if not penalty_data or "updated_at" not in penalty_data:
        return True

    try:
        last_updated = datetime.fromisoformat(penalty_data["updated_at"])
        # Ensure timezone-aware
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        seconds_since = (now - last_updated).total_seconds()

        tf_seconds = TIMEFRAME_SECONDS.get(timeframe, 3600)
        candles_since = seconds_since / tf_seconds

        if candles_since >= interval_candles:
            logger.info(
                f"[{combo_id}] {candles_since:.1f} candles since last calibration "
                f"(threshold: {interval_candles}) — RECALIBRATE"
            )
            return True
        else:
            logger.debug(
                f"[{combo_id}] {candles_since:.1f} candles since last calibration "
                f"(threshold: {interval_candles}) — SKIP"
            )
            return False

    except Exception as e:
        logger.warning(f"[{combo_id}] Error checking recalibration time: {e}")
        return True


def calibrate_combo(
    exchange: str, pair: str, timeframe: str, market_type: str,
    lookback_periods: int = 100
) -> bool:
    """
    Run meta-learning for a single combo and save penalties to SQLite.

    Returns True if calibration succeeded.
    """
    combo_id = f"{exchange}/{pair}/{timeframe}"
    start_time = time.time()

    try:
        try:
            connector = get_connector(exchange)
        except ValueError as e:
            logger.error(f"[{combo_id}] {e}")
            return False

        # ===== L2 META-LEARNING =====
        logger.info(f"[{combo_id}] Running L2 meta-learning...")

        l2_result = meta_learn_all_scopes(
            connector=connector,
            exchange=exchange,
            pair=pair,
            timeframe=timeframe,
            market_type=market_type,
            lookback_periods=lookback_periods,
        )

        l2_penalties = l2_result.get("penalties", {})
        l2_models = l2_result.get("models", {})

        # Save L2 penalties to SQLite
        db.save_all_penalties(
            exchange, pair, timeframe, "l2", l2_penalties, l2_models
        )

        logger.info(
            f"[{combo_id}] L2 penalties saved: "
            f"{', '.join(f'{k}={v:.6f}' for k, v in l2_penalties.items())}"
        )

        # ===== L3 META-LEARNING =====
        # L3 needs score_history and price_history
        score_history = db.get_score_history(exchange, pair, timeframe)
        if len(score_history) < 3:
            logger.warning(
                f"[{combo_id}] Insufficient score history for L3 ({len(score_history)}/3). "
                f"Skipping L3 calibration."
            )
        else:
            # Fetch price history for L3
            ohlcv = connector.fetch_history(
                "ohlcv", pair, timeframe, market_type, lookback_periods
            )
            price_history = []
            if ohlcv:
                for candle in ohlcv:
                    if isinstance(candle, dict) and "close" in candle:
                        price_history.append(float(candle["close"]))

            if len(price_history) >= 3:
                logger.info(f"[{combo_id}] Running L3 meta-learning...")

                # Apply window cap
                score_capped = _apply_window_cap(score_history, timeframe)
                price_capped = _apply_window_cap(price_history, timeframe)

                l3_result = meta_learn_all_layer3_penalties(
                    score_capped, price_capped
                )

                l3_penalties = l3_result.get("penalties", {})
                l3_models = l3_result.get("models", {})

                db.save_all_penalties(
                    exchange, pair, timeframe, "l3", l3_penalties, l3_models
                )

                logger.info(
                    f"[{combo_id}] L3 penalties saved: "
                    f"{', '.join(f'{k}={v:.6f}' for k, v in l3_penalties.items())}"
                )
            else:
                logger.warning(
                    f"[{combo_id}] Insufficient price history for L3 ({len(price_history)} points)"
                )

        elapsed = time.time() - start_time
        logger.info(f"[{combo_id}] Calibration complete in {elapsed:.1f}s")
        return True

    except Exception as e:
        logger.error(f"[{combo_id}] Calibration failed: {e}", exc_info=True)
        return False


def run_calibration_cycle(active_pairs: Dict, interval_candles: int) -> Dict:
    """
    Run one calibration cycle across all active combos.

    Returns dict of results per combo.
    """
    results = {}

    for exchange_name, pairs in active_pairs.items():
        for pair_name, pair_config in pairs.items():
            timeframes = pair_config.get("timeframes", [])
            market_type = pair_config.get("market_type", "perpetual")

            for tf in timeframes:
                combo_id = f"{exchange_name}/{pair_name}/{tf}"

                if should_recalibrate(exchange_name, pair_name, tf, interval_candles):
                    success = calibrate_combo(
                        exchange_name, pair_name, tf, market_type
                    )
                    results[combo_id] = "CALIBRATED" if success else "FAILED"
                else:
                    results[combo_id] = "SKIPPED"

    return results


if __name__ == "__main__":
    args = build_argument_parser().parse_args()
    setup_logging()

    single_pass = args.once

    # Load calibration config
    try:
        infra = get_infrastructure_config()
        cal_config = infra.get("calibration", {})
        interval_candles = cal_config.get("interval_candles", 24)
    except Exception:
        interval_candles = 24

    logger.info("=" * 70)
    logger.info("TTE CALIBRATION — Continuous Meta-Learning Loop")
    logger.info(f"  interval_candles: {interval_candles}")
    logger.info(f"  mode: {'single pass' if single_pass else 'continuous loop'}")
    logger.info("=" * 70)

    active_pairs = load_active_pairs()
    if not active_pairs:
        logger.error("No active pairs found in trading_pairs.yaml")
        sys.exit(1)

    # Count combos
    combo_count = sum(
        len(pc.get("timeframes", []))
        for pairs in active_pairs.values()
        for pc in pairs.values()
    )
    logger.info(f"Active combos: {combo_count}")

    if single_pass:
        # Single pass mode
        results = run_calibration_cycle(active_pairs, interval_candles)
        logger.info("=" * 70)
        logger.info("CALIBRATION RESULTS:")
        for combo_id, status in results.items():
            logger.info(f"  {combo_id}: {status}")
        logger.info("=" * 70)
    else:
        # Continuous loop mode
        cycle = 0
        while True:
            cycle += 1
            logger.info(f"\n--- Calibration Cycle #{cycle} ---")

            try:
                results = run_calibration_cycle(active_pairs, interval_candles)

                calibrated = sum(1 for v in results.values() if v == "CALIBRATED")
                skipped = sum(1 for v in results.values() if v == "SKIPPED")
                failed = sum(1 for v in results.values() if v == "FAILED")

                logger.info(
                    f"Cycle #{cycle} complete: "
                    f"{calibrated} calibrated, {skipped} skipped, {failed} failed"
                )

            except Exception as e:
                logger.error(f"Cycle #{cycle} error: {e}", exc_info=True)

            # Wait before next cycle
            logger.info("Waiting 60 seconds before next cycle...")
            time.sleep(60)
