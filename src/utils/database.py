"""
Database Module — SQLite Storage for TTE

Replaces JSON/YAML file-based persistence with SQLite database.
Provides namespace isolation per (exchange, pair, timeframe) combination.

Schema:
  - score_history: Layer 2 scores per namespace
  - snapshot_history: Snapshot scope values per namespace + scope
  - penalties: CPD penalties per namespace + layer + scope
  - signal_history: Trading signals + outcomes per namespace

Constitution Compliance:
  ✅ No data judgment — only storage mechanism change
  ✅ Namespace isolation prevents cross-pair contamination
  ✅ WAL mode enables safe concurrent access (LIVE + CALIBRATION)
"""

import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict

logger = logging.getLogger(__name__)

# Database file location
DB_PATH = Path(__file__).parent.parent.parent / "data" / "tte.db"


def get_connection() -> sqlite3.Connection:
    """
    Get a SQLite connection with WAL mode enabled.

    WAL (Write-Ahead Logging) allows concurrent readers + 1 writer,
    which is required for LIVE (reader) + CALIBRATION (writer) processes.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema() -> None:
    """
    Initialize database schema. Safe to call multiple times
    (uses IF NOT EXISTS).
    """
    conn = get_connection()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS score_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange    TEXT NOT NULL,
                pair        TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                score       REAL NOT NULL,
                candle_timestamp INTEGER,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_score_namespace
                ON score_history(exchange, pair, timeframe);

            CREATE TABLE IF NOT EXISTS snapshot_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange    TEXT NOT NULL,
                pair        TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                scope       TEXT NOT NULL,
                value       REAL NOT NULL,
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_namespace
                ON snapshot_history(exchange, pair, timeframe, scope);

            CREATE TABLE IF NOT EXISTS penalties (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange    TEXT NOT NULL,
                pair        TEXT NOT NULL,
                timeframe   TEXT NOT NULL,
                layer       TEXT NOT NULL,
                scope       TEXT NOT NULL,
                penalty     REAL NOT NULL,
                model       TEXT NOT NULL DEFAULT 'rbf',
                updated_at  TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_penalty_namespace
                ON penalties(exchange, pair, timeframe, layer, scope);

            CREATE TABLE IF NOT EXISTS signal_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange        TEXT NOT NULL,
                pair            TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                signal_type     TEXT NOT NULL,
                entry_price     REAL NOT NULL,
                stop_loss       REAL,
                take_profit     REAL,
                leverage        REAL,
                score           REAL,
                created_at      TEXT NOT NULL,
                outcome         TEXT NOT NULL DEFAULT 'PENDING',
                outcome_price   REAL,
                outcome_at      TEXT,
                pnl_percent     REAL
            );

            CREATE INDEX IF NOT EXISTS idx_signal_namespace
                ON signal_history(exchange, pair, timeframe, outcome);

            CREATE TABLE IF NOT EXISTS execution_adjustments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange        TEXT NOT NULL,
                pair            TEXT NOT NULL,
                timeframe       TEXT NOT NULL,
                scope           TEXT NOT NULL,
                adjustment      REAL NOT NULL,
                quality_score   REAL,
                sample_size     INTEGER NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_adjustment_namespace
                ON execution_adjustments(exchange, pair, timeframe, scope);
        """)
        
        # Column migration check for score_history
        cursor = conn.execute("PRAGMA table_info(score_history)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "candle_timestamp" not in columns:
            conn.execute("ALTER TABLE score_history ADD COLUMN candle_timestamp INTEGER")
            logger.info("Migrated score_history table: added candle_timestamp column")

        # Create unique index for score_history to prevent duplicates
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_score_candle_unique
                ON score_history(exchange, pair, timeframe, candle_timestamp)
                WHERE candle_timestamp IS NOT NULL
        """)

        conn.commit()
        logger.info(f"Database schema initialized at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize database schema: {e}")
        raise
    finally:
        conn.close()


# ============================================================================
# SCORE HISTORY
# ============================================================================

def append_score(
    exchange: str, pair: str, timeframe: str, score: float, candle_timestamp: Optional[int] = None
) -> None:
    """Append a Layer 2 score to history for a specific namespace."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO score_history (exchange, pair, timeframe, score, candle_timestamp, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (exchange, pair, timeframe, score, candle_timestamp,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        logger.info(
            f"Appended score {score:.4f} to "
            f"{exchange}/{pair}/{timeframe} (timestamp: {candle_timestamp})"
        )
    except Exception as e:
        logger.error(f"Failed to append score: {e}")
    finally:
        conn.close()


