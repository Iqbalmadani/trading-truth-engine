# Test Threshold Result

## Test Code
```python
import numpy as np
from src.layer3.thresholds import get_score_thresholds

score_hist = list(np.random.randn(100))
up, down = get_score_thresholds(score_hist, penalty=0.1)

print(f"Threshold UP: {up:.4f}")
print(f"Threshold DOWN: {down:.4f}")
```

## Console Output
```
Threshold UP: -0.6861
Threshold DOWN: -0.6861
```

## Test Status
✅ **PASSED** - Exit Code: 0 (No errors)

## Execution Details
- **Date**: March 11, 2026
- **Python Version**: 3.14.2
- **Working Directory**: d:/trading_truth_engine
- **Function Tested**: `src.layer3.thresholds.get_score_thresholds()`
- **Parameters**:
  - `score_hist`: List of 100 random normal distribution values
  - `penalty`: 0.1
