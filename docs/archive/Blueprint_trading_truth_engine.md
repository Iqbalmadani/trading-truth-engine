# BLUEPRINT: TRADING TRUTH ENGINE
**Versi: Claude Final — Zero Hidden Assumption**  
**Tanggal: 23 Feb 2026**

---

## PRINSIP DASAR

1. Data adalah satu-satunya hakim.
2. Setiap layer terisolasi — layer bawah tidak tahu tujuan layer atas.
3. Parameter CPD dipilih melalui meta-learning berbasis data, bukan intuisi.
4. Self-mirroring: Layer 2 dan Layer 3 pakai prinsip yang sama (CPD + z-score).
5. Manusia hanya berperan sebagai penyala pertama — selebihnya data yang pegang kendali.

---

## LAYER 1 — THE EXISTENCE ORACLE

**Tugas:** Verifikasi apakah data dari suatu sumber benar-benar ada.  
**Input:** Raw data dari API manapun (exchange, on-chain, makro, dll.)  
**Output:** Biner 0/1 per sumber per interval waktu.

```python
def layer1(data) -> int:
    """
    Tidak ada interpretasi makna.
    Tidak ada perbandingan threshold.
    Tidak ada filter konten.
    Hanya: ada atau tidak.
    """
    if data is None:
        return 0
    if data == "":
        return 0
    if data == []:
        return 0
    if isinstance(data, dict) and len(data) == 0:
        return 0
    # Nilai numerik 0 tetap dianggap ada (informasi bahwa nilainya nol)
    return 1
```

**Larangan keras:**
- Tidak boleh ada angka threshold di dalam layer ini
- Tidak boleh ada perbandingan dengan historis
- Tidak boleh ada logika "jika volume > X"
- Layer ini tidak peduli sumber datanya dari mana

---

## LAYER 2 — THE STRUCTURAL ANALYZER

**Tugas:** Mengubah semua data yang lolos Layer 1 menjadi satu angka score.  
**Input:** Time-series dari setiap sumber (hanya yang Layer 1 = 1)  
**Output:** Satu angka riil (score) per interval waktu.

### Tahap 2.1 — CPD per sumber

```python
import ruptures as rpt

def get_cpd_window(series: list, penalty: float) -> list:
    """
    CPD menentukan window sendiri dari struktur data.
    Tidak ada angka window dari manusia.
    penalty adalah satu-satunya parameter — dipilih via meta-learning.
    """
    algo = rpt.Pelt(model="rbf").fit(series)
    change_points = algo.predict(pen=penalty)
    # Ambil change point terakhir sebagai batas window
    last_cp = change_points[-2] if len(change_points) > 1 else 0
    return series[last_cp:]
```

### Tahap 2.2 — Z-score dalam window CPD

```python
import numpy as np

def zscore_in_window(series: list, current_value: float) -> float:
    """
    Bandingkan nilai sekarang hanya dengan window yang CPD tentukan.
    Bukan dengan window 30 hari atau 50 hari dari manusia.
    """
    mu = np.mean(series)
    sigma = np.std(series)
    if sigma == 0:
        return 0
    return (current_value - mu) / sigma
```

### Tahap 2.3 — Agregasi semua sumber

```python
def layer2(sources: dict, penalties: dict) -> float:
    """
    sources: {"volume": [...], "funding_rate": [...], ...}
    penalties: {"volume": 2.1, "funding_rate": 1.8, ...} — dari meta-learning
    
    Z-score sudah mengandung arah (+/-) dan kekuatan (besar kecilnya).
    Tidak perlu weight tambahan dari manusia.
    """
    total_score = 0.0
    for name, series in sources.items():
        penalty = penalties[name]
        window = get_cpd_window(series, penalty)
        current = series[-1]
        z = zscore_in_window(window, current)
        total_score += z
    return total_score
```

**Larangan keras:**
- Tidak boleh ada metrik profit dalam evaluasi parameter CPD
- Tidak boleh ada filter tambahan di luar CPD + z-score
- Tidak boleh ada weight manual dari manusia
- Score tidak boleh dimodifikasi sebelum dikirim ke Layer 3

---

