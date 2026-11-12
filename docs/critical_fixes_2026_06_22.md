# Critical Fixes — 22 Juni 2026

> **Auditor:** Claude Opus 4 (Thinking)  
> **Tanggal:** 2026-06-22  
> **Status:** 3/3 CRITICAL fixes diterapkan  

---

## Ringkasan

Tiga cacat logika fundamental ditemukan dan diperbaiki dalam satu sesi:

| # | Nama | Severity | File | Status |
|---|------|----------|------|--------|
| 1 | Paradoks Breakout | 🔴 CRITICAL | `src/layer3/risk.py` | ✅ Fixed |
| 2 | Threshold Monoton | 🔴 CRITICAL | `src/layer3/thresholds.py` | ✅ Fixed |
| 3 | Survival Bias Kategori B | 🔴 CRITICAL | `src/layer3/outcome_evaluator.py` | ✅ Fixed |

---

## Fix 1: Paradoks Breakout (risk.py)

### Masalah
Fungsi `get_sl_tp()` menggunakan `max(regime)` dan `min(regime)` sebagai **level absolut** SL/TP.

Saat tren naik kuat, harga terbaru (`entry_price`) berada di dekat `max(regime)`, sehingga:
- `TP = max(regime) ≈ entry_price` → jarak TP ≈ 0
- Validasi geometri di executor.py mendeteksi `entry >= TP` → **sinyal ditolak**

**Dampak:** TTE secara sistematis membuang SEMUA peluang saat tren kuat — kondisi paling menguntungkan untuk trading.

### Solusi
Ubah SL/TP dari **level absolut** menjadi **jarak proporsional** menggunakan regime width:

```python
# SEBELUM (buggy):
sl = natural_low     # Level absolut
tp = natural_high    # Level absolut — bisa ≈ entry saat breakout!

# SESUDAH (fixed):
regime_range = natural_high - natural_low
sl = entry_price - regime_range   # Selalu di bawah entry untuk LONG
tp = entry_price + regime_range   # Selalu di atas entry untuk LONG
```

### Mengapa ini benar
- `regime_range` tetap data-driven (dari PELT CPD)
- SL/TP selalu valid secara geometri (entry selalu di antara keduanya)
- Jarak SL = jarak TP = regime width → risk:reward selalu 1:1 di baseline
- Kategori B multiplier tetap bisa menyesuaikan rasio setelahnya
- Tidak ada hardcoded multiplier — semua dari data

### Perubahan detail
- File: `src/layer3/risk.py`, fungsi `get_sl_tp()`
- Baris 75-121 (original) → 75-129 (baru)
- Validasi geometri lama (`tp <= entry_price`) dihapus karena tidak relevan lagi
- Ditambah safety check: SL/TP tidak boleh ≤ 0 (regime terlalu volatile)

---

## Fix 2: Threshold Monoton (thresholds.py)

### Masalah
Fungsi `get_score_thresholds()` menggunakan `max()` dan `min()` dari **regime saat ini** sebagai threshold:

```python
threshold_up = max(historical_scores_in_current_regime)
threshold_down = min(historical_scores_in_current_regime)
```

**Masalah:** Setiap kali skor baru melampaui threshold → skor itu menjadi bagian dari regime → threshold NAIK → semakin sulit melampaui di masa depan.

Ini seperti **lari sprint yang harus memecahkan rekor dunia setiap kali berlari**. Semakin baik performa TTE, semakin mustahil baginya menghasilkan sinyal baru.

**Dampak:** Setelah beberapa minggu operasi, TTE diam-diam berhenti menghasilkan sinyal — "silent death". Log hanya menunjukkan `Entry is 0 (neutral)` tanpa error.

### Solusi
Gunakan **regime SEBELUMNYA** (bukan regime saat ini) sebagai reference frame:

```python
# SEBELUM (buggy):
# Threshold dari regime SAAT INI — terus naik monoton
historical_scores = score_history[last_cp:-1]
threshold_up = max(historical_scores)

# SESUDAH (fixed):
# Threshold dari regime SEBELUMNYA — reset setiap regime shift
if len(change_points) > 2:
    reference_scores = score_history[prev_cp:last_cp]  # Regime sebelumnya
elif last_cp > 0:
    reference_scores = score_history[:last_cp]          # Semua sebelum regime ini
else:
    reference_scores = score_history[:-1]               # Semua minus current

threshold_up = max(reference_scores)
threshold_down = min(reference_scores)
```

### Mengapa ini benar
- Skor yang melampaui regime lama = genuine breakout → layak menjadi sinyal
- Saat PELT mendeteksi regime baru, "lama" berubah → threshold di-reset
- Tidak ada kenaikan monoton karena reference frame berubah setiap regime shift
- Tetap 100% data-driven — PELT menentukan batas regime