def get_score_history(
    exchange: str, pair: str, timeframe: str
) -> List[float]:
    """
    Get score history as list of floats (oldest → newest).

    Returns only scores for the specified namespace.
    No cross-pair contamination.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT score FROM score_history
               WHERE exchange = ? AND pair = ? AND timeframe = ?
               ORDER BY created_at ASC""",
            (exchange, pair, timeframe)
        )
        scores = [row["score"] for row in cursor.fetchall()]
        logger.debug(
            f"Loaded {len(scores)} scores for "
            f"{exchange}/{pair}/{timeframe}"
        )
        return scores
    except Exception as e:
        logger.error(f"Failed to load score history: {e}")
        return []
    finally:
        conn.close()


def get_score_count(
    exchange: str, pair: str, timeframe: str
) -> int:
    """Get number of scores stored for a namespace."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT COUNT(*) as cnt FROM score_history
               WHERE exchange = ? AND pair = ? AND timeframe = ?""",
            (exchange, pair, timeframe)
        )
        return cursor.fetchone()["cnt"]
    except Exception as e:
        logger.error(f"Failed to count scores: {e}")
        return 0
    finally:
        conn.close()


# ============================================================================
# SNAPSHOT HISTORY
# ============================================================================

def append_snapshot(
    exchange: str, pair: str, timeframe: str,
    scope: str, value: float
) -> None:
    """Append a snapshot value for a specific scope and namespace."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO snapshot_history
               (exchange, pair, timeframe, scope, value, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (exchange, pair, timeframe, scope, value,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        logger.debug(
            f"Appended {scope} snapshot: {value:.6f} "
            f"({exchange}/{pair}/{timeframe})"
        )
    except Exception as e:
        logger.error(f"Failed to append snapshot for {scope}: {e}")
    finally:
        conn.close()


def get_snapshot_history(
    exchange: str, pair: str, timeframe: str, scope: str
) -> List[float]:
    """
    Get snapshot history for a scope and namespace (oldest → newest).

    Temporal integrity preserved: no deduplication.
    Consecutive identical values carry temporal information (how long
    a value persisted) that PELT needs for accurate regime detection.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT value FROM snapshot_history
               WHERE exchange = ? AND pair = ? AND timeframe = ? AND scope = ?
               ORDER BY created_at ASC""",
            (exchange, pair, timeframe, scope)
        )
        raw_values = [row["value"] for row in cursor.fetchall()]

        # No deduplication — PELT requires equi-spaced temporal data.
        # Removing consecutive duplicates distorts time axis:
        #   [100,100,100,150,200] → [100,150,200]
        #   PELT sees 3 rapid changes instead of 5 gradual ones.
        logger.debug(
            f"Loaded {len(raw_values)} snapshot values for "
            f"{scope} ({exchange}/{pair}/{timeframe})"
        )

        return raw_values
    except Exception as e:
        logger.error(f"Failed to load snapshot history for {scope}: {e}")
        return []
    finally:
        conn.close()


# ============================================================================
# PENALTIES
# ============================================================================

def save_penalty(
    exchange: str, pair: str, timeframe: str,
    layer: str, scope: str,
    penalty: float, model: str = "rbf"
) -> None:
    """
    Save or update a penalty for a specific namespace + layer + scope.

    Uses INSERT OR REPLACE — only one active penalty per namespace.
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO penalties
               (exchange, pair, timeframe, layer, scope, penalty, model, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (exchange, pair, timeframe, layer, scope, penalty, model,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        logger.debug(
            f"Saved penalty {layer}/{scope}: {penalty:.6f} "
            f"(model={model}, {exchange}/{pair}/{timeframe})"
        )
    except Exception as e:
        logger.error(f"Failed to save penalty: {e}")
    finally:
        conn.close()


def get_penalty(
    exchange: str, pair: str, timeframe: str,
    layer: str, scope: str
) -> Optional[Dict]:
    """
    Get penalty for a specific namespace + layer + scope.

    Returns dict with 'penalty' and 'model', or None if not found.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT penalty, model, updated_at FROM penalties
               WHERE exchange = ? AND pair = ? AND timeframe = ?
               AND layer = ? AND scope = ?""",
            (exchange, pair, timeframe, layer, scope)
        )
        row = cursor.fetchone()
        if row:
            return {
                "penalty": row["penalty"],
                "model": row["model"],
                "updated_at": row["updated_at"],
            }
        return None
    except Exception as e:
        logger.error(f"Failed to load penalty: {e}")
        return None
    finally:
        conn.close()


def get_all_penalties(
    exchange: str, pair: str, timeframe: str, layer: str
) -> Dict[str, Dict]:
    """
    Get all penalties for a namespace + layer.

    Returns dict: {scope_name: {"penalty": float, "model": str}}
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT scope, penalty, model FROM penalties
               WHERE exchange = ? AND pair = ? AND timeframe = ?
               AND layer = ?""",
            (exchange, pair, timeframe, layer)
        )
        result = {}
        for row in cursor.fetchall():
            result[row["scope"]] = {
                "penalty": row["penalty"],
                "model": row["model"],
            }
        return result
    except Exception as e:
        logger.error(f"Failed to load penalties: {e}")
        return {}
    finally:
        conn.close()


