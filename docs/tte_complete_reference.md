# Trading Truth Engine (TTE) — Complete Reference Document
**Versi:** v2.1 (Post-Stabilization + Execution Scopes Blueprint)
**Tanggal:** 19 Mei 2026
**Owner:** Iqbal (retail quant trader, non-programmer — arsitektur didesain secara filosofis, eksekusi teknis dibantu AI agents)

---

## Tujuan Dokumen Ini

Dokumen ini adalah **referensi mandiri** untuk AI agent manapun yang perlu memahami TTE tanpa akses ke source code. Semua informasi arsitektur, filosofi, kondisi teknis, dan rencana pengembangan tercakup di sini.

---

## 1. Filosofi Inti: "Data is King"

TTE dibangun di atas satu prinsip absolut: **semua parameter harus berasal dari data, bukan judgment manusia.**

Apa artinya secara konkret:
- **Tidak ada** hardcoded threshold (seperti RSI > 70, RSI < 30)
- **Tidak ada** hardcoded multiplier (seperti ATR × 2 untuk SL, ATR × 4 untuk TP)
- **Tidak ada** fixed percentile (seperti "sell kalau di atas 90th percentile")
- **Tidak ada** manual weight (seperti "OI diberi bobot 0.3, funding 0.2")
- Semua parameter ditentukan oleh algoritma statistik yang belajar dari struktur data itu sendiri

Pengecualian yang diizinkan: **parameter infrastruktur** — hal-hal yang menentukan BERAPA BANYAK data yang dilihat mesin, bukan APA ARTI datanya. Contoh: timeout koneksi (10 detik), window cap (500 data points), interval recalibrasi (24 candle).

---

## 2. Arsitektur Berlapis

```
┌──────────────────────────────────────────┐
│            LAYER 1: Existence Oracle     │
│  "Apakah data ini ada?" (binary 0/1)    │
│  5 scope: ohlcv, funding, OI, lev, vol  │
│  Status: ✅ SELESAI                      │
└─────────────────┬────────────────────────┘
                  ↓ data terverifikasi
┌──────────────────────────────────────────┐
│         LAYER 2: Structural Analyzer     │
│  "Apa struktur statistik data ini?"      │
│  CPD (PELT) + Z-score → single score    │
│  Meta-learning: penalty per scope via CH │
│  Status: ✅ SELESAI                      │
└─────────────────┬────────────────────────┘
                  ↓ score + metadata
┌──────────────────────────────────────────┐
│       LAYER 3: Statistical Executioner   │
│  "Apa keputusan trading dari score ini?" │
│  Threshold → Entry → SL/TP → Leverage   │
│  Meta-learning: PARSIAL (hanya Kat. A)   │
│  Status: ⚠️ BELUM LENGKAP               │
└──────────────────────────────────────────┘
```

---

## 3. Layer 1 — Existence Oracle

**Tugas:** Verifikasi apakah data dari exchange benar-benar ada dan valid secara format.
**Output:** Dict `{scope_name: 0 atau 1}` — biner, tanpa interpretasi makna.

**5 Scope (didefinisikan di `config/sources.yaml`):**

| Scope | Deskripsi | Tipe Data |
|---|---|---|
| `ohlcv` | Candlestick (open, high, low, close, volume) | historical |
| `funding_rate` | Biaya funding perpetual futures | historical |
| `open_interest` | Total kontrak terbuka | snapshot |
| `leverage_pressure` | Total ukuran posisi terbuka | snapshot |
| `volatility_compression` | Standar deviasi returns (kompresi volatilitas) | snapshot |

**Validasi structural (bukan meaning):**
- Volume tidak boleh negatif (impossible by definition)
- High tidak boleh lebih rendah dari Low (impossible by OHLCV definition)
- Timestamp tidak boleh lebih tua dari N interval (staleness check)

**Menambah scope baru:** Tambah entry di `sources.yaml` + tambah fetch method di konektor exchange. Tidak ada perubahan logika di Layer 1 atau Layer 2.

**Konektor exchange yang ada:** Gate.io (`src/layer1/connectors/gateio.py`). Binance sudah disiapkan strukturnya.

---

## 4. Layer 2 — Structural Analyzer

**Tugas:** Mengubah data multi-source menjadi SATU angka (score) menggunakan Change Point Detection (CPD) + Z-score.

