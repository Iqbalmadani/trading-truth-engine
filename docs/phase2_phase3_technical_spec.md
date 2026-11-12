# Phase 2 & Phase 3 — Technical Specification

**Tanggal:** 10 Juni 2026
**Status:** DRAFT — Menunggu approval sebelum eksekusi
**Prerequisite:** Phase 1 (Signal Logger) ✅ Selesai
**Referensi:** `docs/layer3_execution_scopes_blueprint.md`

---

## 0. Keputusan Fundamental Yang Sudah Ditetapkan

Sebelum masuk ke detail teknis, ini keputusan yang sudah clear dari diskusi:

| Pertanyaan | Jawaban |
|---|---|
| L3 Kategori B mengubah apa? | **ANGKA** — adjust SL/TP/leverage/confidence |
| L4 mengubah apa? | **KEPUTUSAN** — gate/filter berdasarkan modal, posisi, exposure |
| Mekanisme Kategori B? | **Adaptive, event-driven** — bukan batch retrospektif |
| Trigger kalibrasi? | **Per outcome** — setiap `update_signal_outcome()` dipanggil |
| Namespace isolation? | **Ya** — per (exchange, pair, timeframe), seperti Kategori A |

---

## 1. Pertanyaan Arsitektural: Dimana Kalibrasi B Jalan?

### Rekomendasi: Di `run_live.py`, BUKAN di `run_calibration.py`

**Alasan:**

| Aspek | Kategori A (run_calibration.py) | Kategori B (run_live.py) |
|---|---|---|
| **Trigger** | Setiap 24 candle baru (scheduled) | Setiap outcome baru masuk (event-driven) |
| **Komputasi** | Berat — PELT × 13 penalties × 3 models per scope | Ringan — SQL query + aritmatika pada 50-200 records |
| **Sumber data** | Data market (harga, OI, funding) | Data outcome dari `signal_history` table |
| **Kapan dibutuhkan** | Sebelum kalibrasi berikutnya | Sekarang, sebelum sinyal berikutnya dihasilkan |
| **Latency impact** | ~2 detik per scope (terlalu berat untuk LIVE) | ~50ms (query + math, negligible) |

**Mengapa bukan `run_calibration.py`:**
1. `run_calibration.py` di-trigger oleh `interval_candles: 24` — terlalu lambat untuk event-driven
2. Outcome bisa masuk kapan saja (saat `check_outcomes()` jalan di LIVE)
3. Menunggu calibration cycle berarti adjustment terlambat diterapkan

**Mengapa di `run_live.py`:**
1. `check_outcomes()` sudah jalan di `run_live.py` baris 186-198
2. Setelah outcome ter-resolve → recalibrate → adjustment langsung tersedia untuk sinyal berikutnya
3. Komputasi ringan (~50ms) tidak melanggar prinsip "LIVE harus cepat"

**Insertion point di pipeline (flow baru):**

```
run_live.py — run_live_single():

  L1 → L2 → score
      ↓
  check_outcomes()          ← sudah ada (baris 186)
      ↓ outcome baru masuk?
  [NEW] recalibrate_category_b()  ← BARU — hanya jika outcome baru + sample cukup
      ↓
  Load L3 penalties         ← sudah ada (baris 200)
  [NEW] Load Category B adjustments ← BARU
      ↓
  Layer 3 execution         ← sudah ada (baris 222), sekarang dengan adjustments
      ↓
  Log signal                ← sudah ada (baris 261)
```

---

## 2. Gap 1: Scope Loader

### Problem
`config/execution_scopes.yaml` ada tapi tidak ada kode yang membacanya.

### Solution
Tambah fungsi di `src/utils/config_loader.py` — mengikuti pattern existing.

### Interface

```python
# Di config_loader.py — tambah fungsi baru

def get_execution_scopes_config() -> dict:
    """Load execution_scopes.yaml — scope definitions Layer 3."""
    return load_config("execution_scopes.yaml")


def get_scope_min_signals(scope_name: str) -> int:
    """Get minimum signal threshold untuk scope tertentu."""
    config = get_execution_scopes_config()
    scopes = config.get("scopes", {})
    scope = scopes.get(scope_name, {})
    return scope.get("min_signals", 50)  # default 50 dari YAML
```

