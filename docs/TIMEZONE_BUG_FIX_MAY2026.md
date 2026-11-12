# Timezone Bug Fix Documentation - May 2026

**Date**: May 3, 2026  
**Status**: ✅ RESOLVED  
**Severity**: MEDIUM - Diagnostic files had confusing timestamps but functionality was not affected

---

## Problem Description

### Observed Behavior
When the scheduler ran the pipeline on **May 3, 2026 at 2:08 AM** (local time UTC+07:00):
- **Diagnostic file was created** with correct content ✅
- **But filename had wrong timestamp**:
  - Filename: `NEUTRAL_ETH_USDT_2026-05-02_190805.html` (May 2, 19:08:05 UTC)
  - File creation time in Windows: `5/3/2026 2:08:05 AM` (local time)
  - **Time gap: ~7 hours** ❌

### User Confusion
- User checked `diagnostics/output/` folder
- Expected to see files with **May 3** date in filename
- Only saw files with **May 2** in filename
- Thought scheduler hadn't run correctly ❌

---

## Root Cause Analysis

### System Configuration
```
System Timezone: UTC+07:00 (Bangkok, Hanoi, Jakarta - SE Asia)
Windows Local Time: 02:24:33 on May 3, 2026
UTC Time (Actual): 19:24:23 on May 2, 2026
```

### Code Issues

#### Issue #1: Using `datetime.utcnow()` in report_builder.py
```python
# src/diagnostics/report_builder.py (Line 30)
now = datetime.utcnow()  # ❌ Returns UTC time, not local
timestamp = now.isoformat()  # ❌ Stores as UTC ISO string
```

**Problem**: This creates a UTC timestamp (2026-05-02T19:24:23), but Windows file system saves files with local time (2026-05-03 02:24:33).

#### Issue #2: Using UTC timestamp for filename in html_renderer.py
```python
# src/diagnostics/html_renderer.py (Line 76)
ts_raw = html_data.get("timestamp", datetime.utcnow().isoformat())  # ❌ UTC
try:
    dt = datetime.fromisoformat(ts_raw)  # ❌ No timezone conversion
    ts = dt.strftime("%Y-%m-%d_%H%M%S")  # ❌ Uses UTC time directly
except Exception:
    ts = ts_raw.replace(":", "").replace("T", "_")[:19]
return f"{signal}_{pair}_{ts}.html"  # ❌ Filename has UTC time
```

**Problem**: Filename is generated from UTC timestamp, but user sees file creation time in local timezone → mismatch!

---

## Solution Implemented

### Change #1: Update report_builder.py

**File**: `src/diagnostics/report_builder.py`  
**Line**: 1-30

```python
# BEFORE:
from datetime import datetime, timedelta

now = datetime.utcnow()
timestamp = now.isoformat()

# AFTER:
from datetime import datetime, timedelta, timezone

now = datetime.now(timezone.utc)
timestamp = now.isoformat()
```

**Why**: 
- `datetime.now(timezone.utc)` is more explicit and includes timezone info
- Allows html_renderer to detect and convert to local time

### Change #2: Update html_renderer.py - Add timezone import

**File**: `src/diagnostics/html_renderer.py`  
**Line**: 1-6

```python
# BEFORE:
from datetime import datetime

# AFTER:
from datetime import datetime, timezone
```

### Change #3: Update html_renderer.py - Convert UTC to Local Time for Filename

**File**: `src/diagnostics/html_renderer.py`  
**Line**: 69-81

```python
# BEFORE:
def _get_filename(self, html_data: Dict[str, Any]) -> str:
    signal = str(html_data.get("layer3", {}).get("signal", "0"))
    if signal == "0":
        signal = "NEUTRAL"
    pair = html_data.get("pair", "UNKNOWN").replace("/", "_")
    ts_raw = html_data.get("timestamp", datetime.utcnow().isoformat())
    try:
        dt = datetime.fromisoformat(ts_raw)
        ts = dt.strftime("%Y-%m-%d_%H%M%S")
    except Exception:
        ts = ts_raw.replace(":", "").replace("T", "_")[:19]
    return f"{signal}_{pair}_{ts}.html"

# AFTER:
def _get_filename(self, html_data: Dict[str, Any]) -> str:
    signal = str(html_data.get("layer3", {}).get("signal", "0"))
    if signal == "0":
        signal = "NEUTRAL"
    pair = html_data.get("pair", "UNKNOWN").replace("/", "_")
    ts_raw = html_data.get("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        # Parse UTC timestamp and convert to local time for filename
        dt_utc = datetime.fromisoformat(ts_raw)
        if dt_utc.tzinfo is None:
            # If no timezone info, assume UTC
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        # Convert UTC to local timezone
        dt_local = dt_utc.astimezone()
        ts = dt_local.strftime("%Y-%m-%d_%H%M%S")
    except Exception:
        ts = ts_raw.replace(":", "").replace("T", "_")[:19]
    return f"{signal}_{pair}_{ts}.html"
```

**Why**:
- `dt_utc.astimezone()` automatically converts UTC to system's local timezone
- Filename now reflects what user sees in Windows file explorer ✅
- Works correctly in any timezone (UTC, UTC+7, UTC-5, etc.)

---

## Testing Results