### Alur Proses Per Scope:
```
Data mentah (50+ data points per scope)
  ↓
PELT Algorithm (dari library 'ruptures')
  → Menemukan "change points" (titik patah) dalam time-series
  → Mengidentifikasi rezim terkini (segmen terakhir setelah change point terakhir)
  ↓
Z-score dalam rezim:
  z = (current_value - mean_regime) / std_regime
  → Nilai positif = di atas rata-rata rezim
  → Nilai negatif = di bawah rata-rata rezim
  ↓
Agregasi:
  total_score = sum(z_score semua scope aktif)
  → Tidak ada weight manual — z-score sudah membawa magnitude alami
```

### Meta-Learning Layer 2 (SUDAH ADA)

Setiap scope punya parameter PELT yang perlu dikalibrasi:
- **Penalty:** Seberapa "mahal" bagi PELT untuk menambah change point. Penalty tinggi = sedikit change point. Penalty rendah = banyak change point.
- **Model:** Tipe cost function PELT (l1, l2, atau rbf).

**Cara kalibrasi:**
1. Generate kandidat penalty dari data itu sendiri: `penalty = 2 * σ² * ln(n)` (BIC-scaled), lalu dibuat 13 variasi menggunakan `np.logspace(-3, 1, 13)`
2. Untuk setiap kandidat penalty × setiap model (l1, l2, rbf):
   - Jalankan PELT → dapatkan segmentasi
   - Evaluasi kualitas segmentasi menggunakan **Calinski-Harabasz Index** (CH)
   - CH mengukur: apakah segmen-segmen yang dihasilkan benar-benar berbeda satu sama lain?
3. Kombinasi (penalty, model) dengan CH tertinggi = **pemenang**
4. Simpan ke SQLite

**Ini sepenuhnya data-driven.** Tidak ada manusia yang menentukan penalty berapa yang bagus.

### Window Cap (Batasan Infrastruktur)

PELT memiliki kompleksitas O(n²). Untuk mencegah lamanya komputasi:
```
window_days: 7 (dari infrastructure.yaml)
raw_window = (7 × 86400) / TF_seconds
window_size = max(50, min(500, raw_window))

Hasil per TF:
  5m:  500 candle (capped)  = 42 jam data
  15m: 500 candle (capped)  = 5 hari data
  1h:  168 candle (natural)  = 7 hari data
  4h:  50 candle (floored)   = 8 hari data
  1d:  50 candle (floored)   = 50 hari data
```

---

## 5. Layer 3 — Statistical Executioner

**Tugas:** Mengubah score dari Layer 2 menjadi keputusan trading: Entry (LONG/SHORT/Neutral), Stop Loss, Take Profit, Leverage.

### 5.1 Alur Proses:

```
Input: current_score, score_history (635+ data points), price_history (50 candle), penalties dari meta-learning

Step 1: Threshold Derivation (thresholds.py)
  → PELT pada score_history → temukan rezim terakhir
  → threshold_up = max(rezim)
  → threshold_down = min(rezim)

Step 2: Entry Decision (executor.py)
  → current_score > threshold_up → LONG
  → current_score < threshold_down → SHORT
  → Di antara → Neutral (0)

Step 3: SL/TP (risk.py)
  → PELT pada price_history → temukan rezim harga terakhir
  → LONG: SL = min(rezim), TP = max(rezim)
  → SHORT: SL = max(rezim), TP = min(rezim)
  → Validasi geometri: entry harus di antara SL dan TP

Step 4: Leverage (risk.py)
  → Hitung volatilitas rezim (std log-returns dalam rezim)
  → Hitung volatilitas full history
  → leverage = vol_history / vol_current
  → Cap di exchange_max (dari API, bukan hardcoded)

Step 5: Risk-Reward Ratio
  → RR = reward / risk (dihitung, tidak dievaluasi)
```

### 5.2 Meta-Learning Layer 3 (PARSIAL — hanya Kategori A)

Layer 3 punya 2 scope meta-learning:
- `score` penalty → untuk PELT pada score_history (menentukan threshold)
- `price` penalty → untuk PELT pada price_history (menentukan rezim harga)

Kedua scope ini dikalibrasi persis sama dengan Layer 2 (CH sebagai metrik). **Ini sudah benar dan tidak perlu diubah.**

### 5.3 MASALAH: Instrumen Eksekusi Belum Terkalibrasi

Ini adalah temuan arsitektur kritis. Layer 3 memiliki instrumen eksekusi yang **belum didefinisikan sebagai scope** dan **belum punya mekanisme kalibrasi**:

