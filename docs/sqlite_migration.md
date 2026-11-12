# TTE SQLite Migration — Completed
## Migrasi Data Persistence dari JSON/YAML ke SQLite

**Date:** 14 Mei 2026  
**Status:** ✅ SELESAI — Pipeline tested & verified  
**Database:** `data/tte.db`

---

## Apa Yang Berubah

### Storage: JSON/YAML → SQLite
| Data | Sebelum | Sesudah |
|---|---|---|
| Score history (L2 scores) | `data/score_history.json` | Tabel `score_history` di SQLite |
| Snapshot history (OI, leverage, dll) | `data/snapshot_history.json` | Tabel `snapshot_history` di SQLite |
| Penalties (L2 + L3) | `config/penalties.yaml` | Tabel `penalties` di SQLite |

### Namespace Isolation (Multi-Pair Ready)
Setiap tabel memiliki kolom `exchange + pair + timeframe`. Data tidak akan tercampur antar namespace:

```
gateio / ETH_USDT / 1h  → data terisolasi
gateio / ETH_USDT / 5m  → data terisolasi (BEDA dari 1h)
binance / ETH_USDT / 1h → data terisolasi (BEDA dari gateio)
```

### File Yang Dimodifikasi
| File | Perubahan |
|---|---|
| `src/utils/database.py` | **BARU** — SQLite connection, schema, query helpers |
| `src/utils/history_manager.py` | Rewrite: JSON → SQLite |
| `src/layer2/penalty_manager.py` | Rewrite: YAML → SQLite |
| `src/layer3/penalty_manager.py` | Rewrite: YAML → SQLite |
| `scripts/run_pipeline.py` | Tambah parameter `exchange`, auto-derive dari connector |
| `src/layer2/aggregator.py` | Pass `pair/timeframe` ke `get_snapshot_history()` |
| `src/layer2/meta_learning.py` | Pass `pair/timeframe` ke `get_snapshot_history()` |
| `scripts/migrate_to_sqlite.py` | **BARU** — One-time migration script |

### File Yang TIDAK Berubah
- Semua logika matematika (PELT, BIC, CH, Z-Score)
- `src/layer1/` — Seluruh Layer 1 (verifier, connectors)
- `src/layer2/aggregator.py` — Logika aggregation
- `src/layer2/cpd.py` — CPD logic
- `src/layer3/executor.py` — Signal generation
- `src/layer3/risk.py` — SL/TP/Leverage
- `config/sources.yaml` — Scope definitions (tetap YAML)
- `config/infrastructure.yaml` — Infra config (tetap YAML)

---

## Config Files — Status

| File | Status |
|---|---|
| `config/penalties.yaml` | ❌ **Tidak dipakai lagi** — data di SQLite |
| `config/sources.yaml` | ✅ Masih dipakai — definisi scope |
| `config/infrastructure.yaml` | ✅ Masih dipakai — retry, timeout |

---

## Database Schema

### Tabel `score_history`
```sql
CREATE TABLE score_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,       -- 'gateio', 'binance'
    pair        TEXT NOT NULL,       -- 'ETH_USDT'
    timeframe   TEXT NOT NULL,       -- '1h'
    score       REAL NOT NULL,       -- L2 aggregate z-score
    created_at  TEXT NOT NULL        -- ISO 8601 UTC
);
```

### Tabel `snapshot_history`
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
```

### Tabel `penalties`
```sql
CREATE TABLE penalties (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    exchange    TEXT NOT NULL,
    pair        TEXT NOT NULL,
    timeframe   TEXT NOT NULL,
    layer       TEXT NOT NULL,       -- 'l2' atau 'l3'
    scope       TEXT NOT NULL,       -- 'ohlcv', 'score', 'price'
    penalty     REAL NOT NULL,
    model       TEXT NOT NULL,       -- 'l2', 'rbf', 'l1'
    updated_at  TEXT NOT NULL
);
-- UNIQUE constraint: 1 penalty per namespace+layer+scope
```

---

## Data Migrasi

Migrasi dilakukan oleh `scripts/migrate_to_sqlite.py`:

```
Scores:    632 entries  → gateio/ETH_USDT/1h
Snapshots: 1939 entries → gateio/ETH_USDT/1h
Penalties: 7 entries    → 5 L2 + 2 L3
```

File JSON/YAML lama **tetap ada** di disk sebagai backup.

---

## Test Results

Pipeline run **BERHASIL** setelah migrasi (14 Mei 2026, 07:41-07:50):

```
LAYER 1: 5/5 scopes EXISTS
LAYER 2: score = 5.9530 (all z-scores computed)
  - ohlcv:                 z = 1.4126
  - funding_rate:          z = 1.3932
  - open_interest:         z = 1.4141
  - leverage_pressure:     z = 1.4141
  - volatility_compression: z = 0.3189
Score appended to SQLite: gateio/ETH_USDT/1h (total: 633)
LAYER 3: Signal = NEUTRAL (price at regime high, data says don't trade)
L3 penalties saved to SQLite
HTML report generated
```

Semua data dibaca dari dan ditulis ke SQLite. Tidak ada lagi akses ke file JSON/YAML untuk data persistence.

---

## SQLite Concurrent Safety

Database menggunakan **WAL mode** (Write-Ahead Logging):
- Multiple readers bersamaan: ✅ Aman
- 1 writer + readers bersamaan: ✅ Aman
- Ini diperlukan untuk arsitektur LIVE + CALIBRATION nanti

---

## Langkah Selanjutnya (BELUM dieksekusi — freeze)

1. **Split LIVE/CALIBRATION** — Pisah `run_pipeline.py` jadi dua entry point
2. **Calibration window cap** — Batasi data yang di-feed ke PELT per timeframe
3. **Strategi calibration run** — Kapan dan seberapa sering calibration dijalankan
