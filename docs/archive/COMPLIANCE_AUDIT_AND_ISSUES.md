# TRADING TRUTH ENGINE - COMPLIANCE AUDIT & ISSUE TRACKING

**Generated:** February 24, 2026  
**Status:** Critical Issues Identified  
**Document Purpose:** External AI Review & Issue Resolution

---

## QUICK REFERENCE CHECKLIST

### Overall System Status

| Component | Status | Severity | Notes |
|---|---|---|---|
| Layer 1 | ✅ COMPLIANT | - | Perfect alignment with blueprint |
| Layer 2 | ✅ COMPLIANT | - | Perfect alignment with blueprint |
| Layer 3 | ⚠️ PARTIAL | CRITICAL | Missing meta-learning mechanisms |
| System Integration | ❌ MISSING | CRITICAL | No orchestrator combining all 3 layers |

---

## LAYER 1 COMPLIANCE AUDIT

### Constitutional Requirements

```
Rule 1: Layer 1 must NOT contain any numeric thresholds
Rule 2: Layer 1 must NOT have historical comparisons
Rule 3: Layer 1 must NOT filter content (only format checks)
Rule 4: Layer 1 must be source-agnostic
```

### Audit Results

| Rule | Requirement | Implementation | Result |
|---|---|---|---|
| 1 | No numeric thresholds in logic | Only infrastructure parameters | ✅ PASS |
| 2 | No historical comparisons | Only current-vs-current checks | ✅ PASS |
| 3 | No content filtering | Format-based only | ✅ PASS |
| 4 | Source-agnostic | Connector-based, extensible | ✅ PASS |

### Detailed Findings

**File:** `src/layer1/verifier.py`

#### Check 1.1: Raw Existence
```python
✅ if data is None: return 0
✅ if data == "": return 0
✅ if data == []: return 0
✅ if isinstance(data, dict) and len(data) == 0: return 0
✅ # numeric 0 is considered existing
```
Verdict: Perfect alignment with blueprint

#### Check 1.2: Required Fields
```python
✅ _check_required_fields(): Verifies presence and non-nullness
❌ Search for: Any field-value threshold checks
   Result: None found - only structure checks
```
Verdict: Compliant

#### Check 1.3: Parseability
```python
✅ float(value) conversion attempts
✅ No value judgment or comparison
```
Verdict: Compliant

#### Check 1.4: Timestamp Freshness
```python
⚠️ age > max_age where max_age = max_staleness_intervals * interval_seconds
✅ Parameter: max_staleness_intervals = 2 (from infrastructure.yaml)
✅ This is infrastructure constraint, not data judgment
✅ Reasoning: Exchange updates per interval, so 2x = 2 missed updates
```
Verdict: Compliant (infrastructure parameter, not judgment)

#### Check 1.5: Structural Rules
```python
✅ volume >= 0: Mathematical impossibility (negative volume impossible)
✅ high >= low: OHLCV definition (not judgment)
✅ Rules in STRUCTURAL_RULES dict (configurable, not hardcoded)
```
Verdict: Compliant (definitional, not judgmental)

### Configuration Transparency

**File:** `config/sources.yaml`
```yaml
✅ Scope definitions: clear, machine-readable
✅ Required fields: explicit per scope
✅ Update intervals: traceable to source (or "dynamic")
✅ Platform list: extensible design
```

**File:** `config/infrastructure.yaml`
```yaml
✅ max_retries = 3: Technical necessity
✅ timeout = 10s: Technical constraint
✅ max_staleness_intervals = 2: Infrastructure parameter
✅ All documented and configurable
```

### Conclusion

**Layer 1: ✅ PERFECT COMPLIANCE**

No issues found. Completely aligned with blueprint philosophy.

---

## LAYER 2 COMPLIANCE AUDIT

### Constitutional Requirements

```
Rule 1: Layer 2 must NOT contain manual weights
Rule 2: Layer 2 must NOT have hardcoded windows
Rule 3: Layer 2 must NOT have hardcoded thresholds
Rule 4: Meta-learning must evaluate homogeneity, NOT profit
Rule 5: Score cannot be modified before Layer 3
```

### Audit Results