| Instrumen | Formula Saat Ini | Masalah |
|---|---|---|
| SL placement | `min/max(regime)` | Formula tetap, tidak pernah dievaluasi efektivitasnya |
| TP placement | `max/min(regime)` | Formula tetap, tidak pernah dievaluasi realisasinya |
| Leverage sizing | `vol_history/vol_current` | Formula tetap, tidak pernah dievaluasi P&L-nya |
| RR evaluation | `reward/risk` | Dihitung tapi tidak pernah dipakai sebagai filter |
| Entry timing | Score vs threshold | Tidak ada evaluasi apakah timing optimal dalam rezim |

**Analogi:** Ini seperti jika Layer 2 menggunakan `penalty = 1.0` hardcoded untuk semua scope tanpa meta-learning. Hasilnya: kadang benar kadang salah, tapi tidak pernah bisa improve karena tidak ada feedback loop.

### 5.4 Dua Kategori Instrumen Eksekusi

**Kategori A: Statistika Struktural** (sudah ada)
- Bisa dikalibrasi dari data market sekarang (seperti Layer 2)
- Metrik: Calinski-Harabasz Index
- Scope: `entry_signal` (score penalty), `regime_detection` (price penalty)

**Kategori B: Kualitas Eksekusi** (BELUM ADA)
- Hanya bisa dikalibrasi dari HASIL NYATA (outcome data)
- Metrik: win rate, hit rate, expected value
- Scope: `sl_quality`, `tp_quality`, `leverage_quality`, `signal_confidence`

**Mengapa Kategori B berbeda:**
```
Kategori A: "Apakah PELT memotong data dengan benar?"
  → Bisa dijawab SEKARANG dari data yang ada (CH score)

Kategori B: "Apakah SL di batas rezim itu efektif?"
  → TIDAK BISA dijawab sekarang
  → Harus TUNGGU dan LIHAT: apakah harga sentuh SL sebelum TP?
  → Butuh data outcome yang belum ada
```

---

## 6. Blueprint: Layer 3 Execution Scopes + Layer 4

### 6.1 Layer 3 Redesign

Layer 3 perlu mendefinisikan instrumen eksekusi sebagai **scope** (seperti Layer 2 mendefinisikan `sources.yaml`), dengan dua kategori kalibrasi yang berbeda:

```
Kategori A (structural): dikalibrasi dari data market via CH → SUDAH ADA
Kategori B (outcome): dikalibrasi dari data outcome via win rate → BELUM ADA
```

Menambah scope eksekusi baru = tambah entry di YAML + implementasi evaluator. Sama mudahnya dengan menambah scope analisis di Layer 2.

### 6.2 Layer 4: Monitor + Executor (BELUM ADA)

Layer baru yang memiliki dua fungsi:

**Fungsi 1: Eksekusi** — Menjalankan sinyal dari Layer 3 di exchange via API (open/close position otomatis).

**Fungsi 2: Monitor** — Mencatat outcome setiap sinyal dan mengirimnya kembali ke Layer 3 untuk kalibrasi Kategori B.

```
Data Flow:
  L1 → L2 (top-down): L1 menyediakan data terverifikasi
  L2 → L3 (top-down): L2 menyediakan score
  L3 → L4 (top-down): L3 menyediakan sinyal trading
  L4 → L3 (bottom-up): L4 menyediakan data outcome → feedback loop
```

### 6.3 Roadmap Implementasi

| Phase | Deskripsi | Status |
|---|---|---|
| Phase 1 | Signal Logger — catat setiap sinyal ke SQLite, cek outcome di run berikutnya | Belum mulai |
| Phase 2 | Definisi scope Kategori B di YAML | Belum mulai |
| Phase 3 | L3 Outcome Calibration — meta-learning Kategori B dari data outcome (50+ sinyal) | Belum mulai |
| Phase 4 | Layer 4 penuh — eksekusi otomatis via exchange API + monitoring real-time | Belum mulai |

---

## 7. Database & Isolation

### SQLite (data/tte.db)

TTE menggunakan SQLite dengan WAL mode (Write-Ahead Logging) yang memungkinkan concurrent read/write:
- `run_live.py` = reader (banyak instance bisa jalan paralel)
- `run_calibration.py` = writer (satu instance menulis penalty baru)

### Namespace Isolation

Semua data terisolasi per kombinasi unik **(exchange, pair, timeframe)**:

```
gateio/ETH_USDT/1h  → penalty sendiri, score_history sendiri, snapshot sendiri
gateio/ETH_USDT/4h  → TERPISAH TOTAL dari 1h walaupun pair sama
binance/BTC_USDT/1h  → TERPISAH TOTAL dari gateio walaupun TF sama
```

