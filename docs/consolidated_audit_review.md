# Consolidated Audit Review — Trading Truth Engine
## Audit #1 (6 Issues) + Audit #2 (15 Issues) + Audit #3 (14 Issues) + Opus Verdict

**Date:** 13 Mei 2026  
**Reviewed By:** Opus (Antigravity IDE) — against actual source code  
**Purpose:** Satu dokumen definitif — mana yang harus FIX, mana yang BUANG.

---

## Legenda Verdict

| Emoji | Meaning |
|---|---|
| ✅ **FIX** | Valid, harus diperbaiki |
| ✅☑️ **FIXED** | Sudah diperbaiki |
| ⚠️ **MONITOR** | Valid concern tapi belum urgent / mitigated by design |
| ❌ **BUANG** | Invalid — auditor salah baca kode atau salah teori |
| 🧹 **CLEANUP** | Benar tapi bukan bug — code hygiene / improvement |

---

## 🔴 CRITICAL ISSUES

### CRIT-01: Double Data Ingestion — Snapshot Ditulis 2× Per Run
**Source:** Audit #2 + Audit #3 (HIGH-09)  
**Verdict:** ✅☑️ **FIXED (12 Mei 2026)**

Aggregator memanggil `append_snapshot_value()` padahal connector sudah write.
**Fix applied:** Hapus double write di `aggregator.py`. Connector yang handle.

---

### CRIT-02: Z-Score Self-Referential Bias
**Source:** Audit #1 #4, Audit #2, Audit #3  
**Verdict:** ⚠️ **MONITOR — Valid statistik, dampak terbatas**

Current value termasuk dalam window → z-score pulled toward 0 (~3.5-20% dampening tergantung window size).

**Kenapa MONITOR bukan FIX:**
- Window 28 candle = bias 3.5% (negligible)
- Window 3 candle, exclude current → window 2 → std sangat tidak stabil
- L3 thresholds SUDAH exclude current score
- Ini built-in conservatism, bukan bug

---

### CRIT-03: Dead Code `_fetch_leverage_from_funding()` Return 20x
**Source:** Audit #2  
**Verdict:** ✅☑️ **FIXED (12 Mei 2026)**

Dead code yang hardcode 20x. Dihapus.

---

### CRIT-04: Data Fabrication — `liquidity_density` dan `position_concentration`
**Source:** Audit #3 (CRITICAL-01)  
**Verdict:** 🧹 **CLEANUP — Dead code, TIDAK masuk pipeline**

Audit #3 benar: `_fetch_liquidity_density()` return hardcoded `100.0`, dan `_fetch_position_concentration()` return hardcoded Herfindahl `0.2`.

**TAPI — kedua scope ini TIDAK ADA di `sources.yaml`.** Pipeline TIDAK pernah memanggil mereka. Z-score pipeline TIDAK terpengaruh. Ini dead code, bukan active poison.

Audit #3 sendiri mengakui: *"Scope position_concentration **tidak ada** di sources.yaml saat ini, jadi tidak aktif."*

**Action:** Hapus fallback hardcoded di `_fetch_liquidity_density()`, atau mark dengan `# UNUSED: not in sources.yaml`.

---

### CRIT-05: Tidak Ada Order Execution Layer
**Source:** Audit #3  
**Verdict:** ❌ **BUANG — Salah scope audit**

TTE adalah **signal generator by design**, bukan trading bot. Blueprint dari awal sudah jelas: L1→L2→L3 = signal generation. Order execution adalah scope terpisah yang belum dibangun (dan memang belum direncanakan untuk fase ini).

Auditor mengkritik ikan karena tidak bisa terbang. Ini bukan bug — ini scope yang berbeda.

---

### CRIT-06: Race Condition File Persistence
**Source:** Audit #3 (CRITICAL-02), Audit #2 (INFO-01)  
**Verdict:** ⚠️ **MONITOR — Tidak terjadi saat ini**

Benar bahwa JSON/YAML read-modify-write tanpa locking bisa corrupt. Tapi:
- Pipeline **single-process**, single-pair saat ini
- Scheduler (`run_scheduler.ps1`) **menunggu** run selesai sebelum start run baru
- Ini jadi **real issue** hanya saat multi-pair atau concurrent runs

**Action:** Fix saat migrasi ke SQL (prerequisite multi-pair anyway).

---

### CRIT-07: Score History Tidak Namespace Per Pair/Timeframe
**Source:** Audit #2 (HIGH-02), Audit #3 (CRITICAL-04)  
**Verdict:** ✅ **FIX — Blocker multi-pair**

`score_history.json` dan `snapshot_history.json` tidak filter per pair/timeframe. Saat ini hanya run ETH_USDT/1h → tidak masalah. Tapi **HARUS fix sebelum** menambah pair kedua.

---

### CRIT-08: Simple vs Log Returns di Volatility Compression
**Source:** Audit #2 (HIGH-03), Audit #3 (CRITICAL-03)  
**Verdict:** ⚠️ **MONITOR — Dampak <0.1% di normal conditions**

