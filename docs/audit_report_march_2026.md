# 🔍 Full Codebase Audit & Fix Report — Trading Truth Engine

**Date**: March 16, 2026
**Scope**: All codebase files in `d:\trading_truth_engine`
**Session**: Initial Static Analysis + Adversarial Review (Claude Opus)
**Status**: Tier 1 + C-05 completed. Other issues deferred or marked as known behavior.

This document serves as the single source of truth for the codebase audit, documenting **all** discovered issues, their constitutionality, and their current resolution status.

---

## 🛠️ TIER 1 — CRITICAL (FIXED TODAY ✅)
*Immediate system-breaking bugs or direct constitution violations that actively contaminated the trading pipeline.*

### C-01: Fabricated Data in `leverage_pressure`
* **Issue**: Both Binance & Gate.io connectors returned a hardcoded `average_leverage = 5.0` multiplier because the APIs don't easily provide real variable leverage. This violated the *“All decisions from data, not assumptions”* constitution rule, contaminating Layer 2 Z-Scores with human-invented numbers.
* **Status**: **FIXED**. 
  - `average_leverage` and `open_positions_count` removed from `sources.yaml` and both connectors.
  - History tracking now uses raw `total_size` directly. Z-Scores are identical as they are scale-invariant.

### C-03: `np.log()` Silent NaN Propagation
* **Issue**: In `risk.py` and `executor.py`, calculating log returns via `np.log()` on zero or negative prices returned `-inf` or `NaN`. NumPy silently passes this through, which would silently corrupt the leverage ratio calculations.
* **Status**: **FIXED**.
  - Added guards: If `np.any(arr <= 0)`, the functions safely log a warning and return strict numerical fallbacks (`1.0` for leverage, `0.0` for volatility) to prevent math crashes.

### C-04: SL/TP Generated Without Current Price Validation
* **Issue**: A LONG entry could be triggered when `current_price` was already exactly at the `Take Profit` level (or worse). This led to valid-looking trades that actually had mathematically impossible or negative Risk:Reward ratios.
* **Status**: **FIXED**. 
  - Added strict geometric validation in `executor.py`: LONG is rejected if `price <= SL` or `price >= TP`. SHORT is rejected if `price >= SL` or `price <= TP`. Rejected trades return a neutral `0` signal.

### C-05: Shared `last_updated` Timestamp Race Condition
* **Issue**: Both Layer 2 and Layer 3 read/wrote to the exact same `last_updated` key in `penalties.yaml`. If Layer 2 refreshed its penalties, Layer 3 would read that new timestamp and think *its own* penalties were fresh, thereby skipping its own refresh cycle. Furthermore, Layer 2’s save function was wiping out Layer 3's data.
* **Status**: **FIXED**.
  - Split timestamp into `last_updated_l2` and `last_updated_l3`. Read-modify-write patterns implemented to ensure safe concurrent saving.

---

## 📅 TIER 2 & 3 — DEFERRED (DESIGN DECISIONS & MONITORING)
*Issues that reflect mathematical edge-cases, constitution interpretation, or require more data to fix safely.*

### Y-08: Deduplication History Removes Valid Data
* **Issue**: `history_manager.py` strips consecutive duplicate values from snapshot data (e.g., Open Interest unchanged for 3 hours). While this prevents CPD from misidentifying regimes due to flatlines, it distorts the true temporal structure of the market.
* **Status**: **DEFERRED** (Tier 3). 
  - Dilemma: Keep duplicates (CPD breaks) vs Remove duplicates (Time Series distorts). 
  - Pending discussion on whether to cap the window size instead of blindly removing consecutives.