Setiap combo adalah **universe independen** dengan meta-learning sendiri. Penalty optimal untuk ETH di TF 1h bisa sangat berbeda dari BTC di TF 4h — dan sistem mengakomodasi ini otomatis.

### Tabel Utama:

| Tabel | Isi | Kunci |
|---|---|---|
| `penalties` | Penalty + model per scope per layer | exchange, pair, timeframe, layer, scope |
| `score_history` | Riwayat score L2 | exchange, pair, timeframe |
| `snapshot_history` | Riwayat data snapshot (OI, leverage, vol) | exchange, pair, timeframe, scope |
| `signal_history` | (BELUM ADA — Phase 1 blueprint) | exchange, pair, timeframe |

---

## 8. Entry Points & Cara Menjalankan

### run_live.py — Signal Generation (Tanpa Meta-Learning)
```bash
python scripts/run_live.py
```
- Membaca semua combo dari `config/trading_pairs.yaml`
- Untuk setiap combo: L1 → L2 → L3 → output sinyal
- Menggunakan penalty yang sudah tersimpan di SQLite (dari calibration sebelumnya)
- Latency: ~17 detik per combo
- **Tidak melakukan meta-learning** — murni baca dan eksekusi

### run_calibration.py — Meta-Learning
```bash
# Single pass (untuk manual/testing):
python scripts/run_calibration.py --once

# Continuous daemon (untuk production):
python scripts/run_calibration.py
```
- Membaca semua combo dari `config/trading_pairs.yaml`
- Untuk setiap combo: meta-learning L2 (5 scope) + meta-learning L3 (score + price)
- Menyimpan penalty baru ke SQLite
- Smart scheduling: hanya recalibrate jika sudah lewat `interval_candles` (default: 24 candle)
- Untuk TF 1h, recalibrasi terjadi setiap ~24 jam

### Konfigurasi

| File | Fungsi |
|---|---|
| `config/trading_pairs.yaml` | Daftar combo aktif (exchange/pair/TF) |
| `config/sources.yaml` | Definisi scope L2 (instrumen analisis) |
| `config/infrastructure.yaml` | Parameter infrastruktur (window, interval, timeout) |

Menambah pair/TF baru = edit `trading_pairs.yaml`. Tidak ada perubahan kode.
Menambah exchange baru = edit `trading_pairs.yaml` + buat konektor di `src/layer1/connectors/`.

---

## 9. Contoh Output Sinyal Live

```json
{
  "gateio/ETH_USDT/1h": {
    "signal": "SHORT",
    "entry_price": 2253.51,
    "stop_loss": 2275.93,
    "take_profit": 2244.4,
    "leverage": 1.28,
    "scope_count": 5,
    "layer3_output": {
      "entry": "SHORT",
      "risk_reward_ratio": 0.41,
      "metadata": {
        "threshold_up": 5.953,
        "threshold_down": 0.169,
        "score": -5.919,
        "score_z": -2.188,
        "regime_volatility": 0.00396
      }
    }
  }
}
```

**Cara membaca:** Semua 5 scope sepakat bearish (score = -5.92, z = -2.19). Entry SHORT di $2253, SL di batas atas rezim ($2276), TP di batas bawah rezim ($2244). Leverage 1.28x (volatilitas rendah relatif terhadap histori). RR = 0.41 (reward lebih kecil dari risk — ini masalah Kategori B yang belum terkalibrasi).

---

## 10. Struktur File Project