### Perubahan detail
- File: `src/layer3/thresholds.py`, fungsi `get_score_thresholds()`
- Baris 82-115 (original) → 82-134 (baru)
- Logika bercabang berdasarkan jumlah change points (0, 1, ≥2)
- Fallback ke `score_history[:-1]` jika regime sebelumnya terlalu pendek

---

## Fix 3: Survival Bias Kategori B (outcome_evaluator.py)

### Masalah
Fungsi `evaluate_sl_quality()` dan `evaluate_tp_quality()` menyimulasikan dampak multiplier SL/TP pada P&L historis, tetapi mengasumsikan **status trade tidak berubah**:

```python
# BUGGY: Asumsi TP_HIT tetap TP_HIT meskipun SL dipersempit
if outcome == "SL_HIT":
    adjusted_pnl.append(pnl * mult)  # Skalakan kerugian
else:
    adjusted_pnl.append(pnl)          # Kemenangan tetap menang ← SALAH!
```

**Masalah matematis:** Jika SL dipersempit 50% (mult=0.5):
- Kerugian per SL_HIT tampak **50% lebih kecil** ✓
- Tapi banyak trade TP_HIT yang seharusnya **terkena SL baru** (karena harga sempat turun ke level SL baru sebelum naik ke TP) → **tidak diperhitungkan** ✗

**Dampak:** Optimizer selalu melihat SL ketat = lebih baik → setelah 50 sinyal, SL menjadi super ketat → di dunia nyata terkena noise terus-menerus → akun habis.

### Solusi
Tambahkan **flip probability** konservatif tanpa memerlukan data MFE/MAE:

#### Untuk SL evaluator:
```python
# Jika SL dipersempit (mult < 1.0):
flip_prob = 1.0 - mult     # Semakin ketat, semakin besar peluang flip
flipped_loss = avg_sl_loss * mult  # Kerugian proporsional ke SL baru
expected_pnl = pnl * (1.0 - flip_prob) + flipped_loss * flip_prob
```

#### Untuk TP evaluator:
```python
# Jika TP diperlebar (mult > 1.0):
flip_prob = 1.0 - (1.0 / mult)  # Semakin jauh, semakin besar peluang gagal
expected_pnl = (pnl * mult) * (1.0 - flip_prob) + avg_sl_loss * flip_prob
```

### Mengapa flip probability linear?
- `flip_prob = 1 - mult` untuk SL: SL 50% lebih ketat → ~50% trade TP_HIT akan flip
- `flip_prob = 1 - 1/mult` untuk TP: TP 2× lebih jauh → ~50% trade gagal sampai
- Model linear adalah **estimasi konservatif** tanpa data MFE/MAE
- Optimal yang sebenarnya: simpan MAE (Max Adverse Excursion) per trade di masa depan

### Perubahan detail
- File: `src/layer3/outcome_evaluator.py`
- Fungsi `evaluate_sl_quality()`: baris 174-203 → diperluas dengan flip logic
- Fungsi `evaluate_tp_quality()`: baris 254-281 → diperluas dengan flip logic
- Precompute `avg_sl_loss` di kedua fungsi untuk estimasi kerugian saat flip
- `evaluate_leverage_quality()` **tidak diubah** — leverage scaling ALL P&L sudah benar

---

## Isu yang Belum Diperbaiki (Prioritas Rendah)

Berikut isu-isu yang ditemukan dalam audit tetapi belum diperbaiki karena severity lebih rendah:

| Priority | Issue | File | Catatan |
|----------|-------|------|---------|
| 🟠 P2 | Outcome check hanya candle terakhir | `signal_logger.py` | Sinyal PENDING selamanya jika VPS restart |
| 🟠 P2 | Leverage bias (vol ratio asimetris) | `risk.py` | Over-leverage sistematis |
| 🟠 P2 | Z-score L2 includes current value | `aggregator.py` | Under-estimate extremity |
| 🟠 P2 | Deduplikasi snapshot distorsi temporal | `database.py` | PELT salah deteksi regime |
| 🟡 P3 | Tidak ada signal expiry | `signal_logger.py` | Akumulasi PENDING tak terbatas |
| 🟡 P3 | Duplikasi meta_learning L2/L3 | Kedua meta_learning.py | Bug divergence risk |
| 🟡 P3 | Database connection pattern | `database.py` | Potential lock di VPS |

---

## Rekomendasi Post-Fix

1. **Jalankan kalibrasi ulang** setelah fix diterapkan:
   ```bash
   python scripts/run_calibration.py --once
   ```

2. **Jalankan backtest** untuk memvalidasi bahwa fix menghasilkan perilaku yang lebih baik

3. **Monitor log** selama beberapa hari pertama di VPS untuk memastikan:
   - Sinyal LONG/SHORT benar-benar muncul (tidak hanya neutral)
   - SL/TP memiliki jarak yang wajar dari entry
   - Threshold tidak terus naik

4. **Perbaiki P2 issues** setelah P0/P1 tervalidasi