### Consumers
- `outcome_evaluator.py` (BARU) — baca `min_signals` untuk guard
- `executor.py` — baca scope definitions untuk tahu scope mana yang aktif

### Tidak berubah
- `sources.yaml` dan `get_sources_config()` — tidak tersentuh
- `infrastructure.yaml` — tidak tersentuh

---

## 3. Gap 2: Refactor `risk.py` — Scope-Aware

### Problem
`risk.py` menghitung SL/TP/leverage dengan formula tetap. Tidak ada mekanisme untuk menerapkan adjustment dari Kategori B.

### Keputusan Arsitektural

**`risk.py` TIDAK DIUBAH.** 

`risk.py` tetap menghitung raw values dari struktur market (regime boundaries, vol ratio). Adjustment dari Kategori B diterapkan di **`executor.py`** setelah menerima raw values.

**Alasan:**
1. `risk.py` adalah "Structural Analyzer" — tugasnya menemukan regime boundaries. Ini Kategori A.
2. Category B adjustment adalah lapisan terpisah di atas structural analysis.
3. Separation of concerns: `risk.py` = raw data → raw values, `executor.py` = raw values + adjustments → final values.
4. `risk.py` tetap bisa di-test secara independen tanpa calibration data.

### Mekanisme Adjustment di `executor.py`

Adjustment bekerja pada **jarak dari entry ke SL/TP**, bukan pada harga absolut:

```python
# SEKARANG (executor.py baris 174):
sl, tp = get_sl_tp(price_history, penalty_price, entry)
# sl dan tp langsung dipakai

# SESUDAH:
raw_sl, raw_tp = get_sl_tp(price_history, penalty_price, entry)

# Apply Category B adjustment (jika tersedia dan aktif)
sl = _apply_distance_adjustment(current_price, raw_sl, entry, cal.sl_multiplier)
tp = _apply_distance_adjustment(current_price, raw_tp, entry, cal.tp_multiplier)
```

**Fungsi adjustment:**

```python
def _apply_distance_adjustment(
    current_price: float, raw_level: float,
    direction: str, multiplier: float
) -> float:
    """
    Adjust SL atau TP berdasarkan multiplier dari Kategori B.
    
    Multiplier bekerja pada JARAK dari entry ke level, bukan harga absolut.
    Ini memastikan adjustment relatif terhadap posisi entry.
    
    multiplier = 1.0 → tidak ada perubahan (identik dengan formula lama)
    multiplier > 1.0 → jarak diperlebar (SL lebih jauh, TP lebih ambisius)
    multiplier < 1.0 → jarak dipersempit (SL lebih ketat, TP lebih konservatif)
    """
    distance = abs(current_price - raw_level)
    adjusted_distance = distance * multiplier
    
    if raw_level < current_price:
        # Level di bawah entry (SL untuk LONG, TP untuk SHORT)
        return current_price - adjusted_distance
    else:
        # Level di atas entry (SL untuk SHORT, TP untuk LONG)
        return current_price + adjusted_distance
```

**Contoh konkret:**

```
Entry price: $2250
Raw SL (min regime): $2200  → distance = $50
Raw TP (max regime): $2300  → distance = $50

Dengan sl_multiplier = 1.0 (default):
  SL = 2250 - 50 × 1.0 = $2200 (tidak berubah)

Dengan sl_multiplier = 1.3 (data bilang SL terlalu ketat):
  SL = 2250 - 50 × 1.3 = $2185 (SL 30% lebih jauh dari entry)

Dengan tp_multiplier = 0.8 (data bilang TP terlalu ambisius):
  TP = 2250 + 50 × 0.8 = $2290 (TP 20% lebih dekat ke entry)
```

**Untuk leverage:**

```python
# SEKARANG (executor.py baris 195):
leverage = get_leverage(price_history, penalty_price, exchange_leverage_max)

# SESUDAH:
raw_leverage = get_leverage(price_history, penalty_price, exchange_leverage_max)
leverage = raw_leverage * cal.leverage_multiplier
leverage = np.clip(leverage, 1.0, exchange_leverage_max)  # exchange cap tetap berlaku
```

### Geometry Validation

Setelah adjustment, geometry validation tetap berlaku (entry harus di antara SL dan TP). Jika adjustment menyebabkan geometry invalid → sinyal jadi neutral. Ini adalah guard otomatis — adjustment yang terlalu ekstrem akan ditolak oleh data.