def save_all_penalties(
    exchange: str, pair: str, timeframe: str,
    layer: str,
    penalties: Dict[str, float],
    models: Dict[str, str]
) -> None:
    """
    Save multiple penalties at once for a namespace + layer.

    Args:
        penalties: {scope_name: penalty_value}
        models: {scope_name: model_name}
    """
    for scope, penalty_value in penalties.items():
        model = models.get(scope, "rbf")
        save_penalty(exchange, pair, timeframe, layer, scope,
                     penalty_value, model)

    logger.info(
        f"Saved {len(penalties)} penalties for "
        f"{layer} ({exchange}/{pair}/{timeframe})"
    )


# ============================================================================
# SIGNAL HISTORY — Phase 1: Signal Logger
# ============================================================================

def log_signal(
    exchange: str, pair: str, timeframe: str,
    signal_type: str, entry_price: float,
    stop_loss: float | None = None, take_profit: float | None = None,
    leverage: float | None = None, score: float | None = None
) -> int:
    """
    Log a new trading signal to signal_history.

    Only log LONG/SHORT signals, never neutral ("0").
    Returns the signal_id for reference.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO signal_history
               (exchange, pair, timeframe, signal_type, entry_price,
                stop_loss, take_profit, leverage, score, created_at, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')""",
            (exchange, pair, timeframe, signal_type, entry_price,
             stop_loss, take_profit, leverage, score,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        signal_id = cursor.lastrowid or -1
        logger.info(
            f"Signal logged: {signal_type} {exchange}/{pair}/{timeframe} "
            f"@ {entry_price:.2f} (SL={stop_loss}, TP={take_profit}, "
            f"lev={leverage}, id={signal_id})"
        )
        return signal_id
    except Exception as e:
        logger.error(f"Failed to log signal: {e}")
        return -1
    finally:
        conn.close()


def get_pending_signals(
    exchange: str, pair: str, timeframe: str
) -> List[dict]:
    """
    Get all PENDING signals for a specific namespace.

    Returns list of dicts with signal details.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, signal_type, entry_price, stop_loss, take_profit,
                      leverage, score, created_at
               FROM signal_history
               WHERE exchange = ? AND pair = ? AND timeframe = ?
                 AND outcome = 'PENDING'
               ORDER BY created_at ASC""",
            (exchange, pair, timeframe)
        ).fetchall()

        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get pending signals: {e}")
        return []
    finally:
        conn.close()


