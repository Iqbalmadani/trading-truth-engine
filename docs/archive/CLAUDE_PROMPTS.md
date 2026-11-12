# PROMPTS FOR CLAUDE AI - Copy One at a Time

## 📋 How to Use:
1. Go to claude.ai
2. Copy one prompt section below (##PROMPT 1, PROMPT 2, or PROMPT 3)
3. Paste to Claude
4. Claude will generate complete code
5. Copy the generated code back and paste to the corresponding .py file
6. Repeat for next prompt

---

## 🔵 PROMPT 1: ReportBuilder Implementation

**File to paste result into:** `src/diagnostics/report_builder.py`

**Copy everything below the "---" line:**

---

You are implementing the ReportBuilder class that converts Trading Truth Engine Layer 1/2/3 outputs into a structured dictionary (html_data) ready for HTML rendering.

### What you receive (inputs):
- layer1_results: dict like `{"ohlcv": 1, "funding_rate": 1, "open_interest": 1}` (binary 0/1 per scope)
- current_score: float (e.g., 1.2154 from Layer 2)
- layer2_metadata: dict containing z_scores dict, penalty value, etc.
- layer3_output: dict with "signal", "entry_price", "stop_loss", "take_profit", "leverage"
- score_history: list of floats representing score progression (e.g., [1.1, 1.2, 1.3])

### What you produce (output):
A complete `html_data` dictionary with this exact structure:

```python
html_data = {
    "timestamp": "2026-02-25T14:00:00",  # ISO 8601 format
    "pair": "ETH_USDT",
    "timeframe": "1h",
    "connector": "gateio",
    
    "layer1": {
        "scopes": {
            "ohlcv": 1,
            "funding_rate": 1,
            "open_interest": 1
        },
        "active_count": 3,  # count of 1's
        "total_count": 3,   # always 3 in current implementation
        "status": "3/3 ACTIVE ✅"  # formatted string
    },
    
    "layer2": {
        "current_score": 1.2154,
        "z_scores": {
            "ohlcv": 0.42,
            "funding_rate": -0.31,
            "open_interest": 0.78
        },
        "active_scopes": ["ohlcv", "funding_rate", "open_interest"],  # list of keys where value==1
        "cpd_info": {
            "penalty_used": 1.0,  # from layer2_metadata
            "window_size": 10,    # from layer2_metadata if available, else 0
            "change_points": 0,   # from layer2_metadata if available, else 0
            "regime_classification": "EMERGING_BULL"  # from layer2_metadata if available
        },
        "score_history_length": 4  # len(score_history)
    },
    
    "layer3": {
        "signal": "LONG",  # from layer3_output["signal"] ("LONG", "SHORT", or "0")
        "entry_price": 1890.50,
        "stop_loss": 1885.00,
        "take_profit": 1900.25,
        "leverage": 20.0,
        "leverage_max_from_api": 200.0,  # from layer3_output if available
        "risk_reward_ratio": 0.95,  # calculated or from layer3_output
        "thresholds": {
            "up": 1.85,    # from layer3_output if "thresholds" key exists
            "down": 1.05
        }
    },
    
    "score_history": [1.2154, 1.1038, 1.3521, 1.4210, 1.2154, 1.1890],  # last 10 or all
    "score_history_timestamps": ["14:00", "15:00", "16:00", "17:00", "18:00"],  # HH:MM format for each score
    
    "metadata": {
        "system_status": "READY",  # determined by whether all layers completed
        "last_api_fetch": "2026-02-25T13:59:45",  # ISO 8601 or "N/A"
        "data_quality": "GOOD",  # "GOOD", "DEGRADED", or "ERROR"
        "warnings": []  # list of warning strings, empty if none
    }
}
```

### Implementation requirements:

1. **Timestamp handling**: Use `datetime.utcnow().isoformat()` if not provided, else use provided timestamp
2. **Layer1 section**:
   - Extract scope names from layer1_results keys
   - Count how many have value == 1
   - Build status string "X/Y ACTIVE ✅" or "X/Y INACTIVE ❌" based on count
3. **Layer2 section**:
   - Extract z_scores from layer2_metadata["z_scores"] if exists, else build empty dict
   - Get active_scopes by filtering layer1_results keys where value == 1
   - Extract cpd_info from layer2_metadata with fallbacks to default values if missing
   - Score history length = len(score_history)
4. **Layer3 section**:
   - Copy all values from layer3_output
   - Handle "signal" field carefully (can be "0" or "LONG" or "SHORT")
   - Calculate risk_reward_ratio = take_profit distance / stop_loss distance if possible
5. **Score history**:
   - Take last 10 scores from score_history list (or all if < 10)
   - Generate timestamps (HH:MM format) backwards from current time
6. **Metadata**:
   - system_status = "READY" if current_score present and not error
   - last_api_fetch = current ISO timestamp
   - data_quality = "GOOD" if all layers active, "DEGRADED" if some missing
   - warnings = list of warning messages to display (empty list if none)

### Error handling:
- If z_scores missing from metadata, use empty dict {}
- If cpd_info missing, use default {"penalty_used": 0, "window_size": 0, "change_points": 0, "regime_classification": "UNKNOWN"}
- If thresholds missing, use {"up": 0, "down": 0}
- If score_history empty, use empty list
- Never crash, always return valid html_data dict

### Only generate the code for the `build_from_pipeline_output` method implementation. Keep the class definition and docstring as-is.

---

## 🔵 PROMPT 2: HtmlReporter Implementation (Part A - Structure & Main Methods)

**File to paste result into:** `src/diagnostics/html_renderer.py`

**Copy everything below the "---" line:**

---

You are implementing the HtmlReporter class that renders html_data dict (from ReportBuilder) into professional, beautiful HTML files with inline CSS.

### Requirements:

**Color Scheme:**
- Background: #1a1a1a
- Text: #ffffff
- Bullish (LONG): #00ff88
- Bearish (SHORT): #ff4444
- Warning: #ffaa00
- Neutral: #888888
- Card BG: #2a2a2a

**Design:**
- Dark theme, professional appearance
- Self-contained (inline CSS only, no external files)
- Responsive (works on mobile and desktop)
- Clear visual hierarchy
- Cards with padding and borders

**Sections in HTML (in order):**
1. Header with timestamp, pair, timeframe, system status
2. Layer 1 card: 3 scope status boxes (grid layout)
3. Layer 2 card: current score, z-score breakdown, CPD info
4. Layer 3 card: signal prominently, trade parameters, LONG/SHORT scenarios side-by-side
5. Score history: simple text representation or mini ASCII chart
6. Risk metrics: entry, SL, TP, leverage, risk/reward
7. Footer with system info

**Filename format:** `{SIGNAL}_{PAIR}_{TIMESTAMP}.html`
- SIGNAL: Extracted from html_data["layer3"]["signal"] (LONG, SHORT, or NEUTRAL)
- PAIR: Extracted from html_data["pair"]
- TIMESTAMP: From html_data["timestamp"], formatted as YYYY-MM-DD_HHMMSS (no colons)
- Example: `LONG_ETH_USDT_2026-02-25_140000.html`

**Implementation for these methods:**

### 1. `_get_filename(self, html_data: dict) -> str:`
Extract signal, pair, timestamp from html_data and format as filename.
- Signal: html_data["layer3"]["signal"] → convert "0" to "NEUTRAL"
- Pair: html_data["pair"]
- Timestamp: html_data["timestamp"] → convert "2026-02-25T14:00:00" to "2026-02-25_140000"
- Return: "LONG_ETH_USDT_2026-02-25_140000.html"

### 2. `render(self, html_data: dict) -> str:`
Generate complete HTML string from html_data.
- Call helper methods to build each section
- Combine all sections with complete HTML structure
- Include <head> with meta tags and complete inline CSS
- Return: Complete HTML string (not saved)

### 3. `save(self, html_data: dict) -> str:`
Render HTML and save to file.
- Create output_dir if not exists
- Call render() to get HTML string
- Call _get_filename() to get filename
- Save HTML to file
- Return: Full filepath of saved file

### Helper methods to implement:

**4. `_build_style(self) -> str:`**
Return complete inline CSS as string. Include:
- Global styles (body, dark background, white text)
- Container (max-width: 1200px, centered, padding)
- Header section (24px bold, centered)
- Card styles (.card class: padding 16px, bg #2a2a2a, border-radius 8px, margin 8px)
- Grid layout for Layer 1 scopes (3 columns)
- Grid layout for stats (flexible)
- Typography (headers 16px, body 14px, labels 12px uppercase)
- Color utilities (.text-green, .text-red, .text-warning)
- Signal box styling (prominent, large, colored based on signal)
- Scenario boxes (LONG/SHORT side-by-side)
- Responsive media queries for mobile
- Return as complete <style>...</style> block

**5. `_build_header_section(self, html_data: dict) -> str:`**
Return HTML string for header showing:
- Timestamp (formatted nice)
- Pair and timeframe
- Overall system status (from metadata)
- Data quality indicator
Return as `<header>...</header>` div

**6. `_build_layer1_section(self, html_data: dict) -> str:`**
Return HTML string showing Layer 1 status:
- Create 3 boxes in grid (one per scope)
- Each box shows: scope name, ✅ EXIST or ❌ ABSENT
- Show overall count: "3/3 ACTIVE ✅"
- Use color: green if all active, red if any missing
Return as `<section class="layer1">...</section>`

**7. `_build_layer2_section(self, html_data: dict) -> str:`**
Return HTML string showing Layer 2 analysis:
- Current score (large, prominently displayed)
- Z-score breakdown (list each scope: ohlcv +0.42, funding -0.31, etc.)
- CPD info (penalty, window size, regime)
- Score history length indicator
Return as `<section class="layer2">...</section>`

**8. `_build_layer3_section(self, html_data: dict) -> str:`**
Return HTML string showing Layer 3 signal and trading parameters:
- SIGNAL box (very prominent, colored based on signal):
  - "LONG" → green, ▲ emoji
  - "SHORT" → red, ▼ emoji
  - "0" or "NEUTRAL" → gray, — emoji
- Trading parameters grid: Entry, SL, TP, Leverage, Risk/Reward Ratio
- Two scenario boxes side-by-side:
  - LONG scenario: show TP (green), Entry, SL (red) with arrows
  - SHORT scenario: show SL (red), Entry, TP (green) with arrows
- Leverage info: current leverage (colored), max from API
Return as `<section class="layer3">...</section>`

**9. `_build_score_history_section(self, html_data: dict) -> str:`**
Return HTML string showing score history:
- Take score_history and timestamps from html_data
- Create simple visualization:
  - Option A: ASCII bar chart (simple ▄▆ characters)
  - Option B: Simple text list of last 5 scores
  - Option C: Text trending arrows (→ ↗ ↑ ↖ ↓)
- Show at least last 5-10 scores with trend
Return as `<section class="history">...</section>`

**10. `_build_risk_metrics_section(self, html_data: dict) -> str:`**
Return HTML string showing risk analysis:
- Entry price
- Stop loss distance
- Take profit distance
- Risk/Reward ratio
- Max leverage (from exchange API)
- Current leverage (suggested)
Return as `<section class="risk">...</section>`

**11. `_build_footer_section(self) -> str:`**
Return HTML string with footer:
- System info: "Trading Truth Engine v1.0"
- Generated timestamp
- Simple disclaimer if needed
Return as `<footer>...</footer>`

### HTML Structure template:
```html
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trading Truth Engine Report</title>
  <style>
    /* Complete inline CSS here */
  </style>
</head>
<body>
  <div class="container">
    [header section]
    [layer1 section]
    [layer2 section]
    [layer3 section]
    [score history section]
    [risk metrics section]
    [footer section]
  </div>
</body>
</html>
```

### Important:
- NO external dependencies (no plotly, no matplotlib, just pure HTML/CSS)
- All CSS inline in <style> tag
- Self-contained single .html file
- Handle missing data gracefully (show "N/A" instead of crashing)
- Use color scheme consistently
- Mobile responsive

### Generate code for: _get_filename, render, save, and all helper methods listed above. Keep class definition and docstring as-is.

---

## 🔵 PROMPT 3: Integration with run_pipeline.py

**File to modify:** `scripts/run_pipeline.py`

**Copy everything below the "---" line:**

---

You need to integrate the HTML report generation into the existing run_pipeline() function in scripts/run_pipeline.py.

### Current situation:
The run_pipeline() function currently returns JSON like:
```python
return {
    "signal": "LONG",
    "entry_price": 1890.50,
    ...
}
```

And prints to console and logs via logger.

### What to do:

**At the END of run_pipeline(), AFTER Layer 3 completes and BEFORE the return statement, ADD:**

```python
# ===== GENERATE HTML REPORT (Optional, non-blocking) =====
try:
    from src.diagnostics.report_builder import ReportBuilder
    from src.diagnostics.html_renderer import HtmlReporter
    
    # Build HTML data structure
    builder = ReportBuilder()
    html_data = builder.build_from_pipeline_output(
        layer1_results=layer1_results,
        current_score=current_score,
        layer2_metadata=layer2_metadata,
        layer3_output=layer3_output,
        score_history=score_history,
        pair=pair,
        timeframe=timeframe,
        connector=connector.__class__.__name__.replace("Connector", "").lower()
    )
    
    # Render and save HTML
    reporter = HtmlReporter(output_dir="diagnostics/output")
    html_filepath = reporter.save(html_data)
    
    # Log that HTML was generated (use WARNING level so it always shows)
    logger.warning(f"HTML Report: {html_filepath}")
    
except Exception as e:
    # HTML generation is optional, don't crash pipeline if it fails
    logger.warning(f"HTML report generation failed (non-blocking): {str(e)}")

# ===== END HTML REPORT GENERATION =====

# Then return the original output as-is
return output
```

### Key points:
1. This code should be placed AFTER layer3_output is created
2. BEFORE the final `return output` statement
3. Use try/except so HTML generation failure doesn't crash the pipeline
4. Use logger.warning() so the message is visible (not debug level)
5. Extract connector name from connector object (GateioConnector → "gateio")
6. JSON output and logging behavior should remain UNCHANGED

### What variables should already exist at this point:
- layer1_results: dict from Layer 1
- current_score: float from Layer 2
- layer2_metadata: dict from Layer 2
- layer3_output: dict from Layer 3
- score_history: list from HistoryManager
- pair, timeframe: str params
- connector: the exchange connector object
- output: the final JSON dict to return

### Result:
- run_pipeline.py behavior unchanged
- JSON output still returned
- Logger messages still shown
- ADDITIONALLY: HTML file generated and saved to diagnostics/output/
- File naming: {SIGNAL}_{PAIR}_{TIMESTAMP}.html
- Visible logging: "HTML Report: diagnostics/output/LONG_ETH_USDT_2026-02-25_140000.html"

### Just provide the code block to insert (the part inside the try block and the try/except structure). No other changes needed.

---

## 📋 Summary

**3 Prompts total:**
1. PROMPT 1: ReportBuilder.build_from_pipeline_output() → Paste result into `src/diagnostics/report_builder.py`
2. PROMPT 2: HtmlReporter methods (_get_filename, render, save, and 7 helpers) → Paste result into `src/diagnostics/html_renderer.py`
3. PROMPT 3: Integration code snippet → Insert into `scripts/run_pipeline.py` before final return

**Order to do them:**
1. Do PROMPT 1 first (ReportBuilder)
2. Do PROMPT 2 second (HtmlReporter)
3. Do PROMPT 3 third (Integration)

Then test by running: `python scripts/run_pipeline.py` and checking `diagnostics/output/` folder for generated HTML.