---

## 4. Gap 3: Refactor `executor.py` — Scope-Aware

### Problem
`executor.py` tidak menerima atau menerapkan calibration data dari Kategori B.

### Perubahan pada `layer3()` function

**Parameter baru:**

```python
def layer3(
    current_score: float,
    score_history: List[float],
    current_price: float,
    price_history: List[float],
    penalty_score: float,
    penalty_price: float,
    exchange_leverage_max: float,
    score_model: str = "rbf",
    price_model: str = "rbf",
    # === BARU: Category B calibration ===
    calibration: Optional[Dict] = None,
) -> Dict[str, Any]:
```

**Parameter `calibration` dict:**

```python
calibration = {
    "sl_multiplier": 1.0,       # dari sl_quality evaluator
    "tp_multiplier": 1.0,       # dari tp_quality evaluator
    "leverage_multiplier": 1.0, # dari leverage_quality evaluator
    "confidence": None,         # dari signal_confidence evaluator
    "source": "default",        # "default" atau "outcome_calibration"
}
```

Jika `calibration = None` → semua multiplier = 1.0 (behavior identik dengan sekarang). **Backward compatible sepenuhnya.**

### Signal Output Baru (untuk L4)

```python
signal = {
    # === Existing (tidak berubah) ===
    "entry": "LONG" | "SHORT" | "0",
    "entry_price": float,
    "stop_loss": float,           # sekarang sudah di-adjust oleh sl_multiplier
    "take_profit": float,         # sekarang sudah di-adjust oleh tp_multiplier
    "leverage": float,            # sekarang sudah di-adjust oleh leverage_multiplier
    "leverage_max_from_api": float,
    "risk_reward_ratio": float,
    
    # === BARU: untuk L4 ===
    "confidence": float | None,   # dari signal_confidence scope
    
    "metadata": {
        # === Existing ===
        "threshold_up": float,
        "threshold_down": float,
        "score": float,
        "score_z": float,
        "price": float,
        "regime_volatility": float,
        
        # === BARU: transparansi kalibrasi ===
        "calibration": {
            "sl_multiplier": float,
            "tp_multiplier": float,
            "leverage_multiplier": float,
            "confidence": float | None,
            "source": "default" | "outcome_calibration",
            "raw_sl": float,    # SL sebelum adjustment (untuk audit)
            "raw_tp": float,    # TP sebelum adjustment (untuk audit)
            "raw_leverage": float,
        }
    }
}
```

**Mengapa `raw_*` disimpan di metadata:**
- Transparansi: bisa audit berapa adjustment yang diterapkan
- Debugging: jika adjustment menghasilkan sinyal yang buruk, bisa bandingkan raw vs adjusted
- L4 bisa melihat keduanya dan membuat keputusan yang lebih informed

---

## 5. Gap 4: Evaluator Output Schema

### Problem
Tidak ada definisi format output dari evaluator Kategori B.

### Arsitektur: File Baru `src/layer3/outcome_evaluator.py`

**Mengapa file baru (bukan modifikasi `meta_learning.py`):**

| Aspek | Kategori A (`meta_learning.py`) | Kategori B (`outcome_evaluator.py`) |
|---|---|---|
| Input | Time-series data (harga, score) | Resolved signals dari DB |
| Algoritma | PELT + CH (segmentation quality) | Expected Value optimization |
| Output | Penalty float + model string | Multiplier float |
| Komputasi | Berat (PELT O(n²)) | Ringan (SQL + math) |
| Trigger | Scheduled (every 24 candles) | Event-driven (per outcome) |

Dua mekanisme yang secara fundamental berbeda → dua file terpisah.

### Schema Output Per Scope

#### `sl_quality` → `sl_multiplier: float`

```python
def evaluate_sl_quality(
    exchange: str, pair: str, timeframe: str
) -> Dict:
    """
    Evaluasi efektivitas penempatan SL dari outcome data.
    
    Returns:
        {
            "multiplier": float,     # 1.0 = no change, >1 = wider, <1 = tighter
            "quality_score": float,  # seberapa reliable estimate ini
            "sample_size": int,      # jumlah resolved signals yang dipakai
            "sl_hit_rate": float,    # % sinyal yang hit SL
            "active": bool,         # apakah sample_size >= min_signals
        }
    """
```

