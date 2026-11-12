# TTE v2 Phase 2 — LIVE/CALIBRATION Split
## Dokumentasi Arsitektur: Pemisahan Dimensi Live & Kalibrasi Meta-Learning

**Date:** 14 Mei 2026  
**Status:** DOKUMENTASI — Belum dieksekusi  
**Prerequisite:** ✅ SQLite migration (selesai)  
**Authors:** Iqbal (filosof sistem), Opus (developer), DeepSeek (reviewer)

---

## 1. Masalah Yang Diselesaikan

### 1.1 Latency
Pipeline sekarang butuh ~10 menit per run karena meta-learning (PELT × 39 kombinasi per scope) dijalankan **setiap kali** pipeline berjalan. Padahal regime pasar jarang berubah setiap jam.

### 1.2 Skalabilitas Instance
Ketika multi-pair aktif (misal 100 combo exchange/pair/TF), menjalankan meta-learning untuk SEMUA combo setiap run = mustahil di laptop biasa.

### 1.3 Kaku vs Adaptif
Solusi "run setiap 7 hari" = hardcoded, bisa basi.  
Solusi "run tanpa henti" = berat, hardware gak kuat.  
Butuh solusi yang **adaptif terhadap karakteristik timeframe**.

---

## 2. Solusi: 2 Parameter Infrastruktur

Semua masalah di atas diselesaikan oleh **2 parameter** di `infrastructure.yaml`:

```yaml
calibration:
  window_days: 7          # Berapa hari data yang dilihat PELT (solve latency)
  interval_candles: 24    # Setiap berapa candle baru, recalibrate (solve scheduling)
```

Kedua parameter ini **otomatis menyesuaikan diri** dengan setiap timeframe tanpa konfigurasi tambahan per pair, per exchange, atau per TF.

---

## 3. Parameter 1: `window_days` — Solve Latency

### Apa Yang Dilakukan
Membatasi jumlah data yang di-feed ke PELT saat meta-learning. PELT complexity = O(n²), jadi membatasi data = membatasi waktu komputasi.

### Formula
```python
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}

raw_window = (window_days * 86400) // TIMEFRAME_SECONDS[timeframe]
window_size = max(min_window, min(max_window, raw_window))
```

### Hasil Per Timeframe (window_days=7, min=50, max=500)

| Timeframe | Raw Window | Setelah Clamp | Data Yang Dilihat |
|---|---|---|---|
| 5m | 2016 | **500** (max cap) | 42 jam terakhir |
| 15m | 672 | **500** (max cap) | 5 hari terakhir |
| 1h | 168 | **168** (natural) | 7 hari terakhir |
| 4h | 42 | **50** (min cap) | 8 hari terakhir |
| 1d | 7 | **50** (min cap) | 50 hari terakhir |

### Kenapa Min 50?
PELT membutuhkan cukup data untuk mendeteksi regime secara statistik. Di bawah 50 data points, segmentasi menjadi tidak reliable.

### Kenapa Max 500?
Di atas 500, waktu komputasi PELT meningkat secara kuadratik tanpa menambah kualitas deteksi regime yang signifikan. Data lama (> 500 candle ke belakang) biasanya sudah melewati beberapa regime change dan tidak relevan untuk regime SAAT INI.

### Filosofi
`window_days` adalah **parameter infrastruktur** — setara dengan `timeout_seconds` dan `max_staleness_intervals`. Ia mengatakan "berapa banyak data yang DILIHAT oleh mesin", bukan "apa ARTI datanya." Data di dalam window tetap 100% menentukan penalty.

---

## 4. Parameter 2: `interval_candles` — Solve Scheduling

### Apa Yang Dilakukan
Menentukan kapan calibration harus di-run ulang untuk setiap combo (exchange/pair/TF). Bukan berdasarkan waktu manusia ("7 hari"), tapi berdasarkan **jumlah candle baru** yang sudah terkumpul sejak kalibrasi terakhir.