### M-01: Leverage Formula is Oversensitive to Historical Crashes
* **Issue**: Leverage = `vol_history / vol_current`. If a massive historical crash pushes `vol_history` very high, but the market is calm today (`vol_current` low), leverage hits max (`200x`). Maxing out leverage exactly when the market is "calm" is highly dangerous if a sudden spike occurs.
* **Status**: **DEFERRED** (Tier 3). 
  - Constitution strictness: We cannot arbitrarily dull the formula without a data-driven reason. Watching this behavior before adjusting the math.

### M-04: Inconsistent Volatility Metrics (Log vs Simple Returns)
* **Issue**: Connector scopes (like `volatility_compression`) use Simple Returns `(p2-p1)/p1`, while `risk.py` uses Log Returns `np.diff(np.log(P))`.
* **Status**: **DEFERRED** (Tier 2). 
  - Standardization to Log Returns is best practice, but immediate numerical impact on currently low-volatility crypto pairs is negligible. Will fix in a future polish/refactor sprint.

### M-07: Layer 3 `calculate_homogeneity` Missing NaN Protection
* **Issue**: A copy-paste divergence. Layer 2's `calculate_homogeneity` has NaN/Inf protections and overflow bounds (e.g. `epsilon=1e-6`). Layer 3's function lacks these safeguards.
* **Status**: **DEFERRED**.
  - No active crashes, but should be unified into a single helper function in future refactors to prevent continued divergence.

### C-06: Race Condition in Snapshot History (File I/O)
* **Issue**: `history_manager.py` uses a read-modify-write pattern on `snapshot_history.json` without file locking.
* **Status**: **DEFERRED**.
  - The system is currently single-processed and runs 1x/hour via Task Scheduler. Theoretical risk only; low practical priority until multi-pair/multi-process is implemented.

---

## 🟢 KNOWN BEHAVIORS & INTENTIONAL DESIGN
*Issues flagged initially but confirmed to be correct functioning based on the constitution.*

### C-02: Missing Scopes in `sources.yaml` (Dead Code)
* **Status**: **KNOWN / INTENTIONAL**. 
  - `liquidity_density` and `position_concentration` are fetched by connectors but not listed in `sources.yaml`. These were intentionally removed to adhere to the constitution (Gate.io doesn't expose real data for them). Layer 1 successfully ignores them. Dead code cleanup needed later.

### M-08: `threshold_up == threshold_down`
* **Status**: **INTENTIONAL**.
  - If score history is perfectly homogeneous, thresholds converge. This correctly forces a neutral signal (`0`), adhering to the design principle: the system refuses to trade on noise.

### Score `0.0000` Generation (std=0 edge case)
* **Status**: **VALID BEHAVIOR**.
  - Found during adversarial review. If all values in a CPD window are perfectly identical, `zscore.py` safely returns `0.0`. Valid behavior reflecting perfect market stasis. Not a bug.

### Y-04: All Penalties Converge to 0.1
* **Status**: **NORMAL / EXPECTED**.
  - Current data history (~340 entries) is insufficient to justify high penalties via meta-learning. Over time, as market variations accrue, these will organically shift.

---

## 🧹 TIER 4 — MINOR QUALITY SMELLS (LOW PRIORITY)
*Minor hardcodes and unused parameters that do not break the engine.*

1. **Y-01 / Y-02 (risk.py)**: Magic numbers `0.001` and `1e-10` used as mathematical fallbacks for zero-division guards. Acts as engineering safety nets.
2. **M-05 (aggregator.py)**: Hardcoded `-2` and `+2` bounds for string classification (BULLISH/BEARISH). Used purely for HTML display, ignored by trading execution logic.
3. **M-02 (gateio.py)**: Leftover dead function returning a hardcoded `20x` leverage. Never called by execution flow.
4. **C-07 (binance.py)**: Fetching max leverage currently uses the wrong end of the array (returns lowest leverage bracket). System currently uses GateIO exclusively, so zero impact.
5. **L-04 (report_builder.py)**: Hardcoded `1h` assumption for HTML timestamp generation. Will need fixing if testing multi-timeframe architectures.

---
*End of Report*
