# TRADING TRUTH ENGINE - COMPLETE SYSTEM ARCHITECTURE DOCUMENTATION

**Generated:** February 24, 2026  
**Status:** System Analysis & Compliance Review  
**Document Version:** 1.0

---

## TABLE OF CONTENTS

1. [Executive Summary](#executive-summary)
2. [System Philosophy](#system-philosophy)
3. [Layer 1: The Existence Oracle](#layer-1-the-existence-oracle)
4. [Layer 2: The Structural Analyzer](#layer-2-the-structural-analyzer)
5. [Layer 3: The Statistical Executioner](#layer-3-the-statistical-executioner)
6. [Compliance Status](#compliance-status)
7. [Critical Issues & Violations](#critical-issues--violations)
8. [File Structure & Modules](#file-structure--modules)
9. [API References](#api-references)

---

## EXECUTIVE SUMMARY

### What is Trading Truth Engine?

A **zero-assumption trading system** designed on the principle: "Data is the only judge."

The system consists of three isolated layers that transform raw market data into trading signals:
- **Layer 1**: Verifies data existence (0/1)
- **Layer 2**: Transforms multi-source data into unified score
- **Layer 3**: Converts score into trading decisions (entry, SL, TP, leverage)

### Key Design Principle

> **Manusia hanya berperan sebagai penyala pertama.** Setelah sistem menyala, data yang pegang kendali.
> 
> *"Humans are only the first ignition. After the system starts, data controls everything."*

### Constitutional Rules

1. Layer 1 must NOT contain any numeric thresholds
2. Layer 2 must NOT contain manual weights or hardcoded windows
3. Layer 3 must NOT contain fixed percentiles, multipliers, or hardcoded leverage
4. Meta-learning evaluates structural homogeneity, NOT profit
5. Every number in code must answer: "Where does this number come from?"

---

## SYSTEM PHILOSOPHY

### Core Principles

1. **Data-Driven**: Every parameter derived from data structure, not human judgment
2. **Layer Isolation**: Lower layers don't know upper layer objectives
3. **Transparency**: All configuration explicit in config files (YAML)
4. **No Hidden Assumptions**: Every threshold, penalty, limit traceable to source
5. **Self-Similar Architecture**: Layer 2 and Layer 3 use same CPD + z-score principles

### Separation of Concerns

```
Layer 1: Existence Check
    ↓ (outputs 0/1 per scope)
Layer 2: Score Generation
    ↓ (outputs single float score)
Layer 3: Trading Decisions
    ↓ (outputs LONG/SHORT/0 + SL, TP, leverage)
```

**Each layer is completely isolated:**
- Layer 1 doesn't know Layer 2 cares about scores
- Layer 2 doesn't know Layer 3 will use score for trading
- Layer 3 only reads score + price history (not raw data)

---

## LAYER 1: THE EXISTENCE ORACLE

### Purpose
Verify whether data from a source truly exists and is structurally valid.

### Input
- Raw data from any API (exchange, on-chain, macro, etc.)
- Scope name (ohlcv, funding_rate, open_interest, etc.)
- Timeframe, market type

### Output
- Binary: 0 (data does not exist) or 1 (data exists and is valid)

### Philosophy
- NO interpretation of meaning
- NO threshold comparisons
- NO content filtering
- ONLY: exists or not exists

### Implementation

**File:** `src/layer1/verifier.py`

```python
class Verifier:
    def verify_all(connector, pair, timeframe, market_type) → Dict[str, int]
    def _check_existence(scope_name, data, timeframe) → int
    def _check_required_fields(scope_name, data) → bool
    def _check_parseability(scope_name, data) → bool
    def _check_timestamp_freshness(scope_name, data, timeframe) → bool
    def _check_structural_rules(scope_name, data) → bool
```

**Multi-Step Existence Check:**

1. **Raw Existence Check** (exactly per blueprint)
   ```python
   if data is None: return 0
   if data == "": return 0
   if data == []: return 0
   if isinstance(data, dict) and len(data) == 0: return 0
   # numeric 0 is considered as data existing
   return 1
   ```

2. **Required Fields Check**
   - Verify all required_fields (per scope config) are present
   - All values must be non-null
   - Source: `config/sources.yaml`

3. **Parseability Check**
   - All numeric fields must be convertible to float
   - Format validation only (not value validation)

4. **Timestamp Freshness Check**
   - Data must be within `max_staleness_intervals` of exchange update frequency
   - Update interval: 
     - `"dynamic"` = follows user's timeframe (OHLCV, OI)
     - Fixed integer = exchange API spec (funding_rate = 28800s/8h)
   - Max staleness: `config/infrastructure.yaml` → `max_staleness_intervals: 2`
   - **Note:** This is INFRASTRUCTURE parameter, not data judgment

5. **Structural Rules Check**
   - **Definitional checks** (not judgmental):
     - Volume ≥ 0 (mathematically impossible to be negative)
     - High ≥ Low (OHLCV definition)
   - Per-scope rules in `STRUCTURAL_RULES` dict

**Constitution Compliance:** ✅ FULL COMPLIANCE
- ✅ No thresholds in logic
- ✅ No historical comparisons
- ✅ No content filters
- ✅ Format-based only, not meaning-based
- ✅ Completely source-agnostic

### Configuration Sources

**File:** `config/sources.yaml`

```yaml
scopes:
  ohlcv:
    update_interval_seconds: dynamic
    required_fields: [open, high, low, close, volume, timestamp]
  funding_rate:
    update_interval_seconds: 28800  # 8h from exchange spec
    required_fields: [rate, timestamp]
  open_interest:
    update_interval_seconds: dynamic
    required_fields: [value, timestamp]
```

**File:** `config/infrastructure.yaml`

```yaml
retry:
  max_attempts: 3
  delay_seconds: 1
request:
  timeout_seconds: 10
timestamp:
  max_staleness_intervals: 2  # 2x the update interval
```

### Exchange Connectors

**Base:** `src/layer1/connectors/base.py`
- Abstract interface for all exchange connectors
- Retry logic (max_attempts from config)
- HTTP request handling with timeout

**Implementations:**
- `src/layer1/connectors/binance.py` - Binance USDⓈ-M perpetuals
- `src/layer1/connectors/gateio.py` - Gate.io perpetuals

**Easy to extend:** Add new exchange = subclass BaseConnector + implement abstract methods

### Edge Cases & Fallback Behavior

- **Empty data:** Returns 0 (data doesn't exist)
- **Null fields:** Returns 0 (incomplete structure)
- **Unparseable values:** Returns 0 (structural fail)
- **Stale timestamp:** Returns 0 (technical infrastructure constraint)
- **Network error:** Retries per config, then returns None (connector returns None)

---

## LAYER 2: THE STRUCTURAL ANALYZER

### Purpose
Transform all data that passed Layer 1 into a single unified score using Change Point Detection + z-score analysis.

### Input
- Layer 1 existence results: `{scope_name: 0 or 1}`
- Time-series data for each active scope
- CPD penalties from meta-learning
- Exchange connector

### Output
- Single real-valued score (can be positive/negative)
- Metadata with active scopes, z-scores per scope, penalties used

### Philosophy
- **CPD determines window** (not human)
- **Z-score contains direction & magnitude** (no manual weights)
- **Aggregation via sum** (pure mathematical, no subjective weighting)
- **Single penalty per scope** (from meta-learning via homogeneity metric)

### Implementation

**Main Orchestrator:** `src/layer2/aggregator.py`

```python
def layer2(
    connector,
    layer1_results: Dict[str, int],
    pair: str,
    timeframe: str,
    market_type: str,
    penalty_manager: PenaltyManager,
    lookback_periods: int = 50,
) → Tuple[float, Dict]
```

**Flow:**

1. **Fetch active scopes** (those where Layer 1 = 1)
2. **For each active scope:**
   - Fetch historical data (via connector)
   - Get penalty from PenaltyManager
   - Apply CPD to find recent regime
   - Calculate z-score within that regime
3. **Sum all z-scores** (no weights)
4. **Return total_score + metadata**

### Stage 2.1: Change Point Detection (CPD)

**File:** `src/layer2/cpd.py`

```python
def get_cpd_window(series: List[float], penalty: float) → List[float]
```

**Algorithm:**
1. Apply PELT algorithm with RBF kernel (domain-invariant)
2. Predict change points using given penalty
3. Return segment from last change point to present (most recent regime)

**Why PELT + RBF?**
- PELT: Pruned Exact Linear Time (efficient)
- RBF: Radial Basis Function (flexible, domain-invariant)
- No manual window size from humans
- Window determined by data structure

**Edge cases:**
- Series < 3 points: return full series (defense, not logic)
- All returns logged for monitoring

### Stage 2.2: Z-Score Calculation

**File:** `src/layer2/zscore.py`

```python
def zscore_in_window(
    series: List[float],
    current_value: float,
    epsilon: float = 1e-9
) → float
```

**Formula:**
```
z = (current_value - μ) / σ
```

**Key Points:**
- Calculation within CPD window (not fixed 30-day or 50-day)
- Mean & std from recent regime only
- Contains both direction (+/-) and magnitude (distance from mean)
- No need for manual weights

**Edge cases:**
- Empty series: return 0 (no reference)
- σ = 0 (no volatility): return 0 (no signal)
- epsilon = 1e-9 (numerical stability)

### Stage 2.3: Aggregation

**File:** `src/layer2/aggregator.py` (aggregation logic)

```python
total_score = 0.0
for scope_name in active_scopes:
    window = get_cpd_window(data_history, penalty)
    z = zscore_in_window(window, current_value)
    total_score += z
return total_score
```

**Why simple sum?**
- Z-scores already contain magnitude (weighting)
- No manual weights needed
- Mathematically pure
- Each scope's contribution scaled by its own volatility

### Meta-Learning for CPD Penalties

**File:** `src/layer2/meta_learning.py`

```python
def find_optimal_penalty(
    series: List[float],
    penalty_candidates: List[float] = None
) → Tuple[float, float]
```

**Homogeneity Metric:**
```
quality = between_segment_variance / within_segment_variance
```

**Logic:**
- Higher ratio = cleaner segmentation
- Between-variance: segments are different from each other
- Within-variance: segments are internally consistent
- Purely STRUCTURAL (not profit-based)

**Penalty Candidates:**
```python
DEFAULT_PENALTY_CANDIDATES = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
```

**Process:**
1. For each penalty candidate:
   - Run PELT with that penalty
   - Calculate homogeneity for resulting segments
2. Select penalty with highest homogeneity
3. Save to `config/penalties.yaml`

### Penalty Management

**File:** `src/layer2/penalty_manager.py`

```python
class PenaltyManager:
    def __init__()
    def get_penalty(scope_name: str) → float
    def refresh_penalties(new_penalties: Dict[str, float]) → None
    def should_refresh() → bool
    def force_refresh_if_needed(connector, pair, timeframe, market_type) → bool
```

**Lifecycle:**
1. **First run:** Meta-learning calculates penalties → saved to `config/penalties.yaml`
2. **Subsequent runs:** Load cached penalties
3. **Auto-refresh:** If penalties > 7 days old, refresh via meta-learning
4. **Manual override:** Caller can force refresh

**File:** `config/penalties.yaml`

```yaml
penalties:
  ohlcv: 1.0
  funding_rate: 2.1
  open_interest: 0.5
last_updated: 1708952345
```

### Constitution Compliance: ✅ FULL COMPLIANCE

| Requirement | Implementation | Status |
|---|---|---|
| No manual weights | Z-scores contain magnitude | ✅ |
| No hardcoded windows | CPD determines window | ✅ |
| No hardcoded thresholds | Penalties from meta-learning | ✅ |
| No profit metrics | Homogeneity metric only | ✅ |
| Configuration explicit | penalties.yaml + sources.yaml | ✅ |
| No hidden numbers | All explained & traceable | ✅ |

---

## LAYER 3: THE STATISTICAL EXECUTIONER

### Purpose
Convert Layer 2 score into actionable trading signals: entry (LONG/SHORT/0), stop loss, take profit, leverage.

### Input
- Current score from Layer 2
- Score history time-series
- Current price
- Price history time-series
- CPD penalty for score distribution
- CPD penalty for price regime
- Exchange leverage maximum (from API)

### Output
```python
{
    "entry": "LONG" | "SHORT" | "0",
    "entry_price": float,
    "stop_loss": float or None,
    "take_profit": float or None,
    "leverage": float,
    "risk_reward_ratio": float or None,
    "metadata": {
        "threshold_up": float,
        "threshold_down": float,
        "score": float,
        "score_z": float,
        "price": float,
        "regime_volatility": float
    }
}
```

### Philosophy
- **Thresholds from CPD** (not fixed percentiles like 90%, 10%)
- **SL/TP from natural price range** (not ATR multipliers)
- **Leverage from volatility ratio** (not hardcoded leverage_max)
- **Layer isolation** (only reads score, not raw data)
- **All parameters data-driven** (or from meta-learning / API)

### Implementation

**Main Orchestrator:** `src/layer3/executor.py`

```python
def layer3(
    current_score: float,
    score_history: List[float],
    current_price: float,
    price_history: List[float],
    penalty_score: float,
    penalty_price: float,
    exchange_leverage_max: float,
) → Dict[str, Any]
```

### Stage 3.1: Threshold Derivation

**File:** `src/layer3/thresholds.py`

```python
def get_score_thresholds(
    score_history: List[float],
    penalty: float
) → Tuple[float, float]
```

**Algorithm:**
1. Apply PELT to score_history with given penalty
2. Extract most recent regime (segment from last change point to end)
3. Calculate mean & std of that regime
4. Thresholds = mean ± 1σ

**Code:**
```python
algo = rpt.Pelt(model="rbf", min_size=1, jump=1).fit(data)
change_points = algo.predict(pen=penalty)
last_cp = change_points[-2] if len(change_points) > 1 else 0
recent_scores = score_history[last_cp:]
threshold_up = np.mean(recent_scores) + np.std(recent_scores)
threshold_down = np.mean(recent_scores) - np.std(recent_scores)
```

**Why mean ± 1σ?**
- Natural statistical bounds from data
- Adapts to current score distribution regime
- No manual percentiles (90%, 80%, etc.)

**Edge case fallback:**
```python
if not score_history or len(score_history) < 3:
    return 1.0, -1.0  # ⚠️ HARDCODED - SEE ISSUES
```

### Stage 3.2: Entry Decision

**File:** `src/layer3/executor.py`

```python
def decide_entry(
    score: float,
    threshold_up: float,
    threshold_down: float
) → str
```

**Logic (exact from blueprint):**
```python
if score > threshold_up:
    return "LONG"
elif score < threshold_down:
    return "SHORT"
else:
    return "0"
```

**Purely binary:**
- No hysteresis
- No additional filters
- No confirmation requirements

### Stage 3.3: Stop Loss & Take Profit

**File:** `src/layer3/risk.py`

```python
def get_sl_tp(
    price_history: List[float],
    penalty: float,
    direction: str
) → Tuple[Optional[float], Optional[float]]
```

**Algorithm:**
1. Get recent price regime via CPD (same penalty as Layer 3.1)
2. Find natural_high and natural_low in that regime
3. For LONG: sl = natural_low, tp = natural_high
4. For SHORT: sl = natural_high, tp = natural_low

**Code:**
```python
window = get_cpd_window(price_history, penalty)
natural_high = max(window)
natural_low = min(window)

if direction == "LONG":
    sl = natural_low
    tp = natural_high
```

**Why natural range?**
- Price has already tested these bounds in recent regime
- No arbitrary risk:reward multipliers
- Support/resistance from price structure itself
- No ATR × 2, ATR × 4, etc.

### Stage 3.4: Leverage Determination

**File:** `src/layer3/risk.py`

```python
def get_leverage(
    price_history: List[float],
    penalty: float,
    exchange_max: float
) → float
```

**Algorithm:**
1. Get current regime via CPD
2. Calculate volatility in current regime: vol_current
3. Calculate volatility in full history: vol_history
4. leverage = vol_history / vol_current
5. Cap at 1.0 (floor) and exchange_max (ceiling)

**Code:**
```python
window = get_cpd_window(price_history, penalty)
window_returns = np.diff(window) / window[:-1]
vol_current = np.std(window_returns)

history_returns = np.diff(price_history) / price_history[:-1]
vol_history = np.std(history_returns)

leverage = vol_history / vol_current
leverage_capped = np.clip(leverage, 1.0, exchange_max)
```

**Why inverse volatility relationship?**
- Low volatility now = high confidence = increase leverage
- High volatility now = low confidence = reduce leverage
- All scaling from data, not human judgment
- Exchange limit (from API) is only hard boundary

**Edge cases:**
```python
if vol_current == 0 or vol_current < 1e-10:
    return 1.0  # Safety fallback to 1x
```

### Layer Isolation: ✅ CONFIRMED

**Layer 3 reads ONLY:**
- ✅ Aggregated score from Layer 2 (not components)
- ✅ Score history (for threshold derivation)
- ✅ Price history (for regime analysis)
- ✅ Meta-learning parameters (penalties)
- ✅ Exchange API limits

**Layer 3 does NOT read:**
- ❌ Volume data
- ❌ Funding rate
- ❌ Open interest
- ❌ OHLCV candles
- ❌ Any raw Layer 1 data

### Constitution Compliance: ⚠️ PARTIAL - SEE ISSUES

---

## COMPLIANCE STATUS

### Layer 1: ✅ FULL COMPLIANCE

| Constitutional Rule | Implementation | Status|
|---|---|---|
| No numeric thresholds | Infrastructure only (max_staleness_intervals) | ✅ |
| No historical comparisons | Format checks only | ✅ |
| No content filters | Definitional checks only | ✅ |
| Layer isolation | Completely source-agnostic | ✅ |
| Transparency | All config in YAML | ✅ |

**Verdict:** Layer 1 is perfectly aligned with blueprint.

### Layer 2: ✅ FULL COMPLIANCE

| Constitutional Rule | Implementation | Status |
|---|---|---|
| No manual weights | Z-scores contain magnitude | ✅ |
| No hardcoded windows | CPD determines | ✅ |
| No hardcoded thresholds | Penalties from meta-learning | ✅ |
| Meta-learning on homogeneity | NOT profit | ✅ |
| Configuration explicit | penalties.yaml + sources.yaml | ✅ |
| No hidden numbers | All traceable | ✅ |

**Verdict:** Layer 2 is perfectly aligned with blueprint.

### Layer 3: ⚠️ PARTIAL COMPLIANCE - CRITICAL ISSUES

✅ **Compliant Elements:**
- Entry decision logic (pure binary)
- SL/TP from natural price range
- Leverage from volatility ratio
- Layer isolation (only reads score)
- No hardcoded leverage in code (uses exchange_max)

❌ **Non-Compliant Elements:**
- Missing meta-learning for penalty_score
- Missing meta-learning for penalty_price
- Hardcoded fallback thresholds (1.0, -1.0)

---

## CRITICAL ISSUES & VIOLATIONS

### 🔴 ISSUE #1: Missing Meta-Learning for `penalty_score`

**Location:** `src/layer3/executor.py`, `src/layer3/thresholds.py`

**Problem:**
- Blueprint Stage 3.1 states: "penalty adalah satu-satunya parameter — dipilih via meta-learning"
- Implementation only receives `penalty_score` as parameter
- **NO meta-learning mechanism** to determine optimal penalty_score
- Parameter passed from caller without validation or selection process

**Current Code:**
```python
def layer3(..., penalty_score: float, ...):
    # ...
    threshold_up, threshold_down = get_score_thresholds(
        score_history, penalty_score  # ← Just a parameter!
    )
```

**Where does penalty_score come from?**
- ✗ Not from PenaltyManager (only manages Layer 2 scope penalties)
- ✗ No `meta_learn_score_penalty()` function exists
- ✗ Hardcoded in tests as `1.0`
- ✗ Passed as unvalidated parameter in production

**Violation:** Constitutional Rule #4: "Each number must answer: where does it come from?"

**Required Fix:**
Create `meta_learn_score_penalty()` function that:
1. Takes score_history time-series
2. Evaluates CPD homogeneity for different penalty candidates
3. Selects penalty with best segmentation
4. Caches result similarly to Layer 2 penalties

### 🔴 ISSUE #2: Missing Meta-Learning for `penalty_price`

**Location:** `src/layer3/risk.py` (both `get_sl_tp()` and `get_leverage()`)

**Problem:**
- Blueprint Stages 3.3 and 3.4 use `penalty` for CPD on price data
- Implementation only receives `penalty_price` as parameter
- **NO meta-learning mechanism** to determine optimal penalty_price
- Parameter passed without validation or selection

**Current Code:**
```python
def get_sl_tp(price_history, penalty, direction):  # ← penalty as parameter
def get_leverage(price_history, penalty, exchange_max):  # ← penalty as parameter
```

**Where does penalty_price come from?**
- ✗ Not from PenaltyManager
- ✗ No `meta_learn_price_penalty()` function
- ✗ Hardcoded in tests as `1.0`
- ✗ Unvalidated parameter

**Violation:** Constitutional Rule #4: "Zero hidden assumptions"

**Required Fix:**
Create `meta_learn_price_penalty()` function that:
1. Takes price_history time-series
2. Evaluates CPD homogeneity for different penalty candidates
3. Selects penalty with best price regime detection
4. Caches result

### 🔴 ISSUE #3: Hardcoded Fallback Thresholds

**Location:** `src/layer3/thresholds.py` line 95-96

**Problem:**
```python
if not score_history or len(score_history) < 3:
    logger.warning(f"Score history too short ({len(score_history)}), using ±1 as fallback")
    return 1.0, -1.0  # ⚠️ HARDCODED NUMBERS
```

**Issue:**
- Thresholds `1.0, -1.0` are hardcoded
- No meta-learning, no data derivation
- When CPD fails, system silently returns these magic numbers
- Violates: "Every number must be traceable"

**Consequence:**
- If score_history < 3 points, system uses arbitrary thresholds
- Threshold not data-driven
- Silent fallback behavior not fully transparent

**Required Fix:**
- Option 1: Data-driven fallback (never hardcode)
- Option 2: Propagate error up (require sufficient history)
- Option 3: Use running statistics with memory

---

## FILE STRUCTURE & MODULES

### Directory Tree

```
trading_truth_engine/
│
├── config/
│   ├── infrastructure.yaml        # System infrastructure (retry, timeout, etc.)
│   ├── penalties.yaml             # Cached CPD penalties from meta-learning
│   └── sources.yaml               # API scopes definition
│
├── src/
│   ├── layer1/
│   │   ├── __init__.py
│   │   ├── verifier.py            # Existence check logic
│   │   └── connectors/            # Exchange API implementations
│   │       ├── base.py
│   │       ├── binance.py
│   │       ├── gateio.py
│   │       └── __init__.py
│   │
│   ├── layer2/
│   │   ├── __init__.py
│   │   ├── aggregator.py          # layer2() main function
│   │   ├── cpd.py                 # Change Point Detection
│   │   ├── zscore.py              # Z-score calculation
│   │   ├── meta_learning.py       # Penalty optimization
│   │   ├── penalty_manager.py     # Penalty lifecycle management
│   │   └── __pycache__/
│   │
│   ├── layer3/
│   │   ├── __init__.py
│   │   ├── executor.py            # layer3() main function
│   │   ├── thresholds.py          # Score threshold derivation
│   │   ├── risk.py                # SL/TP & leverage calculation
│   │   └── __pycache__/
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── config_loader.py       # Load YAML configurations
│   │   └── __pycache__/
│   │
│   └── __init__.py
│
├── tests/
│   ├── test_layer1_to_layer2_integration.py
│   ├── test_layer2.py
│   ├── test_layer3.py
│   └── __pycache__/
│
├── requirements.txt
├── pyproject.toml
├── pyrightconfig.json
├── Blueprint_trading_truth_engine.md
├── Blueprint_supplement.md
├── Briefing_for_antigravity.md
└── SYSTEM_ARCHITECTURE_DOCUMENTATION.md (this file)
```

### Module Dependencies

```
Layer 1 (verifier.py)
├── connectors/ (Binance, Gate.io)
├── config_loader (for sources.yaml, infrastructure.yaml)
└── Standalone logic

Layer 2 (aggregator.py)
├── layer1/verifier (receives Layer 1 results)
├── cpd.py (get_cpd_window)
├── zscore.py (zscore_in_window)
├── meta_learning.py (find_optimal_penalty)
├── penalty_manager.py (PenaltyManager)
└── config_loader

Layer 3 (executor.py)
├── layer2/cpd.py (get_cpd_window)
├── thresholds.py (get_score_thresholds)
├── risk.py (get_sl_tp, get_leverage)
└── config_loader (for exchange limits)
```

---

## API REFERENCES

### Layer 1 API

#### Verifier Class

```python
class Verifier:
    def __init__(self):
        """Initialize with configuration from YAML files"""
    
    def verify_all(
        connector: BaseConnector,
        pair: str,
        timeframe: str,
        market_type: str
    ) -> Dict[str, int]:
        """
        Run existence check on all configured scopes.
        
        Returns:
            {scope_name: 0 or 1} for each scope
        """
    
    def get_active_scopes(results: Dict[str, int]) -> List[str]:
        """Return list of scope names that passed existence check"""
    
    def get_scope_count(results: Dict[str, int]) -> int:
        """Return count of active scopes"""
```

#### BaseConnector (Abstract)

```python
class BaseConnector(ABC):
    def fetch(
        scope: str,
        pair: str,
        timeframe: str,
        market_type: str
    ) -> Dict | None:
        """Fetch data for a scope, return normalized dict or None"""
    
    def validate_pair(pair: str, market_type: str) -> bool:
        """Check if pair exists on this exchange"""
    
    def validate_timeframe(timeframe: str) -> bool:
        """Check if timeframe supported"""
    
    def get_supported_timeframes() -> List[str]:
        """Return supported timeframe list"""
```

### Layer 2 API

#### layer2 Function

```python
def layer2(
    connector: BaseConnector,
    layer1_results: Dict[str, int],
    pair: str,
    timeframe: str,
    market_type: str,
    penalty_manager: PenaltyManager,
    lookback_periods: int = 50,
) -> Tuple[float, Dict[str, Any]]:
    """
    Transform multi-source data into aggregated score.
    
    Returns:
        (score: float, metadata: Dict)
        
    Metadata includes:
        - active_scopes: List of scopes that passed Layer 1
        - z_scores: Dict of {scope_name: z_score}
        - penalties_used: Dict of {scope_name: penalty}
        - aggregation_method: "sum"
    """
```

#### CPD Functions

```python
def get_cpd_window(
    series: List[float],
    penalty: float
) -> List[float]:
    """
    Return segment from last change point to present.
    Most recent regime in the time-series.
    """

def get_all_change_points(
    series: List[float],
    penalty: float
) -> List[int]:
    """Get all detected change points (for monitoring)"""
```

#### Z-Score Functions

```python
def zscore_in_window(
    series: List[float],
    current_value: float,
    epsilon: float = 1e-9
) -> float:
    """
    Calculate z-score of current_value within series window.
    Formula: (x - μ) / σ
    """
```

#### Meta-Learning Functions

```python
def find_optimal_penalty(
    series: List[float],
    penalty_candidates: Optional[List[float]] = None,
    min_size: int = 1
) -> Tuple[float, float]:
    """
    Find CPD penalty with best homogeneity.
    
    Returns:
        (best_penalty, best_homogeneity_score)
    """

def meta_learn_all_scopes(
    connector: BaseConnector,
    pair: str,
    timeframe: str,
    market_type: str,
    lookback_periods: int = 100,
    penalty_candidates: Optional[List[float]] = None
) -> Dict[str, float]:
    """
    Run meta-learning for all scopes.
    
    Returns:
        {scope_name: optimal_penalty}
    """
```

#### PenaltyManager Class

```python
class PenaltyManager:
    def __init__(self):
        """Load cached penalties from config/penalties.yaml"""
    
    def get_penalty(
        scope_name: str,
        default: float = 1.0
    ) -> float:
        """Get penalty for scope, or default if not found"""
    
    def get_all_penalties() -> Dict[str, float]:
        """Get all cached penalties"""
    
    def refresh_penalties(
        new_penalties: Dict[str, float]
    ) -> None:
        """Refresh with new penalties, save to YAML"""
    
    def should_refresh() -> bool:
        """Check if penalties > 7 days old"""
    
    def force_refresh_if_needed(
        connector: BaseConnector,
        pair: str,
        timeframe: str,
        market_type: str
    ) -> bool:
        """Auto-refresh if stale, return True if refreshed"""
```

### Layer 3 API

#### layer3 Function

```python
def layer3(
    current_score: float,
    score_history: List[float],
    current_price: float,
    price_history: List[float],
    penalty_score: float,
    penalty_price: float,
    exchange_leverage_max: float,
) -> Dict[str, Any]:
    """
    Convert score into complete trading signal.
    
    Returns:
        {
            "entry": "LONG" | "SHORT" | "0",
            "entry_price": float,
            "stop_loss": float or None,
            "take_profit": float or None,
            "leverage": float,
            "risk_reward_ratio": float or None,
            "metadata": {...}
        }
    """
```

#### Threshold Functions

```python
def get_score_thresholds(
    score_history: List[float],
    penalty: float
) -> Tuple[float, float]:
    """
    Derive thresholds from score distribution via CPD.
    
    Returns:
        (threshold_up, threshold_down)
    """
```

#### Risk Management Functions

```python
def get_sl_tp(
    price_history: List[float],
    penalty: float,
    direction: str
) -> Tuple[Optional[float], Optional[float]]:
    """
    Determine stop loss and take profit from natural price range.
    
    Returns:
        (stop_loss, take_profit) or (None, None) on error
    """

def get_leverage(
    price_history: List[float],
    penalty: float,
    exchange_max: float
) -> float:
    """
    Determine leverage from inverse volatility relationship.
    
    Returns:
        leverage value between 1.0 and exchange_max
    """
```

---

## CONFIGURATION FILES

### config/sources.yaml

Defines all data scopes and their requirements:

```yaml
scopes:
  ohlcv:
    description: "Candlestick OHLCV data"
    update_interval_seconds: dynamic      # Follows user's timeframe
    required_fields:
      - open
      - high
      - low
      - close
      - volume
      - timestamp
  
  funding_rate:
    description: "Perpetual futures funding rate"
    update_interval_seconds: 28800        # 8h (from exchange spec, not human judgment)
    required_fields:
      - rate
      - timestamp
  
  open_interest:
    description: "Perpetual futures open interest"
    update_interval_seconds: dynamic
    required_fields:
      - value
      - timestamp

platforms:
  - gateio
  - binance

market_types:
  - perpetual
  - spot
  - quarterly

default_market_type: perpetual
```

### config/infrastructure.yaml

System infrastructure parameters:

```yaml
retry:
  max_attempts: 3           # Network retry count
  delay_seconds: 1          # Delay between retries

request:
  timeout_seconds: 10       # HTTP request timeout

timestamp:
  max_staleness_intervals: 2  # Data > 2x update_interval is stale
```

### config/penalties.yaml

Cached CPD penalties from meta-learning:

```yaml
penalties:
  ohlcv: 1.0
  funding_rate: 2.1
  open_interest: 0.5
last_updated: 1708952345  # Unix timestamp
```

---

## TESTING & VALIDATION

### Test Files

**File:** `tests/test_layer1_to_layer2_integration.py`
- Integration tests: Layer 1 → Layer 2
- Verify data flows correctly through layers

**File:** `tests/test_layer2.py`
- CPD window detection
- Z-score calculation
- Aggregation
- Meta-learning for penalties
- PenaltyManager

**File:** `tests/test_layer3.py`
- Score threshold derivation
- SL/TP calculation
- Leverage determination
- Entry decision logic
- End-to-end signal generation

### Running Tests

```bash
pytest tests/
pytest tests/test_layer2.py -v
pytest tests/test_layer3.py -v
```

---

## DEPLOYMENT & OPERATIONAL NOTES

### Prerequisites

```
Python 3.9+
ruptures (CPD library)
numpy
pyyaml
requests
```

### Installation

```bash
pip install -r requirements.txt
```

### First Run

1. **Layer 1:** Fetch data from all scopes
2. **Layer 2 Meta-Learning:** Run `python scripts/run_meta_learning.py`
   - Computes optimal penalties for each scope
   - Saves to `config/penalties.yaml`
3. **Layer 2 & 3:** Normal operation

### Monitoring

- Check logs for:
  - Layer 1: Data existence checks
  - Layer 2: CPD regime changes, z-score magnitudes
  - Layer 3: Threshold changes, signal generation

### Maintenance

- **Penalty refresh:** Auto-refresh every 7 days (configurable)
- **Manual refresh:** Call `penalty_manager.force_refresh_if_needed()`
- **Add new scope:** Add to `config/sources.yaml` + implement connector method

---

## KEY TAKEAWAYS

### What Works Well (✅)

1. **Layer 1**: Perfectly isolated, format-based, zero judgment
2. **Layer 2**: Pure data-driven, meta-learning on homogeneity, transparent
3. **Architecture**: Three clean layers, each focused on one task
4. **Transparency**: All configuration explicit in YAML

### What Needs Fixing (❌)

1. **Layer 3 Missing Meta-Learning**: `penalty_score` and `penalty_price` without selection process
2. **Hardcoded Fallbacks**: Magic numbers in thresholds.py when history insufficient
3. **System Integration**: No clear integration point or orchestrator calling all three layers

### Immediate Actions

1. Implement `meta_learn_score_penalty()` for score distribution
2. Implement `meta_learn_price_penalty()` for price regime
3. Create Layer 3 PenaltyManager for caching Layer 3 penalties
4. Replace hardcoded fallbacks with data-driven approach
5. Create orchestrator script that ties all three layers together

---

## REFERENCES

- **Blueprint:** `Blueprint_trading_truth_engine.md` - System constitution
- **Supplement:** `Blueprint_supplement.md` - Extended specifications
- **Project Root:** `d:\trading_truth_engine\`

---

**Document Version:** 1.0  
**Last Updated:** February 24, 2026  
**Status:** For External AI Review