### Formula
```
waktu_recalibrate = interval_candles × durasi_1_candle
```

### Hasil Per Timeframe (interval_candles=24)

| Timeframe | Interval | Recalibrate Setiap |
|---|---|---|
| 5m | 24 × 5m | **2 jam** |
| 15m | 24 × 15m | **6 jam** |
| 1h | 24 × 1h | **24 jam** (1 hari) |
| 4h | 24 × 4h | **4 hari** |
| 1d | 24 × 1d | **24 hari** |

### Kenapa Ini Elegan
- **TF cepat = recalibrate sering** → pasar 5 menit memang berubah cepat, penalty harus fresh
- **TF lambat = recalibrate jarang** → pasar daily memang berubah lambat, penalty tahan lama
- **Gak ada hardcoded schedule** → 1 angka (`24`) berlaku untuk semua TF
- **Data yang menentukan** → 24 candle baru = cukup data baru untuk PELT mungkin menemukan regime baru

### Kenapa 24?
24 candle ≈ 1 "siklus" natural di sebagian besar timeframe:
- 5m: 24 candle = 2 jam (1 sesi trading Asia/Eropa/US)
- 1h: 24 candle = 1 hari (1 full trading day)
- 1d: 24 candle = 24 hari (hampir 1 bulan)

Ini bukan angka ajaib — ini **parameter infrastruktur** yang bisa di-adjust. Tapi 24 adalah default yang masuk akal untuk semua TF.

### Filosofi
Sama seperti `window_days`, parameter `interval_candles` mengatakan "berapa banyak data baru yang harus terkumpul sebelum mesin melihat ulang." Mesin yang memutuskan hasilnya (penalty tetap atau berubah), manusia hanya menentukan kapan mesin harus melihat.

---

## 5. Arsitektur Dua Proses

### 5.1 LIVE Process — `scripts/run_live.py`

```
┌─────────────────────────────────────────────────┐
│  LIVE PROCESS                                   │
│                                                 │
│  Trigger: sesuai timeframe                      │
│    - TF 1h → scheduler jalankan setiap jam      │
│    - TF 5m → scheduler jalankan setiap 5 menit  │
│                                                 │
│  Flow:                                          │
│    1. Baca penalty dari SQLite (instant)         │
│    2. L1 → L2 → L3 (tanpa meta-learning)        │
│    3. Simpan score ke SQLite                     │
│    4. Output signal + HTML report               │
│                                                 │
│  Kecepatan: DETIK                               │
│  Meta-learning: TIDAK ADA                       │
└─────────────────────────────────────────────────┘
```

**Apa yang TIDAK ada di LIVE:**
- `meta_learn_all_scopes()` — TIDAK dipanggil
- `force_refresh_if_needed()` — TIDAK dipanggil
- Semua PELT loop untuk cari penalty — TIDAK ada

**Apa yang TETAP ada:**
- L1 existence check (verify data dari exchange)
- L2 aggregate z-scores (PAKAI penalty dari SQLite, bukan compute)
- L3 threshold + signal generation
- Score append ke SQLite
- HTML report generation

### 5.2 CALIBRATION Process — `scripts/run_calibration.py`

```
┌─────────────────────────────────────────────────┐
│  CALIBRATION PROCESS                            │
│                                                 │
│  Trigger: loop terus-menerus                    │
│                                                 │
│  Flow:                                          │
│    while True:                                  │
│      for combo in active_combos:                │
│        new_candles = count_since_last(combo)     │
│        if new_candles >= interval_candles:       │
│          1. Fetch data (window_days worth)       │
│          2. Run meta-learning (PELT × CH)       │
│          3. Simpan penalty ke SQLite             │
│        else:                                    │
│          skip (instant)                         │
│      sleep(60)  # Cek lagi dalam 1 menit       │
│                                                 │
│  Kecepatan: Detik per cycle (mostly skip)       │
└─────────────────────────────────────────────────┘
```