def update_signal_outcome(
    signal_id: int, outcome: str,
    outcome_price: float, pnl_percent: float
) -> None:
    """
    Update a signal's outcome after evaluation.

    Args:
        signal_id: ID of the signal to update
        outcome: 'TP_HIT', 'SL_HIT', 'EXPIRED', or 'REVERSED'
        outcome_price: Price when outcome was determined
        pnl_percent: Profit/loss in percentage (including leverage)
    """
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE signal_history
               SET outcome = ?, outcome_price = ?,
                   outcome_at = ?, pnl_percent = ?
               WHERE id = ?""",
            (outcome, outcome_price,
             datetime.now(timezone.utc).isoformat(),
             pnl_percent, signal_id)
        )
        conn.commit()
        logger.info(
            f"Signal #{signal_id} outcome: {outcome} "
            f"@ {outcome_price:.2f} (P&L: {pnl_percent:+.2f}%)"
        )
    except Exception as e:
        logger.error(f"Failed to update signal outcome: {e}")
    finally:
        conn.close()


# ============================================================================
# EXECUTION ADJUSTMENTS — Phase 3: Category B Calibration
# ============================================================================

def save_adjustment(
    exchange: str, pair: str, timeframe: str,
    scope: str, adjustment: float,
    quality_score: float, sample_size: int
) -> None:
    """
    Save or update a Category B adjustment for a namespace + scope.

    Uses INSERT OR REPLACE — only one active adjustment per namespace + scope.

    Args:
        scope: One of 'sl_quality', 'tp_quality', 'leverage_quality',
               'signal_confidence'
        adjustment: Multiplier (1.0 = no change) or confidence score (0-1)
        quality_score: Reliability estimate of this adjustment
        sample_size: Number of resolved signals used to compute this
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO execution_adjustments
               (exchange, pair, timeframe, scope, adjustment,
                quality_score, sample_size, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (exchange, pair, timeframe, scope, adjustment,
             quality_score, sample_size,
             datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        logger.debug(
            f"Saved adjustment {scope}: {adjustment:.4f} "
            f"(quality={quality_score:.3f}, n={sample_size}, "
            f"{exchange}/{pair}/{timeframe})"
        )
    except Exception as e:
        logger.error(f"Failed to save adjustment: {e}")
    finally:
        conn.close()


def get_adjustment(
    exchange: str, pair: str, timeframe: str, scope: str
) -> Optional[Dict]:
    """
    Get adjustment for a specific namespace + scope.

    Returns dict with 'adjustment', 'quality_score', 'sample_size',
    or None if not found.
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT adjustment, quality_score, sample_size, updated_at
               FROM execution_adjustments
               WHERE exchange = ? AND pair = ? AND timeframe = ?
               AND scope = ?""",
            (exchange, pair, timeframe, scope)
        )
        row = cursor.fetchone()
        if row:
            return {
                "adjustment": row["adjustment"],
                "quality_score": row["quality_score"],
                "sample_size": row["sample_size"],
                "updated_at": row["updated_at"],
            }
        return None
    except Exception as e:
        logger.error(f"Failed to load adjustment: {e}")
        return None
    finally:
        conn.close()


def get_all_adjustments(
    exchange: str, pair: str, timeframe: str
) -> Dict[str, Dict]:
    """
    Get all Category B adjustments for a namespace.

    Returns dict: {scope_name: {"adjustment": float, "quality_score": float,
                                "sample_size": int}}
    """
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT scope, adjustment, quality_score, sample_size
               FROM execution_adjustments
               WHERE exchange = ? AND pair = ? AND timeframe = ?""",
            (exchange, pair, timeframe)
        )
        result = {}
        for row in cursor.fetchall():
            result[row["scope"]] = {
                "adjustment": row["adjustment"],
                "quality_score": row["quality_score"],
                "sample_size": row["sample_size"],
            }
        return result
    except Exception as e:
        logger.error(f"Failed to load adjustments: {e}")
        return {}
    finally:
        conn.close()


def get_resolved_signals(
    exchange: str, pair: str, timeframe: str
) -> List[Dict]:
    """
    Get all resolved signals (TP_HIT or SL_HIT) for a namespace.

    Used by outcome_evaluator.py to compute Category B adjustments.
    Returns list of dicts with signal details and outcomes.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, signal_type, entry_price, stop_loss, take_profit,
                      leverage, score, created_at, outcome, outcome_price,
                      outcome_at, pnl_percent
               FROM signal_history
               WHERE exchange = ? AND pair = ? AND timeframe = ?
                 AND outcome IN ('TP_HIT', 'SL_HIT')
               ORDER BY outcome_at ASC""",
            (exchange, pair, timeframe)
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"Failed to get resolved signals: {e}")
        return []
    finally:
        conn.close()


def get_resolved_signal_count(
    exchange: str, pair: str, timeframe: str
) -> int:
    """Count resolved signals (TP_HIT or SL_HIT) for a namespace."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """SELECT COUNT(*) as cnt FROM signal_history
               WHERE exchange = ? AND pair = ? AND timeframe = ?
                 AND outcome IN ('TP_HIT', 'SL_HIT')""",
            (exchange, pair, timeframe)
        )
        return cursor.fetchone()["cnt"]
    except Exception as e:
        logger.error(f"Failed to count resolved signals: {e}")
        return 0
    finally:
        conn.close()


# ============================================================================
# INITIALIZATION — Auto-create schema on first import
# ============================================================================

# Initialize schema when module is first imported
try:
    init_schema()
except Exception as e:
    logger.warning(f"Database auto-init failed (will retry on first use): {e}")