### Before Fix
```
Pipeline Run: May 3, 2026 2:08:05 AM (Local UTC+7)
Filename Generated: SHORT_ETH_USDT_2026-05-02_190805.html  ❌
Windows Creation Time: 5/3/2026 2:08:05 AM
Time Mismatch: 7 hours
```

### After Fix
```
Pipeline Run: May 3, 2026 2:24:33 AM (Local UTC+7)
Filename Generated: NEUTRAL_ETH_USDT_2026-05-03_022433.html  ✅
Windows Creation Time: 5/3/2026 2:24:33 AM
Time Mismatch: 0 hours ✅
```

---

## Impact Analysis

| Aspect | Before | After |
|--------|--------|-------|
| **Functionality** | ✅ Works correctly | ✅ Works correctly |
| **Filename Clarity** | ❌ Confusing (7-hour lag) | ✅ Matches Windows timestamp |
| **User Experience** | ❌ Misleading | ✅ Clear and intuitive |
| **Cross-timezone Support** | ❌ Only UTC correct | ✅ Works in any timezone |
| **Debugging** | ❌ Hard to correlate | ✅ Easy to correlate logs with files |

---

## How to Prevent Similar Issues

### Best Practices for Python Datetime

#### ❌ DON'T: Use naive UTC datetime
```python
# BAD - No timezone info
now = datetime.utcnow()  # Returns datetime without tzinfo
```

#### ✅ DO: Use timezone-aware datetime
```python
# GOOD - Explicit timezone
now = datetime.now(timezone.utc)  # Returns UTC with tzinfo
```

#### ✅ DO: Convert between timezones when needed
```python
# If you have UTC and need local time
dt_utc = datetime.now(timezone.utc)
dt_local = dt_utc.astimezone()  # Auto-converts to system local tz
```

#### ✅ DO: Document timezone assumptions
```python
def _get_filename(self, html_data: Dict[str, Any]) -> str:
    """Generate filename using LOCAL timezone for clarity.
    
    Args:
        html_data: Contains timestamp in UTC ISO format
    
    Returns:
        Filename with LOCAL time (matches Windows file creation time)
    """
    # Implementation...
```

---

## Files Modified

| File | Lines | Change Type | Reason |
|------|-------|------------|--------|
| `src/diagnostics/report_builder.py` | 1-30 | Import + datetime | Add timezone awareness |
| `src/diagnostics/html_renderer.py` | 5-6 | Import | Add timezone conversion support |
| `src/diagnostics/html_renderer.py` | 69-86 | Function logic | Convert UTC → Local for filename |
| `src/diagnostics/html_renderer.py` | 456 | Function logic | Fix `_build_footer_section()` — replace deprecated `utcnow()` with `datetime.now(timezone.utc)` |

---

## Lessons Learned

### 1. **Timezone Awareness is Critical**
   - Always use timezone-aware datetime objects
   - Use `datetime.now(timezone.utc)` instead of `datetime.utcnow()`

### 2. **Match User's Expected Format**
   - Filename timestamps should match what users see in file explorer
   - Don't show UTC time if user operates in different timezone

### 3. **Timezone Conversions Need Explicit Logic**
   - `utcnow()` + file system local time = confusing mismatch
   - Always convert UTC → local when generating user-facing strings

### 4. **Testing Across Timezones**
   - This bug was hidden because system is UTC+7
   - Would have been caught immediately if deployed in UTC timezone

---

## Additional Context

### Scheduler Task Details
- **Task Name**: TradingTruthEngine_Pipeline
- **Trigger**: Every hour at clean times (01:00, 02:00, 03:00, etc.)
- **Log Location**: `logs/scheduled_runs/scheduler.log`
- **Diagnostic Output**: `diagnostics/output/`

### Timeline of Events
- **Apr 26, 2026 02:54 AM**: Last scheduler run before issue
- **May 1, 2026 3:47 PM - 4:17 PM**: Manual runs with old bug
- **May 3, 2026 2:08 AM**: Scheduler run with old bug (timestamps mismatch)
- **May 3, 2026 2:24 AM**: First run with fix → filename now correct ✅
- **May 3, 2026 2:37 AM**: Additional fix by Claude — `_build_footer_section()` `utcnow()` → `datetime.now(timezone.utc)` ✅

---

## How to Apply Similar Fixes in Other Code

If you find similar timezone issues elsewhere:

```python
# Find UTC-only timestamps:
# grep -r "datetime.utcnow()" src/

# Replace with timezone-aware:
# datetime.now(timezone.utc)

# For filename generation, always convert to local:
dt_utc = datetime.fromisoformat(iso_string)  # Assume UTC
dt_local = dt_utc.astimezone()  # Convert to local
filename_timestamp = dt_local.strftime("%Y-%m-%d_%H%M%S")
```

---

## References

- Python datetime documentation: https://docs.python.org/3/library/datetime.html
- Timezone handling best practices: https://docs.python.org/3/library/datetime.html#aware-and-naive-objects
- Issue tracker: Internal documentation

---

**Document Version**: 1.1  
**Last Updated**: May 3, 2026 02:37 AM  
**Change Log**:
- v1.0 (02:30 AM) — Initial fix by Copilot: `report_builder.py` + `html_renderer.py` filename logic
- v1.1 (02:37 AM) — Additional fix by Claude: `html_renderer.py` `_build_footer_section()` still used deprecated `utcnow()`  
**Status**: ✅ CLOSED - All `utcnow()` instances eliminated