#### `tp_quality` → `tp_multiplier: float`

```python
def evaluate_tp_quality(
    exchange: str, pair: str, timeframe: str
) -> Dict:
    """
    Returns:
        {
            "multiplier": float,     # 1.0 = no change, >1 = more ambitious, <1 = closer
            "quality_score": float,
            "sample_size": int,
            "tp_hit_rate": float,    # % sinyal yang hit TP
            "active": bool,
        }
    """
```

#### `leverage_quality` → `leverage_multiplier: float`

```python
def evaluate_leverage_quality(
    exchange: str, pair: str, timeframe: str
) -> Dict:
    """
    Returns:
        {
            "multiplier": float,     # 1.0 = no change, >1 = more aggressive, <1 = more conservative
            "quality_score": float,
            "sample_size": int,
            "avg_pnl_per_unit_leverage": float,
            "active": bool,
        }
    """
```

#### `signal_confidence` → `confidence: float`

```python
def evaluate_signal_confidence(
    exchange: str, pair: str, timeframe: str
) -> Dict:
    """
    Berbeda dari 3 scope di atas:
    - sl/tp/leverage menghasilkan MULTIPLIER yang mengubah formula
    - signal_confidence menghasilkan SCORE yang menjadi metadata
    
    Confidence score dipakai untuk:
    - L3: dimasukkan ke signal output sebagai field "confidence"
    - L4: digunakan sebagai input untuk gate/filter decisions
    
    Returns:
        {
            "confidence": float,     # 0.0-1.0, seberapa reliable sinyal dari combo ini
            "quality_score": float,
            "sample_size": int,
            "win_rate": float,       # overall win rate
            "active": bool,
        }
    """
```

### Wrapper: Evaluate All

```python
def evaluate_all_category_b(
    exchange: str, pair: str, timeframe: str
) -> Dict:
    """
    Jalankan semua evaluator Kategori B untuk satu combo.
    Hanya evaluator yang active (sample >= min_signals) yang menghasilkan adjustment.
    Yang tidak active → multiplier = 1.0 (default, tidak berubah).
    
    Returns:
        {
            "sl_multiplier": float,
            "tp_multiplier": float,
            "leverage_multiplier": float,
            "confidence": float | None,
            "source": "outcome_calibration" | "default",
            "details": {
                "sl_quality": { full evaluator output },
                "tp_quality": { full evaluator output },
                "leverage_quality": { full evaluator output },
                "signal_confidence": { full evaluator output },
            }
        }
    """
```

---

## 6. Gap 5: Override Mechanism (Storage)

### Problem
Tidak ada tempat untuk menyimpan adjustment dari evaluator Kategori B.

### Solution: Tabel Baru `execution_adjustments` di SQLite

```sql
CREATE TABLE IF NOT EXISTS execution_adjustments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange        TEXT NOT NULL,
    pair            TEXT NOT NULL,
    timeframe       TEXT NOT NULL,
    scope           TEXT NOT NULL,     -- 'sl_quality', 'tp_quality', dll.
    adjustment      REAL NOT NULL,     -- multiplier atau confidence value
    quality_score   REAL,              -- reliability estimate
    sample_size     INTEGER NOT NULL,  -- berapa sinyal dipakai
    updated_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_adjustment_namespace
    ON execution_adjustments(exchange, pair, timeframe, scope);
```

**Mengapa tabel baru (bukan extend `penalties`):**
- Schema berbeda: penalties punya `layer` + `model`, adjustments punya `quality_score` + `sample_size`
- Concern berbeda: penalties = parameter PELT, adjustments = multiplier dari outcome
- Lifecycle berbeda: penalties di-update oleh CALIBRATION process, adjustments oleh LIVE process

**Mengapa BUKAN YAML update:**
- YAML files di-edit manusia, DB di-manage sistem
- Namespace isolation natural di SQLite (query by exchange/pair/timeframe)
- Concurrent access aman (WAL mode sudah aktif)

### CRUD Functions di `database.py`