| Rule | Requirement | Implementation | Result |
|---|---|---|---|
| 1 | No manual weights | Z-scores aggregate via sum | ✅ PASS |
| 2 | No hardcoded windows | CPD determines window | ✅ PASS |
| 3 | No hardcoded thresholds | Penalties from meta-learning | ✅ PASS |
| 4 | Homogeneity metric only | Between-var / Within-var | ✅ PASS |
| 5 | No score modification | Pure sum aggregation | ✅ PASS |

### Detailed Findings

#### Finding 2.1: Z-Score Calculation

**File:** `src/layer2/zscore.py`

```python
def zscore_in_window(series, current_value, epsilon=1e-9) -> float:
    mu = np.mean(series)
    sigma = np.std(series)
    if sigma < epsilon:
        return 0.0  # No signal from constant data
    return (current_value - mu) / sigma
```

**Analysis:**
- ✅ Pure formula from statistics
- ✅ No manual weighting (coefficient = 1.0)
- ✅ Direction (+/-) embedded in formula
- ✅ Magnitude (distance from mean) embedded in formula
- ✅ No further adjustment

Verdict: Perfect

#### Finding 2.2: CPD Window Determination

**File:** `src/layer2/cpd.py`

```python
def get_cpd_window(series, penalty) -> list:
    algo = rpt.Pelt(model="rbf", min_size=1, jump=1).fit(data)
    change_points = algo.predict(pen=penalty)
    last_cp = change_points[-2] if len(change_points) > 1 else 0
    return series[last_cp:]
```

**Analysis:**
- ✅ Algorithm: PELT (Pruned Exact Linear Time) - domain-invariant
- ✅ Kernel: RBF (Radial Basis Function) - flexible
- ✅ Penalty: ONLY parameter (from meta-learning)
- ✅ Window: Determined by data structure (change points)
- ✅ No hardcoded window size

Verdict: Perfect

#### Finding 2.3: Aggregation

**File:** `src/layer2/aggregator.py`

```python
total_score = 0.0
for scope_name in active_scopes:
    # ... fetch data, get penalty, calculate z ...
    z = zscore_in_window(cpd_window, current_value)
    total_score += z
return total_score
```

**Analysis:**
- ✅ Simple SUM: pure math, no weighting
- ✅ Each z-score weighted by its own volatility (in z calculation)
- ✅ No additional multipliers or filters
- ✅ No clipping or capping

Verdict: Perfect

#### Finding 2.4: Meta-Learning for Penalties

**File:** `src/layer2/meta_learning.py`

```python
def calculate_homogeneity(series, change_points, epsilon=1e-9) -> float:
    # Split into segments using change points
    segments = [series[prev:cp] for cp in change_points]
    
    segment_means = [np.mean(seg) for seg in segments]
    between_var = np.var(segment_means)
    within_vars = [np.var(seg) for seg in segments]
    within_var = np.mean(within_vars)
    
    quality = between_var / (within_var + epsilon)
    return quality
```

**Analysis:**
- ✅ Metric: Homogeneity (structural, not financial)
- ✅ Between-variance: Segments different from each other
- ✅ Within-variance: Segments internally consistent
- ✅ Higher ratio = cleaner segmentation = better penalty
- ✅ NO profit/return metrics used
- ✅ NO performance metrics used

Verdict: Perfect

#### Finding 2.5: Penalty Candidates

**File:** `src/layer2/meta_learning.py`

```python
DEFAULT_PENALTY_CANDIDATES = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
```

**Analysis:**
- ✅ Explicit list (not hidden)
- ✅ Range covers low (many segments) to high (few segments)
- ⚠️ Technical boundary: Someone had to choose these numbers
- ✅ This is allowed per blueprint: "Manusia menyediakan rentang, data yang memilih"
  (Humans provide the range, data chooses the value)

Verdict: Compliant (intentional design choice)

#### Finding 2.6: Penalty Manager

**File:** `src/layer2/penalty_manager.py`

```python
class PenaltyManager:
    def get_penalty(scope_name, default=1.0) -> float
    def refresh_penalties(new_penalties) -> None
    def should_refresh() -> bool
    
PENALTIES_CONFIG_PATH = "config/penalties.yaml"
REFRESH_INTERVAL_DAYS = 7
```

