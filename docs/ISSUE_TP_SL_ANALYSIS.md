# TP/SL Issue Analysis Report

**Date**: March 16, 2026  
**Status**: 🔴 CRITICAL  
**Severity**: HIGH  

---

## Executive Summary

Analysis of HTML diagnostic files in `diagnostics/output/` reveals a **critical bug in TP (Take Profit) calculation** affecting 40% of LONG trade signals. The issue causes **TP to equal Entry Price**, resulting in zero profit potential and invalid Risk/Reward ratios.

---

## Problem Statement

When a LONG trade signal is generated:
- **Expected**: TP > Entry Price (profit potential exists)
- **Actual (40% of cases)**: TP = Entry Price (no profit potential)

**Example:**
```
File: LONG_ETH_USDT_2026-03-11_222910.html
Entry Price:     $2236.80
TP:              $2236.80  ← SAME AS ENTRY
SL:              $2207.14
TP Distance:     $0        ← NO PROFIT
Risk/Reward:     undefined (0 ÷ 29.66)
```

---

## Files Affected (40% of LONG trades analyzed)

| File | Entry Price | SL | TP | TP Distance | Status |
|------|-------------|----|----|------------|--------|
| LONG_ETH_USDT_2026-03-11_222910.html | $2236.80 | $2207.14 | $2236.80 | $0 | ❌ BUG |
| LONG_ETH_USDT_2026-03-12_010017.html | $2118.85 | $2101.06 | $2118.85 | $0 | ❌ BUG |
| LONG_ETH_USDT_2026-03-12_030030.html | $2076.30 | $2069.66 | $2077.21 | $0.91 | ✅ OK |
| LONG_ETH_USDT_2026-03-12_100015.html | $2101.16 | $2043.90 | $2184.32 | $83.16 | ✅ OK |
| LONG_ETH_USDT_2026-03-12_110015.html | $2068.35 | $2043.60 | $2075.60 | $7.25 | ✅ OK |

**Failure Rate**: 2/5 = **40%**

---

## Root Cause

**Location**: [`src/layer3/risk.py#L29-L101`](src/layer3/risk.py) → `get_sl_tp()` function

**Problem Flow**:

1. CPD (Change Point Detection) calculates `natural_high` and `natural_low` from recent price regime
2. If regime range is too narrow (`natural_high ≈ natural_low`), code falls back to:
   ```python
   natural_high = max(price_history)
   natural_low = min(price_history)
   ```
3. For LONG trades: `tp = natural_high`
4. If current_price (entry price) is already near `max(price_history)`:
   ```
   tp ≈ max(price_history) ≈ current_price
   → TP = Entry Price  ❌
   ```

**Trigger Condition**: Entry signal fires when price is **near or at local peak**

---

## Impact Assessment

| Aspect | Impact |
|--------|--------|
| **Trading Signal Quality** | CRITICAL - 40% of signals have 0% profit potential |
| **Risk/Reward Calculation** | BROKEN - Division by zero or undefined ratios |
| **Trade Execution** | DANGEROUS - Traders may execute trades with no valid TP |
| **Diagnostics** | MISLEADING - HTML reports show invalid risk metrics |

---

## Technical Details

### CPD Window Analysis
```
When regime detection is weak:
- CPD window length < 2 (very narrow regime)
- Falls back to entire price_history
- If current_price ≈ max(price_history):
  → LONG: tp = max = current_price → BUG
  → SHORT: tp = min (usually far away) → OK
```

### Why This Happens
- System detects LONG signal at **price peaks**
- Peak detection uses high score threshold
- Entry price already near max historical price
- TP calculation uses max price → equals entry

---

## Recommended Solutions

### Option 1: Use Previous Max (Exclude Current Period)
```python
natural_high = max(price_history[:-1])  # Exclude latest candle
natural_low = min(price_history[:-1])
```
**Pros**: Simple, prevents same-price issue  
**Cons**: May miss recent regime shift

### Option 2: Use Percentile-Based Targets
```python
tp = np.percentile(price_history, 90)  # 90th percentile for LONG
sl = np.percentile(price_history, 10)  # 10th percentile
```
**Pros**: More robust, avoids edge cases  
**Cons**: Slightly arbitrary threshold selection

### Option 3: Enforce Minimum TP-Entry Distance
```python
min_buffer = current_price * 0.001  # 0.1% buffer
if tp <= current_price + min_buffer:
    tp = current_price * 1.001
```
**Pros**: Guarantees TP > Entry  
**Cons**: Artificial buffer may be too small

### Option 4: Reject Invalid Signals
```python
if tp <= current_price:  # For LONG
    logger.warning(f"Invalid TP={tp} <= Entry={current_price}, rejecting signal")
    return None, None
```
**Pros**: Prevents bad trades from executing  
**Cons**: May reject valid marginal cases

---

## Recommended Action

**Implement Option 1 + Option 4 combination**:
1. Use `max(price_history[:-1])` to avoid same-price scenarios
2. Add validation check: if TP ≤ Entry, reject trade signal
3. Add logging to track rejection rate
4. Retest against historical diagnostics

---

## Testing Checklist

- [ ] Verify fix resolves all 40% failed cases
- [ ] Check that TP > Entry for all LONG trades
- [ ] Verify TP < Entry for all SHORT trades
- [ ] Validate Risk/Reward ratios are now calculable
- [ ] Run full historical backtest through pipeline
- [ ] Compare RR distribution before/after fix

---

## References

- **Code File**: `src/layer3/risk.py`
- **Function**: `get_sl_tp(price_history, penalty, direction)`
- **Diagnostics Source**: `diagnostics/output/*.html`
- **Config Reference**: `config/penalties.yaml`