```
trading_truth_engine/
├── config/
│   ├── trading_pairs.yaml        ← combo aktif (exchange/pair/TF)
│   ├── sources.yaml              ← definisi scope L2
│   └── infrastructure.yaml       ← parameter infra (window, interval)
├── data/
│   └── tte.db                    ← SQLite database (penalties, scores, snapshots)
├── scripts/
│   ├── run_live.py               ← signal generation (tanpa meta-learning)
│   └── run_calibration.py        ← meta-learning daemon
├── src/
│   ├── layer1/
│   │   ├── verifier.py           ← existence check (0/1)
│   │   └── connectors/
│   │       └── gateio.py         ← Gate.io API connector
│   ├── layer2/
│   │   ├── aggregator.py         ← orchestrator L2 (CPD + Z-score per scope)
│   │   ├── cpd.py                ← PELT wrapper (ruptures library)
│   │   ├── zscore.py             ← z-score calculation
│   │   ├── meta_learning.py      ← BIC candidates + CH evaluation
│   │   └── penalty_manager.py    ← penalty caching/loading (SQLite)
│   ├── layer3/
│   │   ├── executor.py           ← orchestrator L3 (threshold → entry → SL/TP → leverage)
│   │   ├── thresholds.py         ← threshold derivation dari score CPD
│   │   ├── risk.py               ← SL/TP placement + leverage calculation
│   │   ├── meta_learning.py      ← score + price penalty optimization
│   │   └── penalty_manager.py    ← L3 penalty caching/loading (SQLite)
│   └── utils/
│       ├── database.py           ← SQLite operations (WAL mode, 30s timeout)
│       ├── config_loader.py      ← YAML config loading
│       └── history_manager.py    ← snapshot history management
├── docs/
│   ├── tte_complete_reference.md ← DOKUMEN INI
│   ├── tte_v2_blueprint.md       ← blueprint teknis awal
│   └── layer3_execution_scopes_blueprint.md ← blueprint L3 Kat. B + L4
├── diagnostics/
│   └── output/                   ← HTML report per sinyal
└── logs/
    └── scheduled_runs/
        ├── live.log              ← log run_live
        └── calibration.log       ← log run_calibration
```

---

## 11. Dependencies

```
Python 3.14
ruptures        ← Change Point Detection (PELT algorithm)
numpy           ← numerical computation
sqlite3         ← database (built-in Python)
requests        ← HTTP API calls
pyyaml          ← YAML config parsing
```

Tidak ada dependency berat. Tidak butuh GPU. Bisa jalan di VM 4 vCPU / 8GB RAM.

---

## 12. Glossary

| Istilah | Definisi dalam konteks TTE |
|---|---|
| **Scope** | Unit instrumen yang terdefinisi dan terkalibrasi — di L2 ini instrumen analisis (ohlcv, funding, dll), di L3 ini instrumen eksekusi (SL, TP, leverage, dll) |
| **PELT** | Pruned Exact Linear Time — algoritma Change Point Detection dari library `ruptures` |
| **CH** | Calinski-Harabasz Index — metrik kualitas segmentasi (semakin tinggi = segmen semakin berbeda satu sama lain) |
| **BIC** | Bayesian Information Criterion — basis formula untuk generate kandidat penalty (`2σ²ln(n)`) |
| **Penalty** | Parameter yang mengontrol sensitivitas PELT — penalty tinggi = sedikit change point, rendah = banyak |
| **Rezim** | Segmen terakhir dari time-series setelah change point terakhir — mewakili "kondisi market saat ini" |
| **Z-score** | `(x - μ) / σ` — berapa standar deviasi nilai saat ini dari rata-rata rezim |
| **Namespace** | Kombinasi unik (exchange, pair, timeframe) yang mengisolasi semua data |
| **Kategori A** | Instrumen yang bisa dikalibrasi dari data market sekarang (statistika murni) |
| **Kategori B** | Instrumen yang hanya bisa dikalibrasi dari data outcome/hasil trading |
| **WAL Mode** | Write-Ahead Logging — mode SQLite yang memungkinkan concurrent read + write |
| **Daemon** | Proses background yang jalan terus (run_calibration.py tanpa --once) |

---

## 13. Status Ringkasan

| Komponen | Status | Catatan |
|---|---|---|
| Layer 1 (Existence) | ✅ Selesai | 5 scope, binary check, gak pernah perlu disentuh |
| Layer 2 (Analysis) | ✅ Selesai | 5 scope, full meta-learning, zero hardcoded |
| L2 Meta-Learning | ✅ Selesai | BIC + CH, per scope per namespace |
| Layer 3 Kat. A | ✅ Selesai | Score + price penalty via CH |
| Layer 3 Kat. B | ❌ Belum ada | Instrumen eksekusi belum terkalibrasi |
| Layer 4 (Monitor) | ❌ Belum ada | Blueprint sudah ada |
| SQLite + Isolation | ✅ Selesai | WAL mode, namespace per combo |
| LIVE/CALIBRATION Split | ✅ Selesai | Dua entry point terpisah |
| Window Cap | ✅ Selesai | Auto per TF, infrastructure param |
| Multi Exchange/Pair/TF | ✅ Arsitektur siap | Saat ini baru 1 combo aktif (gateio/ETH_USDT/1h) |
| Deployment | 🔜 Planned | Target: Contabo VPS 4vCPU/8GB + Docker |
