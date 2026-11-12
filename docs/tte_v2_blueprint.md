# TTE v2 Architecture Blueprint
## Migrasi SQLite + Multi-Pair Isolation + Live/Calibration Split

**Date:** 14 Mei 2026  
**Author:** Opus (Antigravity IDE) — berdasarkan diskusi dengan Iqbal  
**Status:** DOKUMENTASI — Belum dieksekusi  
**Filosofi:** Data is King — tidak berubah

---

## 1. Ringkasan Perubahan

TTE saat ini adalah **single-pair, single-timeframe, single-exchange** engine yang menyimpan data di file JSON/YAML. Upgrade ini mengubah TTE menjadi arsitektur **multi-everything** dengan pemisahan antara proses LIVE dan CALIBRATION.

### Yang Berubah
| Aspek | Sekarang (v1) | Nanti (v2) |
|---|---|---|
| Storage | JSON + YAML files | **SQLite database** (`data/tte.db`) |
| Pair/TF | Hardcoded 1 pair, 1 TF | **Multi-pair, multi-TF, multi-exchange** |
| Pipeline | 1 proses (LIVE + meta-learning) | **2 proses terpisah** |
| Latency | ~11 menit (meta-learning setiap run) | **Detik** (LIVE), menit (CALIBRATION) |
| Penalty cache | `config/penalties.yaml` (flat) | **SQLite tabel** (per namespace) |
| Score history | `data/score_history.json` (flat) | **SQLite tabel** (per namespace) |
| Snapshot history | `data/snapshot_history.json` (flat) | **SQLite tabel** (per namespace) |

### Yang TIDAK Berubah
- **Seluruh logika matematika**: PELT, BIC, CH, Z-Score, L1/L2/L3 flow
- **Filosofi**: Data is King, no hardcoded judgment
- **Connector layer**: Gate.io & Binance connectors tetap sama
- **sources.yaml**: Scope definitions tetap sama
- **infrastructure.yaml**: Retry, timeout, staleness tetap sama

---

## 2. Hierarki Data: Exchange → Pair → Timeframe

Setiap kombinasi `exchange + pair + timeframe` adalah **satu namespace terisolasi**. Tidak ada data yang tercampur antar namespace.

```
Gate.io
├── ETH_USDT
│   ├── 5m   → [score_history, snapshot_history, penalties] ← namespace sendiri
│   ├── 1h   → [score_history, snapshot_history, penalties] ← namespace sendiri
│   └── 1d   → [score_history, snapshot_history, penalties] ← namespace sendiri
├── BTC_USDT
│   ├── 15m  → [score_history, snapshot_history, penalties]
│   ├── 4h   → [score_history, snapshot_history, penalties]
│   └── 1d   → [score_history, snapshot_history, penalties]
│
Binance
├── ETH_USDT
│   ├── 1h   → [score_history, snapshot_history, penalties] ← BEDA dari Gate.io ETH 1h!
│   └── ...
```

**Contoh konkret:**
- Gate.io / ETH_USDT / 1h → penalty OHLCV = 3.89
- Gate.io / ETH_USDT / 5m → penalty OHLCV = 0.72 ← **BEDA**, karena TF 5m punya volatility berbeda
- Binance / ETH_USDT / 1h → penalty OHLCV = 4.11 ← **BEDA**, karena data Binance ≠ Gate.io

Ini juga berarti exchange itu **penopang**: di dalam 1 exchange ada ratusan pair, di setiap pair ada ~10 TF. Arsitektur harus bisa menampung semua tanpa campur data.

---

## 3. Schema SQLite

### 3.1 Tabel `score_history`
Menggantikan `data/score_history.json`.

```sql
CREATE TABLE score_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,       -- 'gateio', 'binance'
    pair        TEXT NOT NULL,       -- 'ETH_USDT', 'BTC_USDT'
    timeframe   TEXT NOT NULL,       -- '1h', '5m', '1d'
    score       REAL NOT NULL,       -- Layer 2 aggregate z-score
    created_at  TEXT NOT NULL        -- ISO 8601 UTC timestamp
);

CREATE INDEX idx_score_namespace ON score_history(exchange, pair, timeframe);
```

**Query contoh (menggantikan `get_score_history()`):**
```sql
SELECT score FROM score_history
WHERE exchange = 'gateio' AND pair = 'ETH_USDT' AND timeframe = '1h'
ORDER BY created_at ASC;
```

### 3.2 Tabel `snapshot_history`
Menggantikan `data/snapshot_history.json`.