## LAYER 3 — THE STATISTICAL EXECUTIONER

**Tugas:** Terjemahkan score ke keputusan LONG/SHORT/0 beserta SL, TP, leverage.  
**Input:** Time-series score dari Layer 2 + data harga aset  
**Output:** Sinyal trading + entry + SL + TP + leverage

### Tahap 3.1 — Threshold dari CPD pada distribusi score

```python
def get_score_thresholds(score_history: list, penalty: float) -> tuple:
    """
    Threshold tidak ditentukan manusia (bukan 90%, bukan 80%).
    CPD membaca distribusi score dan menemukan batas alaminya sendiri.
    """
    algo = rpt.Pelt(model="rbf").fit(np.array(score_history))
    change_points = algo.predict(pen=penalty)
    
    # Ambil segmen terakhir yang strukturnya stabil
    last_cp = change_points[-2] if len(change_points) > 1 else 0
    recent_scores = score_history[last_cp:]
    
    # Batas alami dari segmen itu sendiri — bukan persentil dari manusia
    threshold_up = np.mean(recent_scores) + np.std(recent_scores)
    threshold_down = np.mean(recent_scores) - np.std(recent_scores)
    
    return threshold_up, threshold_down
```

### Tahap 3.2 — Keputusan entry

```python
def decide_entry(score: float, threshold_up: float, threshold_down: float) -> str:
    if score > threshold_up:
        return "LONG"
    elif score < threshold_down:
        return "SHORT"
    else:
        return "0"
```

### Tahap 3.3 — SL dan TP dari struktur harga

```python
def get_sl_tp(price_history: list, penalty: float, direction: str) -> tuple:
    """
    SL dan TP bukan dari ATR x 2 atau ATR x 4 (itu angka manusia).
    Tapi dari batas alami pergerakan harga dalam window CPD.
    """
    window = get_cpd_window(price_history, penalty)
    
    natural_high = max(window)
    natural_low = min(window)
    
    if direction == "LONG":
        sl = natural_low
        tp = natural_high
    elif direction == "SHORT":
        sl = natural_high
        tp = natural_low
    else:
        return None, None
    
    return sl, tp
```

### Tahap 3.4 — Leverage dari volatility dalam window CPD

```python
def get_leverage(price_history: list, penalty: float) -> float:
    """
    Leverage bukan dari leverage_max yang manusia tentukan.
    Tapi berbanding terbalik dengan volatility alami window CPD.
    Exchange limit (dari API exchange) adalah satu-satunya batas keras.
    """
    window = get_cpd_window(price_history, penalty)
    returns = np.diff(window) / window[:-1]
    
    vol_current = np.std(returns)
    vol_history = np.std(np.diff(price_history) / price_history[:-1])
    
    if vol_current == 0:
        return 1.0
    
    # Leverage naik saat volatility rendah, turun saat volatility tinggi
    # Skala dari data sendiri, bukan dari angka manusia
    leverage = vol_history / vol_current
    
    # Satu-satunya batas: limit dari exchange (diambil dari API, bukan di-hardcode)
    exchange_max = get_exchange_leverage_limit()  # dari API
    return min(leverage, exchange_max)
```

**Larangan keras:**
- Tidak boleh ada persentil tetap (90%, 10%, 5%, 95%)
- Tidak boleh ada multiplier ATR tetap (x2, x4)
- Tidak boleh ada leverage_max yang di-hardcode manusia
- Tidak boleh membaca data mentah — hanya boleh membaca score dari Layer 2

---

## META-LEARNING UNTUK PARAMETER CPD

Ini adalah satu-satunya titik di mana manusia memberikan instruksi awal.  
Instruksinya: "cari penalty terbaik" — data yang menjawab, bukan manusia.

