# Layer 3 Redesign — Changelog & Documentation

**Date:** 2026-05-07  
**Triggered by:** Diagnostic report `SHORT_ETH_USDT_2026-05-07_020013.html` showing suicide trade (R:R=0.097, 261 change points, CPD window=4)

---

## Root Cause

The meta-learning metric (`between_var / within_var`) was **mathematically biased toward over-segmentation**. Micro-segments (2-3 datapoints) trivially have near-zero `within_var`, so the metric always preferred the **lowest penalty** (`0.1`) → **261 change points** from 550 data points → regime of only 4 candles → SL/TP range of $5 → R:R = 0.097 (suicide trade).

## Changes Made

### Phase 1: Meta-Learning Metric + Penalty Candidates (`meta_learning.py`)

**BEFORE:**
```python
# Biased metric — rewards over-segmentation
quality = between_var / (within_var + epsilon)

# Hardcoded penalty candidates
DEFAULT_PENALTY_CANDIDATES = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
```

**AFTER:**
```python
# Calinski-Harabasz Index (F-ratio from ANOVA)
# Naturally penalizes over-segmentation via (n-k)/(k-1) factor
ch_score = (between_var * (n - k)) / (within_var * (k - 1) + epsilon)

# BIC-scaled penalty candidates from data's own variance
bic_base = 2.0 * sigma2 * np.log(n)  # Information theory (Schwarz 1978)
candidates = [bic_base * m for m in np.logspace(-1, 1, 10)]
```

**Why Calinski-Harabasz:**
- k=261, n=550 → (n-k)/(k-1) = 289/260 = **1.1** (penalized)
- k=5, n=550 → (n-k)/(k-1) = 545/4 = **136.25** (rewarded)
- **~122x natural preference** for meaningful segmentation over micro-segmentation
- This is the F-statistic from ANOVA — mathematical identity, not human choice

**Why BIC-scaled candidates:**
- `2σ²·ln(n)` is the theoretically optimal PELT penalty (Killick et al., 2012)
- Scales automatically to data magnitude — works for any asset, any timeframe
- No more human-chosen `[0.1, 0.5, ..., 10.0]`

---

### Phase 2: Remove Hardcoded Safety Nets (`risk.py`)

**BEFORE:**
```python
# Hardcoded 0.001 (0.1%) narrow regime fallback
if (natural_high - natural_low) / natural_high < 0.001:
    natural_high = max(price_history)  # Falls back to full history!
    natural_low = min(price_history)

# Hardcoded 0.001 minimum distance correction
min_distance = entry_price * 0.001
if direction == "LONG" and tp <= entry_price:
    tp = entry_price + min_distance  # Artificial TP correction!
```

**AFTER:**
```python
# Zero range = no structure → don't trade
if natural_high == natural_low:
    return None, None  # Data says: no meaningful support/resistance

# Geometry impossible → don't trade
if direction == "LONG" and tp <= entry_price:
    return None, None  # Data says: already at/above regime high

if direction == "SHORT" and tp >= entry_price:
    return None, None  # Data says: already at/below regime low
```

**Philosophy:** When data says "regime has no tradeable structure" or "geometry is impossible," the correct response is **don't trade** — not "artificially fix the numbers."

---

### Phase 4: Remove Hardcoded Lookback (`run_pipeline.py`)

**BEFORE:**
```python
lookback=20,  # Shorter lookback for Layer 3 (recent regime volatility)
```

**AFTER:**
```python
lookback=lookback_periods,  # Same depth as Layer 2 — let CPD find the regime
```

CPD exists to find regimes from larger datasets. Limiting input to 20 candles artificially constrained CPD's visibility. Now uses same `lookback_periods` as Layer 2 (default: 50).

---

### Phase 5: Remove Time-Based Refresh (`penalty_manager.py`)

**BEFORE:**
```python
REFRESH_INTERVAL_DAYS = 7  # Human-chosen refresh interval

def should_refresh(self) -> bool:
    age_days = (time.time() - self.last_updated_timestamp) / 86400
    return age_days > REFRESH_INTERVAL_DAYS
```

**AFTER:**
```python
# No more REFRESH_INTERVAL_DAYS constant
# Penalties computed fresh every pipeline run

def force_refresh_if_needed(self, score_history, price_history) -> bool:
    """Always recompute penalties fresh from current data."""
    logger.info("Computing fresh Layer 3 penalties from current data...")
    # ... meta-learning runs every time
    # Falls back to cached penalties only if meta-learning fails
```

**Philosophy:** Data structure can change at any time. A 7-day interval is a human assumption about "how often markets change." Now the system always uses the freshest data-derived penalties. Computational cost: < 1 second for 30 evaluations (10 candidates × 3 models).

---

### Phase 3: DEFERRED — R:R Viability Gate

R:R ≥ 1.0 gate was intentionally deferred. After the new meta-learning runs for several cycles, we will evaluate whether the improved penalty selection naturally produces viable R:R ratios. If not, Phase 3 will be implemented.

---

## Summary of Numbers Removed

| Removed | Was In | Replaced With |
|---|---|---|
| `0.001` (narrow regime threshold) | `risk.py:77` | Return `None` — data says don't trade |
| `0.001` (min distance multiplier) | `risk.py:98` | Return `None` — geometry impossible |
| `[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]` | `meta_learning.py:25` | BIC-scaled candidates from `2σ²·ln(n)` |
| `7` (refresh interval days) | `penalty_manager.py:35` | Fresh computation every run |
| `20` (price lookback) | `run_pipeline.py:235` | Uses `lookback_periods` parameter |

## Files Modified

| File | Change |
|---|---|
| `src/layer3/meta_learning.py` | Full rewrite — CH metric + BIC candidates |
| `src/layer3/risk.py` | Removed 2× hardcoded `0.001`, updated docstring |
| `src/layer3/penalty_manager.py` | Full rewrite — always fresh compute |
| `scripts/run_pipeline.py` | `lookback=20` → `lookback=lookback_periods` |

## How to Verify (Test at 4:00 AM Scheduled Run)

When the scheduled pipeline runs, check in `logs/scheduled_runs/scheduler.log`:

1. **Meta-learning logs should show BIC-scaled penalties:**
   ```
   BIC base penalty: X.XXXXXX (σ²=..., n=..., ln(n)=...)
   ```

2. **Penalty selected should NOT be 0.1:**
   ```
   Optimal selected: model=XX, penalty=X.XXXXXX (Calinski-Harabasz = X.XXXX)
   ```

3. **Change points should be reasonable (< 20% of data):**
   ```
   CPD detected N regime(s), current regime has M values
   ```

4. **If signal is generated, check diagnostic HTML report:**
   - SL/TP distances should be meaningful (not $0.45)
   - R:R should be reasonable (ideally > 1.0)
   - CPD Window should be > 4 candles

5. **If signal is 0 (neutral):**
   - Check if it's because regime geometry was unfavorable
   - Look for: `"Data says don't trade"` or `"geometry unfavorable"` in logs
   - This is CORRECT behavior — system refuses to trade when data doesn't support it

## Rollback

If issues occur, the previous versions of all files are in git history. The only persistent state that changed is `config/penalties.yaml` — which will be overwritten on next run anyway (fresh computation every run).