**Analysis:**
- ✅ Caching mechanism: penalties saved after meta-learning
- ✅ Auto-refresh: 7 days (reasonable, configurable)
- ✅ Storage: config/penalties.yaml (explicit, observable)
- ✅ Lifecycle: First run → meta-learn; subsequent → cached
- ✅ No modification of penalties in runtime

Verdict: Perfect

### Conclusion

**Layer 2: ✅ PERFECT COMPLIANCE**

No issues found. Completely aligned with blueprint philosophy.

---

## LAYER 3 COMPLIANCE AUDIT - CRITICAL ISSUES

### Constitutional Requirements

```
Rule 1: Layer 3 must NOT contain fixed percentile thresholds (90%, 10%, etc.)
Rule 2: Layer 3 must NOT contain fixed SL/TP multipliers (ATR x2, x4, etc.)
Rule 3: Layer 3 must NOT have hardcoded leverage_max
Rule 4: Layer 3 must use CPD penalties (selected via meta-learning)
Rule 5: Layer 3 must only read score (not raw data)
Rule 6: Every number must be traceable to a source
```

### Audit Results

| Rule | Requirement | Implementation | Result |
|---|---|---|---|
| 1 | No fixed percentiles | Thresholds from CPD | ✅ Code OK |
| 2 | No fixed multipliers | SL/TP from natural range | ✅ Code OK |
| 3 | No hardcoded leverage_max | Uses exchange_max parameter | ✅ Code OK |
| 4 | CPD penalties via meta-learning | **NO META-LEARNING EXISTS** | ❌ CRITICAL |
| 5 | Only read score | Only score + price history | ✅ Code OK |
| 6 | All numbers traceable | **HARDCODED FALLBACKS** | ❌ CRITICAL |

### CRITICAL ISSUE #1: Missing Meta-Learning for `penalty_score`

**Location:** `src/layer3/executor.py`, `src/layer3/thresholds.py`

**File:** `src/layer3/executor.py` (line 69)

```python
def layer3(
    current_score: float,
    score_history: List[float],
    ...
    penalty_score: float,  # ← PARAMETER, not computed
    ...
):
```

**File:** `src/layer3/thresholds.py` (line 54)

```python
def get_score_thresholds(score_history: list, penalty: float) -> tuple:
    """
    Threshold tidak ditentukan manusia (bukan 90%, bukan 80%).
    CPD membaca distribusi score dan menemukan batas alaminya sendiri.
    """
    # ... PELT algorithm ...
    threshold_up = mean_score + std_score
    threshold_down = mean_score - std_score
```

**The Problem:**

Blueprint Stage 3.1 states:
> "penalty adalah satu-satunya parameter — dipilih via meta-learning"

Implementation reality:
- `penalty_score` is received as parameter
- No function `meta_learn_score_penalty()` exists
- No mechanism to select optimal `penalty_score`
- Hardcoded as `1.0` in all tests

**Tree of Issues:**

```
layer3() called with penalty_score parameter
    ↓
BUT: Where does this penalty come from?
    ↓
    ├─ NOT from PenaltyManager (only manages Layer 2 scope penalties)
    ├─ NOT from meta-learning (no such function)
    ├─ NOT validated at layer3() entry point
    └─ Hardcoded in tests as 1.0
    
Result: VIOLATION of "every number must be traceable"
```

**In Production:**

- If called from main pipeline: penalty_score source unknown
- If hardcoded at call site: Magic number appears elsewhere
- If passed from config: Not in any config file
- Result: System cannot explain penalty_score selection