```python
def save_adjustment(
    exchange: str, pair: str, timeframe: str,
    scope: str, adjustment: float,
    quality_score: float, sample_size: int
) -> None:
    """Save/update adjustment — INSERT OR REPLACE per namespace+scope."""

def get_adjustment(
    exchange: str, pair: str, timeframe: str, scope: str
) -> Optional[Dict]:
    """Get adjustment untuk satu scope. Returns None jika belum ada."""

def get_all_adjustments(
    exchange: str, pair: str, timeframe: str
) -> Dict[str, Dict]:
    """Get semua adjustments untuk combo. Returns {scope: {adjustment, quality_score, ...}}"""
```

Pattern identik dengan `save_penalty()` / `get_penalty()` / `get_all_penalties()` yang sudah ada.

---

## 7. Gap 6: Fallback Logic

### Problem
Apa yang terjadi kalau sinyal belum mencapai threshold `min_signals: 50`?

### Jawaban: Multiplier = 1.0 (Formula Lama Otomatis Berlaku)

**Mekanisme:**

```python
def evaluate_all_category_b(exchange, pair, timeframe) -> Dict:
    # Untuk setiap scope:
    config = get_execution_scopes_config()
    min_signals = config["scopes"]["sl_quality"]["min_signals"]  # 50
    
    resolved_count = db.get_resolved_signal_count(exchange, pair, timeframe)
    
    if resolved_count < min_signals:
        # Belum cukup data → semua multiplier default
        return {
            "sl_multiplier": 1.0,
            "tp_multiplier": 1.0,
            "leverage_multiplier": 1.0,
            "confidence": None,
            "source": "default",  # transparent: ini bukan dari kalibrasi
        }
    
    # Cukup data → jalankan evaluator
    # ...
```

**Transisi dari default ke calibrated:**
- **Otomatis**: sistem cek count setiap kali `evaluate_all_category_b()` dipanggil
- **Tidak ada manual switch**: begitu 50 sinyal tercapai → evaluator aktif
- **Per-scope independent**: jika combo A sudah punya 60 sinyal dan combo B baru 20, hanya combo A yang dikalibrasi
- **Transparent**: `source` field di output memberitahu apakah adjustment dari kalibrasi atau default

**Tidak ada "gradual transition":**
- Saat sinyal ke-50 masuk → evaluator langsung aktif
- Ini konsisten dengan Kategori A: begitu meta-learning jalan, penalty langsung berlaku
- Risiko minimal karena multiplier biasanya mendekati 1.0 saat pertama aktif (belum cukup evidence untuk adjustment besar)

---

## 8. Gap 7: Integrasi ke Pipeline

### Problem
Bagaimana evaluator terhubung ke pipeline yang sudah ada.

### File-file yang Terlibat

```
BARU:
  src/layer3/outcome_evaluator.py   ← evaluator Kategori B (4 scope)

DIMODIFIKASI:
  src/utils/config_loader.py        ← tambah get_execution_scopes_config()
  src/utils/database.py             ← tambah tabel execution_adjustments + CRUD
  src/layer3/executor.py            ← terima calibration param, apply adjustments
  src/layer3/__init__.py            ← export baru
  scripts/run_live.py               ← trigger recalibration setelah outcomes

TIDAK BERUBAH:
  src/layer3/risk.py                ← tetap return raw regime values
  src/layer3/thresholds.py          ← tidak tersentuh
  src/layer3/meta_learning.py       ← Kategori A, tidak tersentuh
  src/layer3/penalty_manager.py     ← Kategori A, tidak tersentuh
  src/layer3/signal_logger.py       ← tidak tersentuh
  scripts/run_calibration.py        ← Kategori A, tidak tersentuh
  config/execution_scopes.yaml      ← tidak berubah (sudah benar)
```

### Flow Lengkap di `run_live.py` (Setelah Perubahan)