**Yang dilakukan CALIBRATION:**
- Cek setiap combo: sudah 24 candle baru belum?
- Kalau belum → skip (0 detik)
- Kalau sudah → fetch data, cap ke window_size, run PELT × 39, simpan penalty

**Yang TIDAK dilakukan:**
- Menghasilkan trading signal
- Menulis score history
- Generate HTML report

### 5.3 Diagram Interaksi

```
     CALIBRATION                         LIVE
    (loop terus)                    (sesuai TF)
         │                              │
         │ WRITE penalties              │ READ penalties
         │ to SQLite                    │ from SQLite
         │                              │
         ▼                              ▼
    ┌────────────────────────────────────────┐
    │           data/tte.db (SQLite)         │
    │                                        │
    │  penalties ← CALIBRATION writes        │
    │  score_history ← LIVE writes           │
    │  snapshot_history ← LIVE writes        │
    │              (via connectors)           │
    └────────────────────────────────────────┘
```

---

## 6. Cara Calibration Menentukan "Sudah 24 Candle?"

### Mekanisme
Tabel `penalties` di SQLite sudah punya kolom `updated_at` (timestamp terakhir kalibrasi). Calibration menghitung:

```python
last_calibrated = get_last_calibrated_time(exchange, pair, timeframe)
now = datetime.now(timezone.utc)
seconds_since = (now - last_calibrated).total_seconds()
candles_since = seconds_since / TIMEFRAME_SECONDS[timeframe]

if candles_since >= interval_candles:  # 24
    recalibrate(exchange, pair, timeframe)
```

Tidak perlu menghitung jumlah candle yang benar-benar masuk ke database. Cukup hitung waktu yang sudah berlalu dan konversi ke jumlah candle. Ini karena candle datang secara reguler (1h TF = 1 candle per jam, pasti).

### Kasus Pertama Kali (Belum Ada Penalty)
Jika combo baru ditambahkan dan belum punya penalty di SQLite:
- `last_calibrated` = None
- Calibration langsung run (force calibrate)
- Penalty disimpan ke SQLite
- LIVE bisa mulai menggunakan penalty tersebut

---

## 7. Estimasi Beban Komputasi

### Per Calibration Cycle (1 loop penuh semua combo)

| Skala | Total Combo | Perlu Recalibrate | Skip | Waktu Cycle |
|---|---|---|---|---|
| **MVP** | 8 | ~1 | 7 | **~2 detik** |
| **Medium** | 100 | ~5 | 95 | **~10 detik** |
| **Large** | 1000 | ~30 | 970 | **~1 menit** |

### Kenapa Kebanyakan Skip?
Karena `interval_candles: 24` berarti:
- TF 1h: recalibrate 1× per hari → dalam 1 jam loop, 23 dari 24 cycle = skip
- TF 4h: recalibrate 1× per 4 hari → hampir selalu skip
- TF 1d: recalibrate 1× per 24 hari → hampir selalu skip

Satu-satunya TF yang sering recalibrate adalah 5m (setiap 2 jam), dan itupun cuma ~2 detik per recalibrate karena window di-cap ke 500.

---

## 8. Apa Yang Berubah dari Blueprint Sebelumnya

### Disederhanakan
| Sebelumnya (terlalu kompleks) | Sekarang (2 parameter) |
|---|---|
| Variance check (20% threshold) | ❌ Dihapus |
| Mean check (10% threshold) | ❌ Dihapus |
| 14-hari fallback | ❌ Tidak perlu |
| Simpan mean+variance per combo | ❌ Tidak perlu |
| 4 parameter + logic kompleks | ✅ **2 parameter saja** |

### Tetap Valid (dari DeepSeek)
Saran DeepSeek tentang variance+mean check **tidak dibuang**, tapi di-downgrade ke **optional optimization** untuk nanti kalau combo sudah 1000+. Untuk MVP sampai 100 combo, `interval_candles` sudah lebih dari cukup.

---

## 9. Perubahan File