**Constitution Violation:** ✅ YES (Rule #4 and Rule #6)

---

### CRITICAL ISSUE #2: Missing Meta-Learning for `penalty_price`

**Location:** `src/layer3/risk.py`

**File:** `src/layer3/risk.py` (lines 30 and 97)

```python
def get_sl_tp(price_history: List[float], penalty: float, direction: str):
    # ... CPD uses this penalty ...
    
def get_leverage(price_history: List[float], penalty: float, exchange_max: float):
    # ... CPD uses this penalty ...
```

**The Problem:**

Blueprint Stages 3.3 & 3.4 use CPD on price data with penalty parameter, stating:
> "penalty dipilih via meta-learning"

Implementation reality:
- `penalty_price` is received as parameter to `layer3()`
- No function `meta_learn_price_penalty()` exists
- No data-driven selection of `penalty_price`
- Hardcoded as `1.0` in all tests

**Tree of Issues:**

```
get_sl_tp(price_history, penalty, direction)
get_leverage(price_history, penalty, exchange_max)
    ↓
BUT: How should penalty be chosen for price data?
    ↓
    ├─ NOT from PenaltyManager
    ├─ NOT from meta-learning
    ├─ NOT from price distribution analysis
    └─ Hardcoded or passed as unknown
    
Result: VIOLATION of "every number must be traceable"
```

**Difference from Layer 2:**

In Layer 2, each scope has its own penalty because:
- Different scopes (volume, funding_rate, OI) have different structures
- Meta-learning finds optimal penalty per scope
- Result cached in config/penalties.yaml

In Layer 3, price data might need a DIFFERENT penalty than Layer 2 scope penalties because:
- Price regime structure differs from score distribution structure
- Optimal CPD segmentation might be different
- NO selection mechanism exists

**Constitution Violation:** ✅ YES (Rule #4 and Rule #6)

---

### CRITICAL ISSUE #3: Hardcoded Fallback Thresholds

**Location:** `src/layer3/thresholds.py` (lines 52-56)

```python
def get_score_thresholds(score_history: list, penalty: float) -> tuple:
    if not score_history or len(score_history) < 3:
        logger.warning(
            f"Score history too short ({len(score_history)}), using ±1 as fallback"
        )
        return 1.0, -1.0  # ⚠️ HARDCODED MAGIC NUMBERS
```

**The Problem:**

Fallback thresholds `1.0, -1.0` are hardcoded with no justification.

**Questions:**

1. Why exactly `1.0` and `-1.0`? 
   - Answer: ❌ No explanation in code
2. What is the data source for these numbers?
   - Answer: ❌ Human choice, not data-driven
3. What if data doesn't support these thresholds?
   - Answer: ❌ System still uses them silently

**Consequence:**

When `score_history < 3 points`, system silently returns fixed thresholds without error.

This violates Rule #6: "Setiap angka yang muncul di kode harus bisa dijawab: dari mana angka ini?" 
("Every number in code must answer: where does this number come from?" — if answer is "I decided it", that's a violation)

**Constitution Violation:** ✅ YES (Rule #6)

**Impact:**

- In early trading (insufficient history): Uses hardcoded thresholds
- No transparency about this fallback
- No data-driven alternative

---

### Other Layer 3 Checks - COMPLIANT

#### Check 3.1: Entry Decision Logic
```python
if score > threshold_up: return "LONG"
elif score < threshold_down: return "SHORT"
else: return "0"
```
✅ Pure binary logic from blueprint

#### Check 3.2: SL/TP Calculation
```python
window = get_cpd_window(price_history, penalty)
natural_high = max(window)
natural_low = min(window)
# LONG: sl = natural_low, tp = natural_high
```
✅ Natural range, no multipliers

#### Check 3.3: Leverage Calculation
```python
leverage = vol_history / vol_current
leverage_capped = np.clip(leverage, 1.0, exchange_max)
```
✅ Volatility-based, exchange_max from API

#### Check 3.4: Layer Isolation
Raw data read: NONE ✅
Score read: YES ✅
Price history read: YES (for regime) ✅

---

### Summary: Layer 3 Issues

| Issue | File | Line(s) | Severity | Type |
|---|---|---|---|---|
| **Missing penalty_score meta-learning** | executor.py, thresholds.py | 69, 54 | 🔴 CRITICAL | Architectural |
| **Missing penalty_price meta-learning** | executor.py, risk.py | 70, 30, 97 | 🔴 CRITICAL | Architectural |
| **Hardcoded fallback thresholds** | thresholds.py | 52-56 | 🔴 CRITICAL | Rule Violation |

**Layer 3: ⚠️ PARTIAL COMPLIANCE (67%)**

---

## SYSTEM INTEGRATION ISSUES

### Issue: No Orchestrator

**Problem:**

The three layers exist independently:
- Layer 1: `Verifier` class
- Layer 2: `layer2()` function
- Layer 3: `layer3()` function

No top-level orchestrator that:
1. Calls Layer 1
2. Passes results to Layer 2
3. Passes results to Layer 3
4. Manages the complete pipeline

**Current State:**

Tests show how layers are supposed to work, but:
- No `main.py` or `run_pipeline.py` calls them in sequence
- Parameter passing between layers unclear
- Error handling across layers undefined
- Execution flow not documented

**Evidence:**

```bash
❌ No file: scripts/run_pipeline.py
❌ No file: scripts/orchestrator.py
❌ No file: src/pipeline.py
✅ Blueprint mentions: "scripts/" folder (never implemented)
```

**Required:**

Creation of orchestration layer that:
1. Initializes Verifier + Connectors
2. Runs Layer 1 → get existence results
3. Initializes PenaltyManager + auto-refresh
4. Runs Layer 2 → get score
5. Get or compute Layer 3 penalties
6. Runs Layer 3 → get signal
7. Returns final trading signal

---

## FIX RECOMMENDATIONS

### Priority 1 (CRITICAL - Block deployment)

#### 1A: Implement Meta-Learning for Score Penalty

**Create:** `src/layer3/meta_learning.py`

```python
def meta_learn_score_penalty(
    score_history: List[float],
    penalty_candidates: Optional[List[float]] = None
) -> float:
    """
    Find optimal CPD penalty for score distribution.
    
    Algorithm:
    1. Evaluate CPD homogeneity for each penalty candidate
    2. Select penalty with highest homogeneity
    3. Return best penalty
    
    Homogeneity metric: same as Layer 2 meta-learning
    """
```

#### 1B: Implement Meta-Learning for Price Penalty

**Create:** `src/layer3/meta_learning.py` (same file)

```python
def meta_learn_price_penalty(
    price_history: List[float],
    penalty_candidates: Optional[List[float]] = None
) -> float:
    """
    Find optimal CPD penalty for price regime detection.
    
    Same algorithm as score penalty but for price data.
    """
```

#### 1C: Create Layer 3 Penalty Manager

**Create:** `src/layer3/penalty_manager.py`

```python
class PenaltyManagerLayer3:
    """Manage Layer 3 penalties (score + price) similar to Layer 2"""
    def __init__(self)
    def get_score_penalty(default=1.0) -> float
    def get_price_penalty(default=1.0) -> float
    def refresh_penalties(connector, pair, timeframe, market_type) -> None
```

**Config:** Extend `config/penalties.yaml` to store Layer 3 penalties

```yaml
layer2_penalties:
  ohlcv: 1.0
  ...

layer3_penalties:
  score: 1.5  # ← New
  price: 0.8  # ← New

last_updated: 1708952345
```

#### 1D: Replace Hardcoded Fallbacks in thresholds.py

**Option A: Data-Driven Fallback**

```python
if not score_history or len(score_history) < 3:
    # Use historical standards, not magic numbers
    if len(score_history) > 0:
        return float(np.max(score_history)), float(np.min(score_history))
    else:
        raise ValueError("Cannot compute thresholds without score values")
```

**Option B: Error Propagation**

```python
if not score_history or len(score_history) < 3:
    raise InsufficientDataError(
        f"Score history too short: {len(score_history)} samples, need >= 3"
    )
```

**Option C: Use Neutral Thresholds**

```python
if not score_history or len(score_history) < 3:
    # Neutral thresholds: don't trade until enough history
    logger.warning("Insufficient history, using neutral (no trade) thresholds")
    return float('inf'), float('-inf')  # Always returns "0" (neutral)
```

**Recommendation:** Option B (error propagation) forces awareness of data requirements

### Priority 2 (HIGH - Enable production)

#### 2A: Create System Orchestrator

**Create:** `scripts/run_pipeline.py`

```python
def run_trading_pipeline(
    connector: BaseConnector,
    pair: str,
    timeframe: str,
    market_type: str
) -> Dict[str, Any]:
    """
    Complete pipeline: Layer 1 → Layer 2 → Layer 3
    
    Returns: Final trading signal from Layer 3
    """
    
    # Layer 1: Check data existence
    verifier = Verifier()
    layer1_results = verifier.verify_all(connector, pair, timeframe, market_type)
    
    # Layer 2: Generate score
    penalty_manager_l2 = PenaltyManager()
    penalty_manager_l2.force_refresh_if_needed(connector, pair, timeframe, market_type)
    score, score_metadata = layer2(
        connector, layer1_results, pair, timeframe, market_type,
        penalty_manager_l2
    )
    
    # Layer 3: Generate signal
    penalty_manager_l3 = Penalty ManagerLayer3()
    penalty_manager_l3.force_refresh_if_needed(connector, pair, timeframe, market_type)
    
    signal = layer3(
        current_score=score,
        score_history=[...],  # From persistent storage or history
        current_price=...,
        price_history=[...],
        penalty_score=penalty_manager_l3.get_score_penalty(),
        penalty_price=penalty_manager_l3.get_price_penalty(),
        exchange_leverage_max=get_exchange_leverage_limit(connector, pair)
    )
    
    return signal
```

#### 2B: Add History Management

**Create:** `src/utils/history_manager.py`

```python
class HistoryManager:
    """Manage score and price history for Layer 2/3 operations"""
    def append_score(score: float) -> None
    def append_price(price: float) -> None
    def get_score_history(lookback: int) -> List[float]
    def get_price_history(lookback: int) -> List[float]
```

#### 2C: Documentation

Historical plan only. Deployment and operations guides are not included in this release; see the repository README for the current project scope and limitations.

---

## TESTING COVERAGE

### Current Tests

| Layer | Test File | Coverage |
|---|---|---|
| Layer 1 | test_layer1_to_layer2_integration.py | Basic |
| Layer 2 | test_layer2.py | Comprehensive |
| Layer 3 | test_layer3.py | Comprehensive (but hardcoded penalties) |

### Gaps

1. **No test for `meta_learn_score_penalty()`** - Doesn't exist
2. **No test for `meta_learn_price_penalty()`** - Doesn't exist  
3. **No integration test Layer 1→2→3** - Full pipeline
4. **No test of fallback thresholds** - Critical edge case
5. **No test of Penalty ManagerLayer3** - Doesn't exist

### Required New Tests

```python
def test_meta_learn_score_penalty():
    """Verify optimal penalty selection for score"""
    
def test_meta_learn_price_penalty():
    """Verify optimal penalty selection for price"""
    
def test_layer3_insufficient_score_history():
    """Verify behavior when score_history < 3"""
    # Should NOT use hardcoded thresholds
    
def test_full_pipeline_integration():
    """Layer 1 → Layer 2 → Layer 3 end-to-end"""
    
def test_penalty_manager_layer3():
    """PenaltyManagerLayer3 caching and refresh"""
```

---

## CHECKLIST FOR RESOLUTION

- [ ] Create `src/layer3/meta_learning.py` with score + price penalty meta-learning
- [ ] Create `src/layer3/penalty_manager.py` for Layer 3 penalty caching
- [ ] Update `config/penalties.yaml` to include layer3_penalties section
- [ ] Remove hardcoded fallback threshold `1.0, -1.0` from thresholds.py
- [ ] Implement Option B (error propagation) for insufficient history
- [ ] Create `scripts/run_pipeline.py` orchestrator
- [ ] Create `src/utils/history_manager.py` for score/price history
- [ ] Add new tests for meta-learning functions
- [ ] Add integration test for full pipeline
- [ ] Update docs with deployment & operation guides
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Code review for blueprint compliance
- [ ] Performance testing with real exchange data

---

## SUMMARY TABLE

### Compliance by Layer

| Layer | Compliant | Partial | Violations | Status |
|---|---|---|---|---|
| **Layer 1** | ✅ 100% | 0% | 0% | ✅ READY |
| **Layer 2** | ✅ 100% | 0% | 0% | ✅ READY |
| **Layer 3** | 67% | 33% | 0% | ⚠️ NEEDS FIX |

### Issue Severity

| Severity | Count | Component | Action |
|---|---|---|---|
| 🔴 CRITICAL | 3 | Layer 3 | Must fix before deployment |
| 🟡 HIGH | 1 | System | Must create orchestrator |
| 🟢 LOW | N/A | - | - |

---

**Document Version:** 1.0 - Comprehensive Audit  
**Last Updated:** February 24, 2026  
**Status:** For External AI Review & Resolution  
**Next Steps:** Implement Priority 1 fixes before production deployment