```python
def meta_learn_penalty(series: list, penalty_candidates: list) -> float:
    """
    Metrik evaluasi: homogenitas segmen — bukan profit, bukan return.
    Semakin homogen segmen di dalam dan heterogen antar segmen → semakin baik.
    """
    best_penalty = None
    best_quality = -np.inf
    
    for penalty in penalty_candidates:
        algo = rpt.Pelt(model="rbf").fit(series)
        cps = algo.predict(pen=penalty)
        
        segments = []
        prev = 0
        for cp in cps:
            segments.append(series[prev:cp])
            prev = cp
        
        if len(segments) < 2:
            continue
        
        means = [np.mean(s) for s in segments if len(s) > 0]
        vars_within = [np.var(s) for s in segments if len(s) > 0]
        
        between_var = np.var(means)
        within_var = np.mean(vars_within) + 1e-9
        
        quality = between_var / within_var
        
        if quality > best_quality:
            best_quality = quality
            best_penalty = penalty
    
    return best_penalty

# Rentang yang dicoba — ini adalah batas teknis yang tidak bisa dihindari
# Manusia menyediakan rentang, data yang memilih nilai terbaiknya
PENALTY_CANDIDATES = [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
```

---

## STRUKTUR FOLDER

```
trading_truth_engine/
├── config/
│   ├── sources.yaml          # Daftar API — ditambah bebas kapan saja
│   ├── penalties.yaml        # Hasil meta-learning — diisi otomatis
│   └── exchange_limits.yaml  # Limit dari exchange — diambil dari API
│
├── src/
│   ├── layer1/
│   │   ├── __init__.py
│   │   ├── verifier.py       # Fungsi layer1()
│   │   └── connectors/       # Satu file per API source
│   │
│   ├── layer2/
│   │   ├── __init__.py
│   │   ├── cpd.py            # get_cpd_window()
│   │   ├── zscore.py         # zscore_in_window()
│   │   ├── aggregator.py     # layer2()
│   │   └── meta_learning.py  # meta_learn_penalty()
│   │
│   ├── layer3/
│   │   ├── __init__.py
│   │   ├── thresholds.py     # get_score_thresholds()
│   │   ├── risk.py           # get_sl_tp(), get_leverage()
│   │   └── executor.py       # decide_entry() + output final
│   │
│   ├── monitoring/
│   │   ├── quality_monitor.py    # Pantau homogenitas CPD
│   │   └── performance_monitor.py # Catat sinyal — READ ONLY, tidak ubah parameter
│   │
│   └── utils/
│       ├── data_loader.py
│       └── logger.py
│
├── scripts/
│   ├── run_pipeline.py        # Jalankan sistem real-time
│   └── run_meta_learning.py   # Kalibrasi penalty dari data terbaru
│
├── requirements.txt
└── blueprint.md               # Dokumen ini — konstitusi sistem
```

---

## ATURAN KONSTITUSI — TIDAK BOLEH DILANGGAR

1. Layer 1 tidak boleh mengandung angka threshold apapun.
2. Layer 2 tidak boleh mengandung weight manual atau window hardcoded.
3. Layer 3 tidak boleh mengandung persentil tetap, multiplier tetap, atau leverage_max hardcoded.
4. Meta-learning dievaluasi dengan metrik struktural (homogenitas), bukan profit.
5. Performance monitor hanya boleh membaca — tidak boleh mengubah parameter apapun.
6. Exchange leverage limit diambil dari API exchange, bukan di-hardcode.
7. Setiap angka yang muncul di kode harus bisa dijawab: "dari mana angka ini?" — kalau jawabannya "saya yang tentuin", itu pelanggaran.

---

## BATAS TEKNIS YANG DISADARI

Sistem ini tidak bisa 100% bebas dari sentuhan manusia. Yang tersisa dan disadari:

| Titik | Bentuk Keterlibatan Manusia | Cara Diminimalkan |
|---|---|---|
| Penalty CPD | Rentang kandidat disediakan manusia | Data yang pilih nilai terbaiknya |
| Algoritma CPD | Manusia pilih PELT atau BOCPD | Pilih yang paling domain-invariant |
| Instruksi meta-learning | Manusia tentukan metrik homogenitas | Metrik ini tidak mengandung profit |
| Lookback periods | Manusia tentukan berapa banyak data historis di-fetch dari API (default: 50) | CPD tetap menentukan window analisis sendiri dari data yang tersedia |

Manusia adalah **penyala pertama.** Setelah sistem menyala, data yang pegang kendali.