### File BARU
| File | Purpose |
|---|---|
| `scripts/run_live.py` | LIVE entry point — pipeline tanpa meta-learning |
| `scripts/run_calibration.py` | CALIBRATION entry point — loop meta-learning |

### File DIMODIFIKASI
| File | Perubahan |
|---|---|
| `config/infrastructure.yaml` | Tambah section `calibration:` |
| `src/layer2/meta_learning.py` | Tambah window cap sebelum `find_optimal_penalty()` |
| `src/layer3/meta_learning.py` | Tambah window cap (sama) |

### File DEPRECATED
| File | Alasan |
|---|---|
| `scripts/run_pipeline.py` | Digantikan oleh `run_live.py` |
| `scripts/run_meta_learning.py` | Digantikan oleh `run_calibration.py` |

### File TIDAK BERUBAH
- Seluruh `src/layer1/` — Connectors, verifier
- `src/layer2/aggregator.py` — Z-score aggregation
- `src/layer2/cpd.py` — CPD logic
- `src/layer3/` — Executor, risk, thresholds
- `src/utils/database.py` — SQLite (sudah ready)
- `src/utils/history_manager.py` — (sudah ready)

---

## 10. Config Baru: `trading_pairs.yaml`

Untuk memberitahu LIVE dan CALIBRATION combo mana yang aktif:

```yaml
# config/trading_pairs.yaml
active_pairs:
  gateio:
    ETH_USDT:
      timeframes: ["1h"]
      market_type: perpetual
```

**Menambah pair/TF baru:**
```yaml
active_pairs:
  gateio:
    ETH_USDT:
      timeframes: ["1h", "4h"]       # Tambah 4h
      market_type: perpetual
    BTC_USDT:                         # Tambah BTC
      timeframes: ["1h"]
      market_type: perpetual
  binance:                            # Tambah exchange
    ETH_USDT:
      timeframes: ["5m", "1h"]
      market_type: perpetual
```

Tidak ada perubahan kode. Cukup edit file ini.

---

## 11. Urutan Eksekusi

| Step | Action | Dependensi |
|---|---|---|
| 1 | Update `config/infrastructure.yaml` — tambah section calibration | Tidak ada |
| 2 | Buat `config/trading_pairs.yaml` — definisi combo aktif | Tidak ada |
| 3 | Tambah window cap di `src/layer2/meta_learning.py` | Step 1 |
| 4 | Tambah window cap di `src/layer3/meta_learning.py` | Step 1 |
| 5 | Buat `scripts/run_live.py` — LIVE tanpa meta-learning | Step 1 |
| 6 | Buat `scripts/run_calibration.py` — loop dengan interval_candles | Step 1, 2, 3, 4 |
| 7 | Test: jalankan calibration 1× + live 1× | Step 5, 6 |
| 8 | Update scheduler scripts (.ps1) | Step 7 |

---

## 12. Ringkasan

```
┌──────────────────────────────────────────────┐
│              INFRASTRUCTURE.YAML             │
│                                              │
│  calibration:                                │
│    window_days: 7        ← solve latency     │
│    interval_candles: 24  ← solve scheduling  │
│    min_window: 50        ← PELT minimum      │
│    max_window: 500       ← komputasi cap     │
│                                              │
│  Dua parameter. Auto-adjust per TF.          │
│  Gak perlu sentuh lagi saat nambah pair.     │
└──────────────────────────────────────────────┘

┌──────────────────────────────────────────────┐
│           TRADING_PAIRS.YAML                 │
│                                              │
│  active_pairs:                               │
│    gateio:                                   │
│      ETH_USDT:                               │
│        timeframes: ["1h"]                    │
│                                              │
│  Nambah pair = nambah baris di sini.         │
│  Gak ada perubahan kode.                     │
└──────────────────────────────────────────────┘
```

**Filosofi tetap utuh:** Data is King. Manusia hanya menentukan infrastruktur (berapa data yang dilihat, seberapa sering mesin melihat). Mesin yang menentukan hasilnya (penalty berapa, regime apa, signal apa).