```sql
CREATE TABLE snapshot_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,
    pair        TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    scope       TEXT NOT NULL,       -- 'open_interest', 'leverage_pressure'
    value       REAL NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX idx_snapshot_namespace ON snapshot_history(exchange, pair, timeframe, scope);
```

### 3.3 Tabel `penalties`
Menggantikan `config/penalties.yaml` (khusus bagian penalties/models).

```sql
CREATE TABLE penalties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,
    pair        TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    layer       TEXT NOT NULL,       -- 'l2' atau 'l3'
    scope       TEXT NOT NULL,       -- 'ohlcv', 'funding_rate', 'score', 'price'
    penalty     REAL NOT NULL,
    model       TEXT NOT NULL,       -- 'l2', 'rbf', 'l1'
    data_variance REAL,             -- σ² saat penalty dihitung (untuk smart refresh)
    updated_at  TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_penalty_namespace
    ON penalties(exchange, pair, timeframe, layer, scope);
```

**Catatan:** `UNIQUE INDEX` berarti setiap namespace hanya punya 1 penalty aktif per scope. Update = `INSERT OR REPLACE`.

### 3.4 File Database
```
data/tte.db          ← Satu file. Semua tabel di sini.
config/sources.yaml  ← Tetap YAML (definisi scope, jarang berubah)
config/infrastructure.yaml ← Tetap YAML (retry, timeout, jarang berubah)
```

`sources.yaml` dan `infrastructure.yaml` tetap YAML karena ini **konfigurasi statis** yang dibaca sekali saat startup, bukan data yang terus berubah.

---

## 4. Arsitektur Dua Proses: LIVE & CALIBRATION

### 4.1 LIVE Process — `scripts/run_live.py`

```
┌───────────────────────────────────────────────────────────┐
│  LIVE PROCESS                                             │
│  Trigger: sesuai timeframe (1h TF → setiap jam)          │
│                                                           │
│  1. Baca penalty dari SQLite (SELECT ... WHERE namespace) │
│  2. L1 → L2 → L3 (tanpa meta-learning)                  │
│  3. Simpan score ke SQLite                                │
│  4. Output signal                                         │
│                                                           │
│  Kecepatan: DETIK (tidak ada PELT loop)                  │
│  Meta-learning: TIDAK ADA                                 │
└───────────────────────────────────────────────────────────┘
```

**Flow detail:**
1. Terima parameter: `(exchange, pair, timeframe)`
2. Baca penalty dari tabel `penalties` WHERE namespace = parameter
3. Jika penalty belum ada (pertama kali) → signal = 0, log warning
4. Run L1 → L2 (pakai penalty yang sudah ada) → L3
5. Simpan L2 score ke `score_history`
6. Return signal

**Yang TIDAK dilakukan:** `meta_learn_all_scopes()`, `force_refresh_if_needed()`

### 4.2 CALIBRATION Process — `scripts/run_calibration.py`

```
┌───────────────────────────────────────────────────────────┐
│  CALIBRATION PROCESS                                      │
│  Trigger: terus-menerus (selesai → langsung run lagi)    │
│                                                           │
│  LOOP tanpa henti:                                        │
│    Untuk setiap (exchange, pair, timeframe) yang aktif:   │
│      1. Ambil data history dari exchange API              │
│      2. Apply window cap (sesuai TF)                     │
│      3. Run meta-learning (BIC + CH + PELT)              │
│      4. Simpan penalty ke SQLite                          │
│    Selesai semua combo → mulai lagi dari awal            │
└───────────────────────────────────────────────────────────┘
```

**Flow detail:**
1. Baca daftar `(exchange, pair, timeframe)` aktif dari config
2. Loop setiap combo:
   a. Fetch data history (API atau snapshot)
   b. Cap data ke window size (lihat Section 5)
   c. Run `find_optimal_penalty()` per scope
   d. Simpan penalty + model + variance ke tabel `penalties`
3. Setelah semua combo selesai → `continue` (mulai dari combo pertama)
4. Proses ini **tidak pernah berhenti** selama machine hidup

**Perbedaan kunci dengan LIVE:**
- CALIBRATION **menulis** penalty, LIVE **membaca** penalty
- CALIBRATION boleh lambat (menit), LIVE harus cepat (detik)
- CALIBRATION tidak menghasilkan trading signal
- CALIBRATION run sekuensial (1 combo at a time, bukan parallel)

### 4.3 Diagram Interaksi

