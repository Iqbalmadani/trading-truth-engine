# TTE v2 — Migration & Architecture Upgrade Complete
## Changelog & System Status

**Date:** 14 Mei 2026  
**Status:** ✅ SELESAI — Semua test passed

---

## Apa Yang Sudah Dilakukan

### Phase 1: SQLite Migration ✅
- Migrasi `score_history.json` → SQLite tabel `score_history` (632 entries)
- Migrasi `snapshot_history.json` → SQLite tabel `snapshot_history` (1939 entries)
- Migrasi `penalties.yaml` → SQLite tabel `penalties` (7 entries)
- Namespace isolation: semua data per `exchange/pair/timeframe`
- File JSON/YAML lama **dihapus** (bukan backup, bersih)

### Phase 2: LIVE/CALIBRATION Split ✅
- `run_live.py` — pipeline tanpa meta-learning (**17 detik**, dari ~10 menit)
- `run_calibration.py` — loop meta-learning dengan `interval_candles` trigger
- Window cap di meta-learning (auto-adjust per TF)
- Cleanup file dead (penalties.yaml, template, JSON files)

---

## Struktur File Setelah Upgrade

### `data/`
```
data/
  └── tte.db              ← Satu-satunya file data. SQLite database.
```

### `config/`
```
config/
  ├── sources.yaml        ← Definisi scope (ohlcv, funding_rate, dll)
  ├── infrastructure.yaml ← Retry, timeout + section calibration baru
  └── trading_pairs.yaml  ← Daftar active pairs/TF/exchange
```

### `scripts/`
```
scripts/
  ├── run_live.py          ← LIVE process (sinyal, detik, tanpa meta-learning)
  ├── run_calibration.py   ← CALIBRATION process (loop meta-learning)
  ├── run_pipeline.py      ← Legacy (masih bisa dipakai, tapi deprecated)
  ├── run_meta_learning.py ← Legacy (digantikan run_calibration.py)
  └── migrate_to_sqlite.py ← One-time migration (sudah dijalankan)
```

### `src/utils/`
```
src/utils/
  ├── database.py          ← BARU: SQLite connection + query helpers
  ├── history_manager.py   ← DIUBAH: JSON → SQLite
  └── config_loader.py     ← Tidak berubah
```

---

## Cara Pakai

### Menjalankan Trading Signal (LIVE)
```bash
python scripts/run_live.py
```
Baca combo dari `trading_pairs.yaml`, jalankan pipeline untuk masing-masing.
Kecepatan: **detik**.

### Menjalankan Kalibrasi (CALIBRATION)
```bash
# Single pass (test)
python scripts/run_calibration.py --once

# Loop terus-menerus (production)
python scripts/run_calibration.py
```
Loop semua combo, recalibrate yang sudah `interval_candles` baru.

### Menambah Pair/TF Baru
Edit `config/trading_pairs.yaml`:
```yaml
active_pairs:
  gateio:
    ETH_USDT:
      timeframes: ["1h", "4h"]    # Tambah 4h
      market_type: perpetual
    BTC_USDT:                      # Tambah BTC
      timeframes: ["1h"]
      market_type: perpetual
```
Tidak ada perubahan kode. LIVE dan CALIBRATION otomatis proses combo baru.

---

## 2 Parameter Kalibrasi

Di `config/infrastructure.yaml`:
```yaml
calibration:
  window_days: 7          # Berapa hari data yang dilihat PELT
  interval_candles: 24    # Setiap berapa candle baru, recalibrate
  min_window: 50          # Minimum data untuk PELT
  max_window: 500         # Maximum data (O(n²) cap)
```

Auto-adjust per TF:

| TF | Window | Recalibrate Setiap |
|---|---|---|
| 5m | 500 candle | 2 jam |
| 1h | 168 candle | 24 jam |
| 4h | 50 candle | 4 hari |
| 1d | 50 candle | 24 hari |

---

## Test Results

### LIVE Test (14 Mei 2026, 09:23)
```
Duration: 17 seconds (was ~10 minutes)
L1: 5/5 scopes ✅
L2: score = 0.1691, penalties from SQLite ✅
L3: Signal = NEUTRAL (score within thresholds) ✅
Score saved to SQLite (total: 634) ✅
HTML report generated ✅
```

### CALIBRATION Test (14 Mei 2026, 09:23)
```
Mode: single pass (--once)
gateio/ETH_USDT/1h: SKIPPED (< 24 candles since last calibration) ✅
Duration: < 1 second ✅
```

---

## Dokumentasi Terkait

| Dokumen | Isi |
|---|---|
| `docs/sqlite_migration.md` | Detail migrasi JSON → SQLite |
| `docs/live_calibration_split.md` | Arsitektur LIVE vs CALIBRATION |
| `docs/tte_v2_blueprint.md` | Blueprint awal (referensi) |
| `docs/consolidated_audit_review.md` | Audit review (35 klaim, verdict) |