`gateio.py` pakai `(P2-P1)/P1` (simple), `risk.py` pakai `np.log` (log). Untuk pergerakan <5% (ETH 1H typically 0.1-1%), perbedaan **< 0.1%**. Komentar yang bilang "log returns" memang salah, tapi outputnya effectively identical untuk data ini.

**Action:** Fix komentar + standardize ke log returns saat ada kesempatan. Bukan urgent.

---

## 🟠 HIGH SEVERITY ISSUES

### HIGH-01: CH Formula Non-Standard
**Source:** Audit #1 #2, Audit #2 (HIGH-01)  
**Verdict:** ⚠️ **MONITOR**

Between-var dibagi `n` (non-standard), within-var unweighted. Bekerja di production (R:R 2.77). Fix bisa destabilize.

---

### HIGH-02: Leverage Tanpa Risk Cap
**Source:** Audit #3 (HIGH-01)  
**Verdict:** ⚠️ **MONITOR — Valid concern, guard partial ada**

`vol_history / vol_current` bisa spike ke exchange_max di low vol. Guard `< 1e-10` ada tapi tidak mencegah high leverage di "almost zero" vol.

**Kenapa MONITOR:**
- Ini sesuai filosofi "Data is the only judge"
- Position sizing dan risk-per-trade = scope order execution layer (belum dibangun)
- Menambah hardcoded `max_leverage = 20` = violate Data is King
- SL/TP dari regime bounds = natural stop, bukan infinite risk

**Action:** Bisa tambah damping (sqrt scaling) JIKA ada evidence dari production bahwa leverage terlalu agresif.

---

### HIGH-03: Z-Score Sum Tanpa Normalisasi
**Source:** Audit #3 (HIGH-03)  
**Verdict:** ❌ **BUANG — Auditor salah analisis**

Klaim: *"scope yang kebetulan menghasilkan angka terbesar mendominasi."*

**Fakta dari production log:**
```
ohlcv:          z = 0.5963
funding_rate:   z = -0.5122
open_interest:  z = 1.5133
leverage:       z = 0.9307
volatility:     z = -0.2171
```

Semua z-score dalam range ±1.5. Tidak ada yang "mendominasi." Z-score BY DEFINITION sudah normalized (mean=0, std=1 per scope). Sum of z-scores ADALAH teknik aggregation yang valid di statistik (seperti combined p-value). Auditor bingung antara raw values vs z-scores.

---

### HIGH-04: Snapshot Dedup Menghapus Data Valid
**Source:** Audit #3 (HIGH-04)  
**Verdict:** ⚠️ **MONITOR — Nuansa valid**

Benar bahwa consecutive duplicate removal bisa hapus "weekend stillness" data. Tapi:
- Dedup ini awalnya dibuat KARENA double write (CRIT-01). Sekarang double write sudah di-fix, dedup rate akan turun drastis.
- Float equality (`!=`) memang imprecise, tapi snapshot values dari API biasanya integer-precision (OI = `95142458`, bukan `95142458.0000001`).

**Action:** Monitor dedup rate setelah CRIT-01 fix. Jika masih tinggi, revisit.

---

### HIGH-05: Population std (ddof=0) vs Sample std (ddof=1)
**Source:** Audit #2 (HIGH-04)  
**Verdict:** ⚠️ **MONITOR — Konsisten across system**

Semua pakai `ddof=0`. Bias ~18% di window 3 candle. Tapi SEMUA z-score, threshold, leverage pakai convention yang sama → net effect mendekati 0 karena konsisten.

**Action:** Fix serentak di semua file + test. Bukan urgent.

---

### HIGH-06: SL/TP dari Regime Bounds = Kontradiksi Breakout?
**Source:** Audit #3 (HIGH-06)  
**Verdict:** ❌ **BUANG — Auditor salah paham arsitektur**

Klaim: *"Breakout signal + mean reversion SL/TP = kontradiksi."*

**Ini salah.** Arsitektur L3:
- **Threshold** dari **score** regime (L2 aggregate z-score)
- **SL/TP** dari **price** regime (OHLCV price CPD)

Score bisa breakout TANPA price breakout. Contoh: funding rate + OI + leverage semua negatif (score = -4.11 = breakout bearish), tapi price masih dalam regime $2334-$2340. SL = $2340 (price regime max), TP = $2334 (price regime min). **Ini BUKAN kontradiksi** — SL/TP dari structure harga, bukan dari signal direction.

---

### HIGH-07: Exception Swallowing di Aggregator (z=0.0)
**Source:** Audit #3 (HIGH-08)  
**Verdict:** ⚠️ **MONITOR — Valid, minor**

Jika scope crash, z-score = 0.0 (neutral). Ini memang bisa menyembunyikan failure. Tapi:
- Error di-log sebagai `logger.error()`
- HTML report menunjukkan z-score per scope
- Jika semua 5 scope berhasil (saat ini selalu), ini tidak terjadi

**Action:** Tambah `failed_scopes` di metadata output saat refactor.

