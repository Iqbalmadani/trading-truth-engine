# TRADING TRUTH ENGINE - TECHNICAL IMPLEMENTATION GUIDE

**Generated:** February 24, 2026  
**Status:** Detailed Technical Reference  
**Document Purpose:** Implementation & Development Guide for External AI

---

## QUICK START REFERENCE

### System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Trading Truth Engine                      │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  LAYER 1: DATA VERIFICATION (Binary Output: 0/1)            │
│  ├─ Input: Raw API data                                     │
│  ├─ Process: Structural validation only                     │
│  ├─ Output: {scope_name: 0 or 1}                            │
│  └─ Files: src/layer1/verifier.py, connectors/              │
│                                                               │
│                           ↓                                   │
│                                                               │
│  LAYER 2: SCORE GENERATION (Float Output)                    │
│  ├─ Input: Layer 1 results + time-series data              │
│  ├─ Process: CPD + z-score per scope                        │
│  ├─ Output: Single aggregated score                         │
│  └─ Files: src/layer2/*.py                                  │
│                                                               │
│                           ↓                                   │
│                                                               │
│  LAYER 3: TRADING SIGNALS (Action Output)                    │
│  ├─ Input: Layer 2 score + price history                    │
│  ├─ Process: Threshold → SL/TP → Leverage                   │
│  ├─ Output: {entry, sl, tp, leverage, rr_ratio}            │
│  └─ Files: src/layer3/*.py                                  │
│                                                               │
└─────────────────────────────────────────────────────────────┘

Config Files:
├─ config/sources.yaml        ← Scope definitions
├─ config/infrastructure.yaml ← System parameters
└─ config/penalties.yaml      ← Cached meta-learning results
```

---

## LAYER 1: DATA VERIFICATION

### Complete Flow Diagram

```
Exchange API → Raw Data
    ↓
┌─────────────────────────────────────────────┐
│ Verifier.verify_all(connector, ...)         │
├─────────────────────────────────────────────┤
│                                              │
│ For each scope in config/sources.yaml:      │
│   1. connector.fetch(scope, ...)            │
│   2. _check_existence(data)                 │
│   3. _check_required_fields(data)           │
│   4. _check_parseability(data)              │
│   5. _check_timestamp_freshness(data)       │
│   6. _check_structural_rules(data)          │
│   7. Return 0 or 1                          │
│                                              │
└─────────────────────────────────────────────┘
    ↓
Result: {scope_name: 0/1, ...}
```

### Implementation Reference

**File:** `src/layer1/verifier.py`

```python
class Verifier:
    """Main existence check engine"""
    
    def __init__(self):
        """Load configuration from YAML"""
        self.sources_config = get_sources_config()
        self.infrastructure_config = get_infrastructure_config()
        self.max_staleness = self.infrastructure_config["timestamp"]["max_staleness_intervals"]
    
    def verify_all(self, connector, pair, timeframe, market_type) -> Dict[str, int]:
        """
        Main entry point - check all scopes
        
        Return: {scope_name: 0 or 1}
        """
        results = {}
        for scope_name in self.scopes:
            data = connector.fetch(scope_name, pair, timeframe, market_type)
            result = self._check_existence(scope_name, data, timeframe)
            results[scope_name] = result
        return results
    
    def _check_existence(self, scope_name, data, timeframe) -> int:
        """
        Multi-step existence check
        
        Returns: 0 (not exist) or 1 (exists and valid)
        """
        # Step 1: Raw existence (from blueprint)
        if data is None or data == "" or data == [] or (isinstance(data, dict) and len(data) == 0):
            return 0
        
        # Step 2-5: Structural checks
        if not self._check_required_fields(scope_name, data):
            return 0
        if not self._check_parseability(scope_name, data):
            return 0
        if not self._check_timestamp_freshness(scope_name, data, timeframe):
            return 0
        if not self._check_structural_rules(scope_name, data):
            return 0
        
        return 1
```

### Configuration: sources.yaml

```yaml
scopes:
  ohlcv:
    description: "Candlestick data"
    update_interval_seconds: dynamic
    required_fields: [open, high, low, close, volume, timestamp]
    
  funding_rate:
    description: "Perpetual funding rate"
    update_interval_seconds: 28800  # 8 hours
    required_fields: [rate, timestamp]
    
  open_interest:
    description: "Open interest"
    update_interval_seconds: dynamic
    required_fields: [value, timestamp]

platforms: [gateio, binance]
market_types: [perpetual, spot]
```

### Adding a New Scope

1. Add entry to `config/sources.yaml`
2. Define `required_fields`
3. Update `STRUCTURAL_RULES` in verifier.py if needed
4. Implement fetch method in connector classes
5. Test with `pytest tests/test_layer1_to_layer2_integration.py`

---

## LAYER 2: SCORE GENERATION

### Complete Flow Diagram

```
Layer 1 Results → Active Scopes
    ↓
┌─────────────────────────────────────────────┐
│ layer2(connector, layer1_results, ...)      │
├─────────────────────────────────────────────┤
│                                              │
│ For each active scope:                      │
│   1. Fetch historical data                  │
│   2. Get penalty from PenaltyManager        │
│   3. get_cpd_window(data, penalty)          │
│   4. zscore_in_window(window, current)      │
│   5. Accumulate z-scores                    │
│                                              │
│ aggregate_score = sum(z_scores)             │
│                                              │
└─────────────────────────────────────────────┘
    ↓
Result: (score, metadata)
```

### Detailed Implementation

**File:** `src/layer2/aggregator.py`

```python
def layer2(
    connector: BaseConnector,
    layer1_results: Dict[str, int],
    pair: str,
    timeframe: str,
    market_type: str,
    penalty_manager: PenaltyManager,
    lookback_periods: int = 50
) -> Tuple[float, Dict[str, Any]]:
    """
    Transform multi-source data into single score.
    
    Algorithm:
    1. Identify active scopes (passed Layer 1)
    2. For each active scope:
       a. Fetch lookback_periods of historical data
       b. Get CPD penalty from cache
       c. Find recent regime via CPD
       d. Calculate z-score in that regime
    3. Sum all z-scores
    4. Return (total_score, metadata)
    """
    sources_config = get_sources_config()
    active_scopes = [name for name, value in layer1_results.items() if value == 1]
    
    if not active_scopes:
        return 0.0, {"active_scopes": [], "z_scores": {}}
    
    z_scores = {}
    penalties_used = {}
    
    for scope_name in active_scopes:
        try:
            # Fetch data
            data_history = _fetch_scope_history(
                connector, scope_name, pair, timeframe, market_type, lookback_periods
            )
            
            if not data_history or len(data_history) < 2:
                z_scores[scope_name] = 0.0
                continue
            
            # Get penalty
            penalty = penalty_manager.get_penalty(scope_name)
            penalties_used[scope_name] = penalty
            
            # CPD + z-score
            cpd_window = get_cpd_window(data_history, penalty)
            current_value = data_history[-1]
            z = zscore_in_window(cpd_window, current_value)
            z_scores[scope_name] = z
            
        except Exception as e:
            logger.error(f"Failed to process {scope_name}: {e}")
            z_scores[scope_name] = 0.0
    
    total_score = sum(z_scores.values())
    
    metadata = {
        "active_scopes": active_scopes,
        "num_active": len(active_scopes),
        "z_scores": z_scores,
        "penalties_used": penalties_used,
        "score": total_score,
        "aggregation_method": "sum"
    }
    
    return total_score, metadata
```

### CPD Implementation

**File:** `src/layer2/cpd.py`

```python
def get_cpd_window(series: List[float], penalty: float) -> List[float]:
    """
    Apply PELT (Pruned Exact Linear Time) algorithm with RBF kernel.
    
    Returns: Most recent regime (segment from last change point to now)
    
    Algorithm:
    1. Fit PELT model on time-series
    2. Predict change points using penalty parameter
    3. Extract last change point
    4. Return data from that point to present
    """
    if not series or len(series) < 3:
        logger.debug(f"Series too short ({len(series)}), returning full series")
        return series
    
    try:
        data = np.array(series, dtype=float)
        algo = rpt.Pelt(model="rbf", min_size=1, jump=1).fit(data)
        change_points = algo.predict(pen=penalty)
        
        # change_points is [cp1, cp2, ..., len(data)]
        # Last element is always len(data)
        # Second-to-last is last actual change point
        if len(change_points) > 1:
            last_cp = change_points[-2]
        else:
            last_cp = 0
        
        window = series[last_cp:]
        logger.debug(f"CPD: {len(change_points)-1} regimes detected, current regime size: {len(window)}")
        return window
        
    except Exception as e:
        logger.error(f"CPD failed: {e}, returning full series")
        return series
```

### Z-Score Implementation

**File:** `src/layer2/zscore.py`

```python
def zscore_in_window(series: List[float], current_value: float, epsilon: float = 1e-9) -> float:
    """
    Calculate z-score of current_value within series window.
    
    Formula: z = (x - μ) / σ
    
    Where:
    - x = current_value
    - μ = mean of series
    - σ = standard deviation of series
    
    Returns:
    - Positive: above mean
    - Negative: below mean
    - 0: no volatility / no signal
    """
    if not series:
        logger.warning("Empty series for z-score")
        return 0.0
    
    series_array = np.array(series, dtype=float)
    mean = np.mean(series_array)
    std = np.std(series_array)
    
    if std < epsilon:
        logger.debug(f"Zero volatility (std={std}), returning z=0")
        return 0.0
    
    z_score = (current_value - mean) / std
    return float(z_score)
```

### Meta-Learning for Penalties

**File:** `src/layer2/meta_learning.py`

```python
def find_optimal_penalty(
    series: List[float],
    penalty_candidates: Optional[List[float]] = None,
    min_size: int = 1
) -> Tuple[float, float]:
    """
    Find CPD penalty with highest homogeneity score.
    
    Homogeneity = Between-segment variance / Within-segment variance
    - Higher ratio = better
    - Means: segments are different from each other, consistent within
    
    Returns: (best_penalty, best_homogeneity_score)
    """
    if penalty_candidates is None:
        penalty_candidates = DEFAULT_PENALTY_CANDIDATES
    
    if not series or len(series) < 3:
        logger.warning("Series too short for meta-learning")
        return DEFAULT_PENALTY_CANDIDATES[2], 0.0
    
    best_penalty = None
    best_homogeneity = -np.inf
    
    for penalty in penalty_candidates:
        try:
            algo = rpt.Pelt(model="rbf", min_size=min_size, jump=1).fit(np.array(series))
            cps = algo.predict(pen=penalty)
            
            homogeneity = calculate_homogeneity(series, cps)
            
            logger.debug(f"Penalty {penalty}: homogeneity={homogeneity:.4f}, segments={len(cps)-1}")
            
            if homogeneity > best_homogeneity:
                best_homogeneity = homogeneity
                best_penalty = penalty
        
        except Exception as e:
            logger.warning(f"CPD failed for penalty {penalty}: {e}")
            continue
    
    if best_penalty is None:
        logger.error("All penalties failed, using default")
        return DEFAULT_PENALTY_CANDIDATES[2], 0.0
    
    logger.info(f"Optimal penalty: {best_penalty} (homogeneity={best_homogeneity:.4f})")
    return best_penalty, best_homogeneity

def calculate_homogeneity(series: List[float], change_points: List[int], epsilon: float = 1e-9) -> float:
    """
    Calculate homogeneity score for a set of change points.
    
    Steps:
    1. Split series into segments at change points
    2. Calculate mean of each segment
    3. Calculate between-segment variance (variance of segment means)
    4. Calculate within-segment variance (average variance within segments)
    5. Ratio: between / within
    """
    data = np.array(series, dtype=float)
    
    # Split into segments
    segments = []
    prev = 0
    for cp in change_points:
        if cp > prev:
            segments.append(data[prev:cp])
        prev = cp
    
    if len(segments) < 2:
        return 0.0
    
    # Between-segment variance
    segment_means = [np.mean(seg) for seg in segments if len(seg) > 0]
    between_var = np.var(segment_means)
    
    # Within-segment variance
    within_vars = [np.var(seg) for seg in segments if len(seg) > 0]
    within_var = np.mean(within_vars) if within_vars else 0.0
    
    # Homogeneity
    quality = between_var / (within_var + epsilon)
    return float(quality)
```

### Penalty Manager

**File:** `src/layer2/penalty_manager.py`

```python
class PenaltyManager:
    """
    Manages CPD penalty caching and refresh lifecycle.
    
    Lifecycle:
    1. First run: meta-learning calculates penalties, saves to YAML
    2. Subsequent: load cached penalties
    3. Auto-refresh: if > 7 days old, recalculate
    4. Manual: force refresh via method
    """
    
    def __init__(self):
        self.penalties = {}
        self.last_updated_timestamp = None
        self._load_cached_penalties()
    
    def _load_cached_penalties(self):
        """Load from config/penalties.yaml if exists"""
        if not PENALTIES_CONFIG_PATH.exists():
            logger.info("No cached penalties found")
            return
        
        try:
            with open(PENALTIES_CONFIG_PATH, "r") as f:
                data = yaml.safe_load(f)
            
            self.penalties = data.get("penalties", {})
            self.last_updated_timestamp = data.get("last_updated")
            
            logger.info(f"Loaded {len(self.penalties)} penalties")
        except Exception as e:
            logger.error(f"Failed to load penalties: {e}")
    
    def get_penalty(self, scope_name: str, default: float = 1.0) -> float:
        """Get penalty for scope, or default"""
        return self.penalties.get(scope_name, default)
    
    def should_refresh(self) -> bool:
        """Check if penalties > REFRESH_INTERVAL_DAYS old"""
        if not self.penalties or not self.last_updated_timestamp:
            return True
        
        age_days = (time.time() - self.last_updated_timestamp) / 86400
        return age_days > REFRESH_INTERVAL_DAYS
    
    def refresh_penalties(self, new_penalties: Dict[str, float]):
        """Update penalties and save to YAML"""
        self.penalties = new_penalties.copy()
        self.last_updated_timestamp = time.time()
        self._save_penalties(self.penalties)
        logger.info("Penalties refreshed")
    
    def force_refresh_if_needed(self, connector, pair, timeframe, market_type) -> bool:
        """Auto-refresh if stale, return True if refreshed"""
        if not self.should_refresh():
            logger.debug("Penalties fresh, no refresh needed")
            return False
        
        logger.info("Performing automatic penalty refresh...")
        try:
            new_penalties = meta_learn_all_scopes(
                connector, pair, timeframe, market_type
            )
            self.refresh_penalties(new_penalties)
            return True
        except Exception as e:
            logger.error(f"Auto-refresh failed: {e}")
            return False
```

---

## LAYER 3: TRADING SIGNALS

### Complete Flow Diagram

```
Layer 2 Score + Price History
    ↓
┌─────────────────────────────────────────────┐
│ layer3(score, score_hist, price, ...)       │
├─────────────────────────────────────────────┤
│                                              │
│ Step 1: Derive thresholds                   │
│   - get_score_thresholds(score_hist)        │
│   - Returns: threshold_up, threshold_down   │
│                                              │
│ Step 2: Decide entry                        │
│   - if score > threshold_up: LONG           │
│   - if score < threshold_down: SHORT        │
│   - else: 0 (neutral)                       │
│                                              │
│ IF entry ≠ 0:                               │
│   Step 3: Calculate SL/TP                   │
│     - get_sl_tp(price_hist, penalty, dir)   │
│                                              │
│   Step 4: Calculate leverage                │
│     - get_leverage(price_hist, penalty)     │
│                                              │
│   Step 5: Calculate risk/reward ratio       │
│     - rr_ratio = reward / risk              │
│                                              │
└─────────────────────────────────────────────┘
    ↓
Result: {entry, sl, tp, leverage, rr_ratio, metadata}
```

### Implementation Reference

**File:** `src/layer3/executor.py` (Main Entry Point)

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
    Main Layer 3 orchestrator.
    
    Input requirements:
    - score_history: >= 3 points for CPD
    - price_history: >= 2 points
    - penalty_score, penalty_price: from meta-learning or defaults
    - exchange_leverage_max: from exchange API
    
    Output: Complete trading signal
    """
    
    # Input validation
    if not score_history or len(score_history) < 3:
        logger.warning("Insufficient score history")
        return _neutral_signal(current_price)
    
    if not price_history or len(price_history) < 2:
        logger.warning("Insufficient price history")
        return _neutral_signal(current_price)
    
    try:
        # Step 1: Thresholds from score distribution
        threshold_up, threshold_down = get_score_thresholds(
            score_history, penalty_score
        )
        
        # Step 2: Entry decision
        entry = decide_entry(current_score, threshold_up, threshold_down)
        
        # If neutral, return neutral signal
        if entry == "0":
            return {
                "entry": "0",
                "entry_price": current_price,
                "stop_loss": None,
                "take_profit": None,
                "leverage": 1.0,
                "risk_reward_ratio": None,
                "metadata": {...}
            }
        
        # Step 3: Risk management
        sl, tp = get_sl_tp(price_history, penalty_price, entry)
        if sl is None or tp is None:
            return _neutral_signal(current_price)
        
        # Step 4: Leverage
        leverage = get_leverage(price_history, penalty_price, exchange_leverage_max)
        
        # Step 5: Risk/reward
        if entry == "LONG":
            risk = abs(current_price - sl)
            reward = abs(tp - current_price)
        else:  # SHORT
            risk = abs(current_price - sl)
            reward = abs(current_price - tp)
        
        rr_ratio = reward / risk if risk > 0 else None
        
        # Build signal
        return {
            "entry": entry,
            "entry_price": current_price,
            "stop_loss": sl,
            "take_profit": tp,
            "leverage": leverage,
            "risk_reward_ratio": rr_ratio,
            "metadata": {
                "threshold_up": threshold_up,
                "threshold_down": threshold_down,
                "score": current_score,
                "score_z": _calculate_score_z(score_history, current_score),
                "price": current_price,
                "regime_volatility": _calculate_regime_volatility(price_history, penalty_price)
            }
        }
    
    except Exception as e:
        logger.error(f"Layer 3 failed: {e}")
        return _neutral_signal(current_price)
```

### Threshold Derivation

**File:** `src/layer3/thresholds.py`

```python
def get_score_thresholds(score_history: List[float], penalty: float) -> Tuple[float, float]:
    """
    Derive trading thresholds from score distribution.
    
    Algorithm:
    1. Apply CPD to score_history
    2. Extract most recent regime
    3. Calculate mean ± std of recent regime
    
    Result:
    - threshold_up = mean + std (LONG threshold)
    - threshold_down = mean - std (SHORT threshold)
    """
    if not score_history or len(score_history) < 3:
        logger.warning(f"Score history too short ({len(score_history)})")
        # ⚠️ ISSUE: These are hardcoded, needs fixing
        return 1.0, -1.0
    
    try:
        data = np.array(score_history, dtype=float)
        algo = rpt.Pelt(model="rbf", min_size=1, jump=1).fit(data)
        change_points = algo.predict(pen=penalty)
        
        # Extract most recent regime
        if len(change_points) > 1:
            last_cp = change_points[-2]
        else:
            last_cp = 0
        
        recent_scores = score_history[last_cp:]
        
        # Natural statistical bounds
        mean_score = np.mean(recent_scores)
        std_score = np.std(recent_scores)
        
        threshold_up = mean_score + std_score
        threshold_down = mean_score - std_score
        
        logger.info(f"Thresholds: UP={threshold_up:.4f}, DOWN={threshold_down:.4f}")
        return threshold_up, threshold_down
    
    except Exception as e:
        logger.error(f"CPD failed: {e}, using fallback")
        return 1.0, -1.0  # ⚠️ ISSUE: Hardcoded fallback
```

### Entry Decision

**File:** `src/layer3/executor.py`

```python
def decide_entry(score: float, threshold_up: float, threshold_down: float) -> str:
    """
    Pure binary logic from blueprint.
    
    score > threshold_up → LONG
    score < threshold_down → SHORT
    otherwise → 0 (neutral)
    """
    if score > threshold_up:
        return "LONG"
    elif score < threshold_down:
        return "SHORT"
    else:
        return "0"
```

### Stop Loss & Take Profit

**File:** `src/layer3/risk.py`

```python
def get_sl_tp(
    price_history: List[float],
    penalty: float,
    direction: str
) -> Tuple[Optional[float], Optional[float]]:
    """
    Determine SL/TP from natural price range in CPD regime.
    
    Algorithm:
    1. Apply CPD to find recent price regime
    2. Find natural_high and natural_low in that regime
    3. For LONG: sl=low, tp=high
    4. For SHORT: sl=high, tp=low
    """
    if not price_history or len(price_history) < 2:
        return None, None
    
    if direction == "0":
        return None, None
    
    try:
        # Get recent price regime
        window = get_cpd_window(price_history, penalty)
        
        if not window or len(window) < 2:
            logger.warning("CPD window too short for SL/TP")
            return None, None
        
        # Natural bounds
        natural_high = max(window)
        natural_low = min(window)
        
        # Direction-specific assignment
        if direction == "LONG":
            sl = natural_low
            tp = natural_high
        elif direction == "SHORT":
            sl = natural_high
            tp = natural_low
        else:
            return None, None
        
        logger.info(f"SL/TP for {direction}: SL={sl:.6f}, TP={tp:.6f}")
        return sl, tp
    
    except Exception as e:
        logger.error(f"SL/TP calculation failed: {e}")
        return None, None
```

### Leverage Calculation

**File:** `src/layer3/risk.py`

```python
def get_leverage(
    price_history: List[float],
    penalty: float,
    exchange_max: float
) -> float:
    """
    Determine leverage from inverse volatility relationship.
    
    Algorithm:
    1. Calculate volatility in current CPD regime
    2. Calculate volatility in full history
    3. leverage = vol_history / vol_current
    4. Cap at [1.0, exchange_max]
    
    Logic:
    - Low current volatility → high leverage
    - High current volatility → low leverage
    """
    if not price_history or len(price_history) < 2:
        return 1.0
    
    if exchange_max <= 0:
        return 1.0
    
    try:
        # Current regime volatility
        window = get_cpd_window(price_history, penalty)
        if not window or len(window) < 2:
            return 1.0
        
        window_returns = np.diff(window) / window[:-1]
        vol_current = np.std(window_returns)
        
        # Full history volatility
        history_returns = np.diff(price_history) / price_history[:-1]
        vol_history = np.std(history_returns)
        
        # Guard against division by zero
        if vol_current < 1e-10:
            logger.debug("Current volatility near zero, using 1x")
            return 1.0
        
        # Calculate leverage
        leverage = vol_history / vol_current
        leverage_capped = np.clip(leverage, 1.0, exchange_max)
        
        logger.info(f"Leverage: {leverage_capped:.2f}x (vol_ratio={leverage:.2f})")
        return leverage_capped
    
    except Exception as e:
        logger.error(f"Leverage calculation failed: {e}")
        return 1.0
```

---

## CONFIGURATION & DEPLOYMENT

### Config Files Structure

**File:** `config/infrastructure.yaml`

```yaml
# Retry configuration
retry:
  max_attempts: 3         # How many times to retry failed API calls
  delay_seconds: 1        # Delay between retries

# HTTP request configuration
request:
  timeout_seconds: 10     # HTTP request timeout

# Timestamp configuration
timestamp:
  max_staleness_intervals: 2  # Data older than 2x update_interval is stale
```

**File:** `config/sources.yaml`

```yaml
scopes:
  ohlcv:
    description: "Candlestick OHLCV data"
    update_interval_seconds: dynamic      # Follows user timeframe
    required_fields: [open, high, low, close, volume, timestamp]

  funding_rate:
    description: "Perpetual funding rate"
    update_interval_seconds: 28800        # 8 hours (from exchange API)
    required_fields: [rate, timestamp]

  open_interest:
    description: "Open interest"
    update_interval_seconds: dynamic
    required_fields: [value, timestamp]

platforms: [gateio, binance]
market_types: [perpetual, spot]
default_market_type: perpetual
```

**File:** `config/penalties.yaml` (Auto-generated)

```yaml
penalties:
  ohlcv: 1.0              # CPD penalty for OHLCV data
  funding_rate: 2.1       # CPD penalty for funding rate
  open_interest: 0.5      # CPD penalty for open interest

last_updated: 1708952345  # Unix timestamp
```

### Adding New Exchange

1. Create `src/layer1/connectors/newexchange.py`

```python
from .base import BaseConnector

class NewExchangeConnector(BaseConnector):
    """NewExchange connector"""
    
    BASE_URL = "https://api.newexchange.com"
    
    def fetch(self, scope, pair, timeframe, market_type):
        """Implement fetch logic for this exchange"""
        # Returns normalized dict with required fields or None
        pass
    
    def validate_pair(self, pair, market_type):
        # Check pair validity
        pass
    
    def get_supported_timeframes(self):
        # Return list of supported timeframes
        pass
```

2. Update `config/sources.yaml`

```yaml
platforms: [gateio, binance, newexchange]  # Add here
```

3. Test integration

```python
def test_newexchange_connector():
    connector = NewExchangeConnector()
    verifier = Verifier()
    results = verifier.verify_all(connector, "ETH_USDT", "1h", "perpetual")
    assert isinstance(results, dict)
```

---

## TESTING GUIDELINES

### Test Structure

```
tests/
├── test_layer1_to_layer2_integration.py
├── test_layer2.py
├── test_layer3.py
└── conftest.py  (Pytest fixtures)
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_layer2.py -v

# Run specific test
pytest tests/test_layer2.py::TestCPD::test_cpd_window_detection -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html
```

### Test Fixtures (conftest.py)

```python
@pytest.fixture
def sample_price_data():
    """Sample OHLCV data for testing"""
    return [100.0, 101.0, 99.5, 102.0, 98.5, 103.0]

@pytest.fixture
def sample_score_data():
    """Sample score distribution"""
    return [-0.5, -0.3, 0.1, 0.3, 1.2, 2.1, 1.8, 0.5]

@pytest.fixture
def mock_connector():
    """Mock exchange connector"""
    connector = MagicMock(spec=BaseConnector)
    connector.fetch.return_value = {"close": 100.0, "volume": 1000}
    return connector
```

---

## DEBUGGING & TROUBLESHOOTING

### Common Issues

#### Issue: "Score history too short"

```
Problem: Layer 3 receives < 3 score values
Solution: Ensure Layer 2 accumulates sufficient history before calling Layer 3
Check: score_history length >= 3
```

#### Issue: "CPD detected 0 regimes"

```
Problem: PELT algorithm couldn't detect change points
Solution: Try lower penalty value (more sensitive)
Debug: Log change_points from get_cpd_window()
Check: Data has enough variation
```

#### Issue: "Zero volatility in z-score"

```
Problem: Data has no variation (all same value)
Solution: This is valid - system returns z=0
Check: Is data stale or broken?
Verify: Layer 1 should have caught this
```

#### Issue: "Leverage calculation returns 1x"

```
Problem: vol_current near zero or exceeds vol_history
Solution: Happens during low-vol periods (normal)
Check: Is this expected market condition?
Monitor: regime_volatility in metadata
```

### Logging & Debugging

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger("trading_truth_engine")
logger.setLevel(logging.DEBUG)
```

Example debug output:

```
INFO: Layer 1 | ohlcv: EXISTS (1)
INFO: Layer 1 | funding_rate: EXISTS (1)
INFO: Layer 1 complete | 2/3 scopes active
DEBUG: Layer 2 processing scope: ohlcv
DEBUG: CPD detected 3 regime(s), current regime starts at index 42
DEBUG: ohlcv: z-score = 1.23, current_value = 105.43
INFO: Layer 3 starting | current_score=2.45
DEBUG: Score thresholds: UP=1.50, DOWN=-0.50
INFO: Entry decision: LONG
INFO: SL/TP for LONG: SL=98.00, TP=110.00
INFO: Leverage: 1.50x
```

---

## PERFORMANCE CONSIDERATIONS

### Computational Complexity

| Operation | Time Complexity | Notes |
|---|---|---|
| Layer 1: Verify all scopes | O(n) | n = number of scopes (typically 3-5) |
| Layer 2: CPD | O(n log n) | n = data points, PELT algorithm |
| Layer 2: Z-score | O(n) | Simple mean/std calculation |
| Layer 3: Threshold CPD | O(m log m) | m = score history length |
| Layer 3: SL/TP | O(p log p) | p = price history length |

### Memory Usage

- Layer 1: Minimal (one-time checks)
- Layer 2: Score history cache (typically < 1MB)
- Layer 3: Price history cache (typically < 1MB)
- Total: < 10MB for typical deployment

### Optimization Tips

1. **Lookback period:** Default 50, can reduce if real-time needed
2. **Penalty candidates:** Default 6 values, can reduce to 3-4
3. **Meta-learning frequency:** Default 7 days, can increase to 30 days
4. **History cache:** Store in-memory, not reload each time

---

## DEPLOYMENT CHECKLIST

Before production:

- [ ] All tests passing: `pytest tests/ -v`
- [ ] Configuration YAML files valid
- [ ] Exchange credentials configured
- [ ] Connectivity to exchange verified
- [ ] Initial meta-learning completed
- [ ] Penalties cached in config/penalties.yaml
- [ ] Logging configured properly
- [ ] Error monitoring active
- [ ] Manual testing (paper trading) completed
- [ ] System capacity tested

Example deployment script:

```bash
#!/bin/bash

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v
if [ $? -ne 0 ]; then
    echo "Tests failed, abort deployment"
    exit 1
fi

# Run initial meta-learning
python scripts/run_meta_learning.py

# Start system
python scripts/run_pipeline.py
```

---

**Document Version:** 1.0 - Technical Implementation  
**Last Updated:** February 24, 2026  
**Status:** Complete Technical Reference