```python
def run_live_single(exchange, pair, timeframe, ...):
    # ... L1 → L2 → score (tidak berubah) ...

    # ===== SIGNAL OUTCOME CHECK ===== (sudah ada, baris 186)
    outcome_stats = check_outcomes(exchange, pair, timeframe, ...)
    
    # ===== [BARU] CATEGORY B RECALIBRATION =====
    # Hanya jalan jika ada outcome baru yang ter-resolve
    calibration = None
    if outcome_stats.get("tp_hit", 0) + outcome_stats.get("sl_hit", 0) > 0:
        try:
            from src.layer3.outcome_evaluator import evaluate_all_category_b
            calibration = evaluate_all_category_b(exchange, pair, timeframe)
            
            if calibration["source"] == "outcome_calibration":
                # Simpan adjustments ke SQLite
                _save_category_b_adjustments(exchange, pair, timeframe, calibration)
                logger.info(f"[{combo_id}] Category B recalibrated")
        except Exception as e:
            logger.warning(f"[{combo_id}] Category B recalibration failed: {e}")
    
    # Jika tidak ada outcome baru, load adjustments dari SQLite (cached)
    if calibration is None:
        calibration = _load_cached_adjustments(exchange, pair, timeframe)
    
    # ===== LAYER 3 EXECUTION ===== (dimodifikasi)
    layer3_output = layer3(
        current_score=current_score,
        score_history=score_history,
        current_price=current_price,
        price_history=price_history,
        penalty_score=penalty_score,
        penalty_price=penalty_price,
        exchange_leverage_max=exchange_leverage_max,
        score_model=score_model,
        price_model=price_model,
        calibration=calibration,  # ← BARU
    )
```

### Trigger Logic Detail

```
run_live.py dipanggil setiap candle close
    ↓
check_outcomes() memeriksa sinyal PENDING
    ↓
Ada outcome baru (TP_HIT atau SL_HIT)?
    ├── TIDAK → load cached adjustments dari SQLite → lanjut
    └── YA → evaluate_all_category_b()
                ├── sample < 50 → return default (multiplier = 1.0)
                └── sample >= 50 → hitung adjustments dari ALL resolved signals
                                   → simpan ke SQLite
                                   → lanjut dengan adjustments baru
    ↓
layer3() dijalankan dengan calibration dict
    ↓
Signal output sekarang berisi confidence + calibration metadata
```

**Penting:** Evaluator selalu menghitung dari SEMUA resolved signals (bukan hanya yang baru). Ini sama dengan Kategori A yang selalu menghitung dari semua data dalam window. Setiap trigger = recompute penuh. Hasilnya konvergen seiring data bertambah.

---

## 9. Algoritma Evaluator Kategori B — Prinsip

### Filosofi: Mirror Kategori A

Kategori A:
```
Generate candidates (BIC-scaled)  →  Evaluate each (PELT+CH)  →  Select best
```

Kategori B:
```
Generate candidates (data-scaled)  →  Evaluate each (EV metric)  →  Select best
```

### Quality Metric: Expected Value Ratio (EVR)

Analogi CH untuk Kategori B:

```
CH (Kategori A) = between_var × (n-k) / (within_var × (k-1))
  → Mengukur: "seberapa baik segmentasi ini memisahkan data?"
  → Numerator: perbedaan antar segmen (yang diinginkan tinggi)
  → Denominator: variasi dalam segmen (yang diinginkan rendah)

EVR (Kategori B) = total_profit × n_wins / (total_loss × n_losses + ε)
  → Mengukur: "seberapa baik parameter ini menghasilkan profit?"
  → Numerator: total profit × frekuensi win (yang diinginkan tinggi)
  → Denominator: total loss × frekuensi loss (yang diinginkan rendah)
```

EVR adalah rasio yang secara natural menghukum parameter yang menghasilkan banyak loss besar, dan mereward parameter yang menghasilkan banyak win. Ini bukan metrik manusia — ini F-ratio analog yang diterapkan pada P&L, bukan pada variance.

### Proses Per Scope (Contoh: `sl_quality`)

```
Input: semua resolved signals untuk combo ini

Step 1: Generate SL multiplier candidates
  - Ambil distribusi sl_distance dari resolved signals
  - Hitung: mean_distance, std_distance
  - Candidates = mean ± logarithmic range (seperti BIC candidates di Kategori A)
  - Contoh: [0.5, 0.7, 0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2, 1.3, 1.5, 2.0]
  - Range di-generate dari data, bukan hardcoded

Step 2: Untuk setiap candidate multiplier
  - Estimasi P&L jika multiplier ini dipakai:
    - SL_HIT signals: adjusted_pnl = original_pnl × multiplier
      (SL lebih jauh = loss lebih besar per hit)
    - TP_HIT signals: pnl tidak berubah
      (TP tidak terpengaruh oleh SL adjustment)
  - Hitung EVR untuk konfigurasi ini

Step 3: Pilih multiplier dengan EVR tertinggi

Step 4: Simpan ke execution_adjustments table
```