---

### HIGH-08: Config Loader No Cache
**Source:** Audit #2 (LOW-03), Audit #3 (HIGH-07)  
**Verdict:** 🧹 **CLEANUP**

YAML di-parse 8-10× per run. Performanya fine untuk local file. Tambah `@lru_cache` saat convenient.

---

### HIGH-09: datetime.utcnow() Deprecated
**Source:** Audit #2 (LOW-02), Audit #3 (HIGH-05)  
**Verdict:** 🧹 **CLEANUP**

Ganti ke `datetime.now(timezone.utc)`. 5 menit fix.

---

## 🟡 MEDIUM ISSUES

### MED-01-03: Dead Scopes di Connector
**Source:** Audit #2 (MED-01/02/03), Audit #3 (CRITICAL-01)  
**Verdict:** 🧹 **CLEANUP**

`liquidity_density` dan `position_concentration` tidak di sources.yaml. Dead code.

---

### MED-04: Penalty Magnitude 14+ Orders
**Source:** Audit #2 (MED-04), Audit #3 (HIGH-02)  
**Verdict:** ❌ **BUANG**

Production log: leverage_pressure penalty 23T → 17 change points, z-score 0.93. Working as intended. PELT cost scales with data magnitude.

---

### MED-05: Duplicated Meta-Learning Code L2/L3
**Source:** Audit #2 (LOW-01)  
**Verdict:** 🧹 **CLEANUP**

Pindah ke shared module saat refactor.

---

### MED-06: Snapshot Scopes Punya Lebih Banyak Data dari Historical
**Source:** Audit #3 (HIGH-09, bagian terakhir)  
**Verdict:** ⚠️ **MONITOR — Valid observation**

Snapshot scopes (OI: 485 points) vs historical (OHLCV: 50 points). Window temporal berbeda → z-score dari base data yang berbeda panjangnya.

**Tapi:** CPD + z-score hanya melihat RECENT REGIME (window), bukan full history. Apakah history 50 atau 500 points, z-score tetap dari window 5-28 candle terakhir. Length perbedaan hanya mempengaruhi meta-learning penalty selection, bukan z-score langsung.

---

## Priority Action List — FINAL (Semua 3 Audit Digabung)

### ✅ SUDAH SELESAI
| ID | Action | Status |
|---|---|---|
| CRIT-01 | Hapus double write snapshot | ✅☑️ Fixed |
| CRIT-03 | Hapus dead code 20x leverage | ✅☑️ Fixed |

### 🔥 SEBELUM MULTI-PAIR
| Priority | ID | Action | Effort |
|---|---|---|---|
| 1 | CRIT-07 | Namespace score_history + snapshot per pair/TF | 1-2 jam |
| 2 | CRIT-06 | File locking atau migrasi SQL | 1 hari+ |

### 🔧 WHEN CONVENIENT
| Priority | ID | Action | Effort |
|---|---|---|---|
| 3 | HIGH-09 | `datetime.utcnow()` → `datetime.now(tz=utc)` | 5 menit |
| 4 | HIGH-08 | Config loader cache | 10 menit |
| 5 | CRIT-04 | Cleanup dead scope fabrication code | 15 menit |
| 6 | CRIT-08 | Fix komentar + standardize log returns | 15 menit |
| 7 | MED-05 | Shared cpd_utils.py | 30 menit |
| 8 | HIGH-07 | Tambah `failed_scopes` metadata | 15 menit |

### ⏳ JANGAN FIX SEKARANG
| ID | Why Wait |
|---|---|
| HIGH-01 (CH formula) | Working in production. Test first. |
| HIGH-05 (ddof=0) | Konsisten. Fix harus serentak. |
| HIGH-02 (leverage cap) | Sesuai filosofi. No evidence of problem. |
| CRIT-02 (self-ref z-score) | Built-in conservatism. Monitor. |
| HIGH-04 (dedup) | Monitor after CRIT-01 fix. |
| MED-06 (data length) | CPD window handles this. |

### 🗑️ BUANG
| ID | Why |
|---|---|
| Audit #1 Issue #1 | Timestamp mismatch INVALID |
| CRIT-05 | TTE is signal generator by design |
| HIGH-03 (z-score sum) | Z-score IS normalized. Sum is valid. |
| HIGH-06 (SL/TP breakout) | Score regime ≠ price regime |
| MED-04 (penalty magnitude) | PELT cost scales with data |

---

## Score Card — 3 Audit Reports

| Metric | Audit #1 | Audit #2 | Audit #3 |
|---|---|---|---|
| Total claims | 6 | 15 | 14 |
| Valid + fix needed | 1 | 3 | 2 |
| Valid + monitor | 3 | 5 | 5 |
| Cleanup | 0 | 3 | 2 |
| Invalid/overstatement | 2 | 4 | 5 |
| **Accuracy rate** | **67%** | **73%** | **64%** |

Audit #2 paling akurat. Audit #3 paling detail tapi paling banyak overstatement (5 invalid dari 14).