```
┌──────────────┐          ┌──────────────┐
│  CALIBRATION │          │     LIVE     │
│   Process    │          │   Process    │
│              │          │              │
│ meta-learning│  WRITE   │  Baca penalty│
│ BIC + CH     │────────→ │  L1→L2→L3   │
│ PELT × 39   │ penalties │  Tulis score │
│              │  to SQL  │  to SQL      │
└──────────────┘          └──────────────┘
       ↕                         ↕
   ┌───────────────────────────────┐
   │      data/tte.db (SQLite)    │
   │                               │
   │  penalties    ← CAL writes   │
   │  score_history ← LIVE writes │
   │  snapshot_history ← both     │
   └───────────────────────────────┘
```

**Concurrent access:** SQLite dengan WAL mode support 1 writer + multiple readers secara bersamaan. CALIBRATION = writer (penalties), LIVE = writer (scores) + reader (penalties). Ini aman karena mereka menulis ke **tabel berbeda**.

---

## 5. Calibration Window — Batasan Teknis Per Timeframe

### Kenapa Perlu Window Cap?
PELT complexity = O(n²). Tanpa cap, snapshot scopes (OI, leverage) yang terus bertambah data setiap run akan membuat calibration makin lambat seiring waktu. Window cap memastikan waktu komputasi **konstan** selamanya.

### Apakah Ini Melanggar Filosofi?
**Tidak.** Window cap adalah **parameter infrastruktur** — sama statusnya dengan `max_staleness_intervals: 2` dan `timeout_seconds: 10`. Ia mengatakan "berapa banyak data yang DILIHAT oleh mesin", bukan "apa ARTI datanya." Data yang dilihat tetap 100% menentukan penalty — manusia hanya menentukan jendela observasinya.

### Cara Hitung Window Per Timeframe

```yaml
# infrastructure.yaml (tambahan baru)
calibration:
  window_days: 7     # Berapa hari ke belakang yang dilihat calibration
  min_window: 50     # Minimum data points (statistik butuh minimal ini)
  max_window: 500    # Maximum data points (batas komputasi)
```

**Formula (di kode):**
```python
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

raw_window = (window_days * 86400) // TIMEFRAME_SECONDS[timeframe]
window_size = max(min_window, min(max_window, raw_window))
```

**Hasil per TF (dengan default 7 hari):**
| Timeframe | Raw Window | After Clamp | Keterangan |
|---|---|---|---|
| 5m | 2016 | **500** (max cap) | 500 candle × 5m = 42 jam data |
| 15m | 672 | **500** (max cap) | 500 candle × 15m = 5 hari data |
| 1h | 168 | **168** (natural) | Persis 7 hari |
| 4h | 42 | **50** (min cap) | 50 candle × 4h = 8 hari data |
| 1d | 7 | **50** (min cap) | 50 candle × 1d = 50 hari data |

**Perhatikan:** TF besar (4h, 1d) otomatis melihat lebih jauh ke belakang karena minimum 50 candle. Ini masuk akal — regime di daily chart memang lebih lambat berubah.

---

## 6. Config Baru: Active Trading Pairs

Untuk memberitahu sistem pair+TF mana yang aktif, kita perlu config baru:

```yaml
# config/trading_pairs.yaml

active_pairs:
  gateio:
    ETH_USDT:
      timeframes: ["1h", "4h"]
      market_type: perpetual
    BTC_USDT:
      timeframes: ["1h"]
      market_type: perpetual

  binance:
    ETH_USDT:
      timeframes: ["5m", "1h", "1d"]
      market_type: perpetual
```

LIVE scheduler membaca ini untuk tahu combo mana yang harus di-run dan kapan.
CALIBRATION membaca ini untuk tahu combo mana yang harus di-calibrate.

**Menambah pair baru = menambah entry di file ini.** Tidak ada perubahan kode.

---

## 7. Perubahan File — Scope Lengkap

### 7.1 File BARU
| File | Purpose |
|---|---|
| `src/utils/database.py` | SQLite connection, schema init, query helpers |
| `scripts/run_live.py` | LIVE process entry point (tanpa meta-learning) |
| `scripts/run_calibration.py` | CALIBRATION process entry point (loop tak terbatas) |
| `config/trading_pairs.yaml` | Daftar active pairs + TF + exchange |

### 7.2 File DIMODIFIKASI
| File | Perubahan |
|---|---|
| `src/utils/history_manager.py` | Ganti JSON read/write → SQLite query. Tambah `exchange` parameter. |
| `src/layer2/penalty_manager.py` | Ganti YAML read/write → SQLite query. Tambah namespace parameters. |
| `src/layer3/penalty_manager.py` | Sama — ganti ke SQLite, tambah namespace. |
| `src/layer2/meta_learning.py` | Tambah window cap sebelum `find_optimal_penalty()`. |
| `src/layer3/meta_learning.py` | Sama — tambah window cap. |
| `config/infrastructure.yaml` | Tambah section `calibration:` (window_days, min/max_window). |