> **Limitasi yang disadari:** Tanpa data full price path, kita tidak bisa tahu apakah SL_HIT signal akan menjadi TP_HIT jika SL-nya lebih lebar. Estimasi di atas konservatif — ia mengasumsikan SL_HIT tetap SL_HIT dengan adjusted loss. Ini adalah lower-bound estimate yang aman: jika multiplier tetap menghasilkan EVR tinggi bahkan dengan estimasi konservatif ini, maka secara real-world hasilnya kemungkinan lebih baik.
>
> Setelah L4 aktif dan monitoring real-time tersedia, estimasi ini bisa diperbaiki dengan data price path yang lebih lengkap.

### Candidate Generation (Data-Scaled)

Sama seperti Kategori A menggunakan BIC (`2σ²ln(n)`) sebagai center point:

```python
def generate_multiplier_candidates(resolved_signals: List[Dict]) -> List[float]:
    """
    Generate multiplier candidates scaled to data.
    Center: 1.0 (current formula)
    Range: scaled by variance of observed P&L distribution
    """
    pnl_values = [s["pnl_percent"] for s in resolved_signals]
    pnl_std = np.std(pnl_values)
    
    # Sensitivity scaled by data variance
    # High P&L variance → wider search range
    # Low P&L variance → narrower search range
    scale = min(1.0, pnl_std / 10.0)  # normalized
    
    # Logarithmic spacing around 1.0
    lower = max(0.3, 1.0 - scale * 2)
    upper = min(3.0, 1.0 + scale * 2)
    candidates = np.logspace(np.log10(lower), np.log10(upper), 13).tolist()
    
    # Ensure 1.0 is always a candidate (baseline comparison)
    if 1.0 not in candidates:
        candidates.append(1.0)
        candidates.sort()
    
    return candidates
```

---

## 10. Impact Pada L4 (Awareness)

L4 akan mengonsumsi output L3 yang sudah di-adjust oleh Kategori B. Berikut bagaimana output ini digunakan:

```
L3 output (setelah Phase 2/3):
  {
    entry: "LONG",
    entry_price: 2250.00,
    stop_loss: 2185.00,       ← sudah di-adjust (raw: 2200)
    take_profit: 2290.00,     ← sudah di-adjust (raw: 2300)
    leverage: 1.85,           ← sudah di-adjust (raw: 2.0)
    confidence: 0.72,         ← BARU: dari signal_confidence
    metadata.calibration: {   ← BARU: transparansi
      sl_multiplier: 1.3,
      tp_multiplier: 0.8,
      ...
    }
  }

L4 input (masa depan):
  - Signal di atas (entry, SL, TP, leverage, confidence)
  - Modal available (dari exchange API)
  - Posisi open (dari exchange API)
  - Total exposure
  
L4 decisions:
  - confidence < threshold → skip signal
  - exposure terlalu tinggi → reduce size atau skip
  - modal tidak cukup → skip
  - korelasi dengan posisi existing → reduce size
  - Semua OK → eksekusi order ke exchange
```

**Penting:** L3 tidak perlu tahu tentang L4. L3 menghasilkan signal + confidence + adjustments. L4 mengonsumsi itu sebagai input. Decoupled sepenuhnya.

---

## 11. Perubahan YAML Config

### `config/execution_scopes.yaml` — TIDAK BERUBAH

File ini sudah benar. Field `status` di YAML menjadi **dokumentasi**, bukan state aktif. Status aktual ditentukan at runtime oleh `resolved_count >= min_signals`.

### `config/infrastructure.yaml` — TIDAK BERUBAH

Kategori B tidak butuh parameter infrastruktur tambahan. Trigger-nya event-driven, bukan interval-based.

---

## 12. Urutan Eksekusi (Implementation Order)

### Phase 2: Scope Definition + Refactor