### 7.3 File DEPRECATED (Tetap Ada, Tidak Dipakai Lagi)
| File | Alasan |
|---|---|
| `data/score_history.json` | Digantikan oleh tabel `score_history` di SQLite |
| `data/snapshot_history.json` | Digantikan oleh tabel `snapshot_history` di SQLite |
| `config/penalties.yaml` | Digantikan oleh tabel `penalties` di SQLite |
| `scripts/run_pipeline.py` | Digantikan oleh `run_live.py` + `run_calibration.py` |

### 7.4 File TIDAK BERUBAH
- `src/layer1/` — Seluruh Layer 1 (verifier, connectors)
- `src/layer2/aggregator.py` — Logika aggregation tetap
- `src/layer2/cpd.py` — CPD logic tetap
- `src/layer2/zscore.py` — Z-score logic tetap
- `src/layer3/executor.py` — Signal generation tetap
- `src/layer3/risk.py` — SL/TP/Leverage tetap
- `src/layer3/thresholds.py` — Threshold derivation tetap
- `src/diagnostics/` — Report builder tetap
- `config/sources.yaml` — Scope definitions tetap

---

## 8. Bug Fixes Yang Ikut Terselesaikan

Ketiga bug dari audit yang statusnya "sebelum multi-pair" **otomatis terselesaikan** oleh migrasi ini:

| Bug | Solusi di v2 |
|---|---|
| **Score history tidak di-namespace** (CRIT-07) | ✅ Tabel SQL punya kolom `exchange + pair + timeframe` |
| **Race condition file persistence** (CRIT-06) | ✅ SQLite WAL mode = concurrent-safe |
| **`datetime.utcnow()` deprecated** (HIGH-09) | ✅ Ganti ke `datetime.now(timezone.utc)` saat refactor |

---

## 9. Urutan Eksekusi

| Step | Action | Dependensi |
|---|---|---|
| **1** | Buat `src/utils/database.py` — SQLite schema + helpers | Tidak ada |
| **2** | Refactor `history_manager.py` — JSON → SQLite | Step 1 |
| **3** | Refactor `penalty_manager.py` (L2 + L3) — YAML → SQLite | Step 1 |
| **4** | Tambah window cap di `meta_learning.py` (L2 + L3) | Tidak ada |
| **5** | Update `infrastructure.yaml` — tambah section calibration | Tidak ada |
| **6** | Buat `config/trading_pairs.yaml` — active pairs config | Tidak ada |
| **7** | Buat `scripts/run_live.py` — LIVE process tanpa meta-learning | Step 2, 3 |
| **8** | Buat `scripts/run_calibration.py` — CALIBRATION loop | Step 2, 3, 4 |
| **9** | Migrate data lama (JSON → SQLite) — one-time script | Step 1, 2 |
| **10** | Test end-to-end: jalankan CALIBRATION + LIVE bersamaan | Step 7, 8 |

---

## 10. Catatan Penting

### 10.1 SQLite → PostgreSQL Nanti
Semua query SQL yang kita tulis di `database.py` **kompatibel 95%** dengan PostgreSQL. Saat pindah ke cloud/home server nanti:
1. Ganti `sqlite3.connect("data/tte.db")` → `psycopg2.connect(host=..., db=...)`
2. Ganti `AUTOINCREMENT` → `SERIAL`
3. Selesai. Tidak ada perubahan logika.

### 10.2 Skala Realistis
| Skala | Combo | Calibration Cycle | Feasibility |
|---|---|---|---|
| MVP (sekarang) | 2 exchange × 2 pair × 2 TF = 8 | ~2 menit | ✅ Laptop kentang |
| Medium | 2 × 10 × 5 = 100 | ~30 menit | ✅ Laptop biasa |
| Large | 2 × 50 × 10 = 1000 | ~5 jam | ⚠️ Butuh prioritization |
| Extreme | 2 × 300 × 10 = 6000 | ~16 jam | ❌ Butuh cloud/distributed |

### 10.3 MVP Target
Untuk eksekusi pertama, kita target:
- **2 exchange:** Gate.io + Binance (sudah ada connector)
- **2 pair:** ETH_USDT + BTC_USDT
- **2-3 TF:** 1h + 4h (+ optional 5m)
- Total: **8-12 combo** — calibration cycle < 5 menit

Ini sudah cukup untuk membuktikan arsitektur multi bekerja. Menambah pair/TF nanti = tambah baris di `trading_pairs.yaml`. Tidak ada perubahan kode.