| Step | File | Perubahan | Dependensi |
|---|---|---|---|
| 2.1 | `src/utils/config_loader.py` | Tambah `get_execution_scopes_config()` | Tidak ada |
| 2.2 | `src/utils/database.py` | Tambah tabel `execution_adjustments` + CRUD | Tidak ada |
| 2.3 | `src/layer3/executor.py` | Tambah parameter `calibration`, apply adjustments, output `confidence` | 2.1 |
| 2.4 | `src/layer3/__init__.py` | Update exports | 2.3 |

**Verifikasi Phase 2:**
- `run_live.py --once` tetap jalan normal (calibration = None → semua multiplier 1.0)
- Output signal identik dengan sebelum perubahan
- Tidak ada regression

### Phase 3: Outcome Evaluators

| Step | File | Perubahan | Dependensi |
|---|---|---|---|
| 3.1 | `src/layer3/outcome_evaluator.py` | Buat file baru — 4 evaluator + wrapper | 2.2 |
| 3.2 | `scripts/run_live.py` | Integrasikan recalibration trigger setelah `check_outcomes()` | 2.3, 3.1 |
| 3.3 | `src/layer3/__init__.py` | Export evaluator functions | 3.1 |

**Verifikasi Phase 3:**
- Dengan < 50 sinyal: output identik (semua multiplier 1.0, source = "default")
- Dengan >= 50 sinyal: adjustments diterapkan, metadata berisi calibration details
- Log menunjukkan "Category B recalibrated" saat outcome baru masuk

---

## 13. Verification Plan

### Automated (Setelah Implementasi)

```powershell
# Test 1: Backward compatibility — tidak ada sinyal resolved
python scripts/run_live.py --once
# Expected: identik dengan sebelum perubahan, calibration.source = "default"

# Test 2: Schema migration
python -c "from src.utils.database import init_schema; print('OK')"
# Expected: tabel execution_adjustments dibuat tanpa error
```

### Manual

1. **Inspect signal output** — field baru (`confidence`, `metadata.calibration`) ada
2. **Inspect database** — tabel `execution_adjustments` kosong (belum ada data)
3. **Simulate 50+ signals** — insert dummy resolved signals, verify evaluator aktif
4. **Compare raw vs adjusted** — cek `raw_sl` vs `stop_loss` di metadata

---

## 14. Risiko dan Mitigasi

| Risiko | Severity | Mitigasi |
|---|---|---|
| EVR metric tidak konvergen dengan data sedikit | Medium | Min 50 signals guard + quality_score tracking |
| Adjustment terlalu ekstrem | Low | Geometry validation tetap berlaku → sinyal invalid = neutral |
| Backward incompatibility | Low | `calibration=None` → multiplier 1.0 → behavior identik |
| Price path limitation (counterfactual) | Medium | Estimasi konservatif + akan diperbaiki saat L4 aktif |
| Race condition LIVE write adjustments | Low | SQLite WAL mode + INSERT OR REPLACE (atomic) |

---

## 15. Ringkasan Arsitektur

```
┌─────────────────────────────────────────────────────────────┐
│                    KATEGORI A (existing)                     │
│  Trigger: every 24 candles (run_calibration.py)             │
│  Input: market data (harga, score)                          │
│  Algorithm: PELT + CH                                       │
│  Output: penalty + model → SQLite penalties table           │
│  Consumed by: thresholds.py, risk.py                        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    KATEGORI B (new)                          │
│  Trigger: every new outcome (run_live.py)                   │
│  Input: resolved signals from signal_history                │
│  Algorithm: EVR optimization (mirror CH pattern)            │
│  Output: multipliers + confidence → SQLite adjustments table│
│  Consumed by: executor.py → signal output → L4              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    LAYER 4 (future)                          │
│  Input: signal from L3 (with confidence + adjusted values)  │
│         + modal, posisi, exposure dari exchange API          │
│  Logic: gate/filter decisions                               │
│  Output: executed order OR reject with reason               │
│  Feedback: outcome data → signal_history → triggers Kat. B  │
└─────────────────────────────────────────────────────────────┘
```

**Data flow:**
```
Market data → L1 → L2 → score
                          ↓
              Kat. A penalties (from CALIBRATION)
                          ↓
                    L3 executor ← Kat. B adjustments (from LIVE outcome)
                          ↓
              Signal + confidence + calibration metadata
                          ↓
                    L4 (future) → execute or skip
                          ↓
                    Outcome → signal_history → trigger Kat. B recalibration
```
