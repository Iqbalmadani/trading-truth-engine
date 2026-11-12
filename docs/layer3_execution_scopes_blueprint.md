# Layer 3 Execution Scopes — Arsitektur Blueprint

## Latar Belakang: Mengapa Dokumen Ini Ada

TTE memiliki arsitektur berlapis dimana setiap layer menambahkan dimensi intelligence:

- **Layer 1** → Verifikasi keberadaan data (biner 0/1)
- **Layer 2** → Analisis struktural data → menghasilkan score
- **Layer 3** → Eksekusi trading berdasarkan score

Layer 1 dan Layer 2 sudah **selesai secara arsitektur**. Semua scope terdefinisi, semua terkalibrasi, dan kemudahan penambahan scope baru sudah terjamin melalui `config/sources.yaml`.

Layer 3 **belum selesai**. Dokumen ini menjelaskan mengapa dan bagaimana menyelesaikannya.

---

## Anatomi Scope di TTE

### Layer 2: Scope = Instrumen Analisis

Layer 2 memiliki scope yang terdefinisi jelas di `config/sources.yaml`:

```
ohlcv                  → data candle (open, high, low, close, volume)
funding_rate           → biaya funding perpetual futures
open_interest          → total kontrak terbuka
leverage_pressure      → tekanan leverage (total posisi)
volatility_compression → kompresi volatilitas (std returns)
```

Setiap scope memiliki:
1. **Definisi** di `sources.yaml` (data_type, required_fields)
2. **Konektor** di `src/layer1/connectors/` (fetch data dari exchange)
3. **Penalty** dari meta-learning (CH mengevaluasi kualitas segmentasi PELT)
4. **Model** dari meta-learning (l1, l2, atau rbf — dipilih data)
5. **Storage** di SQLite `penalties` table (terisolasi per namespace)

**Semua scope identik secara arsitektur.** Menambah scope baru = tambah entry di `sources.yaml` + tambah fetch method di konektor. Tidak ada perubahan logika.

**Sifat scope L2: 100% statistika murni.** Data market masuk → PELT segmentasi → Z-score keluar. Tidak ada judgment manusia. Tidak ada yang perlu dievaluasi dari "hasil" — kualitas diukur dari struktur data itu sendiri (CH).

### Layer 3: Scope = Instrumen Eksekusi (BELUM TERDEFINISI)

Berbeda dengan Layer 2, Layer 3 **tidak mendefinisikan scope-nya secara eksplisit**. Instrumen eksekusi tersebar di berbagai file tanpa kategorisasi:

| Instrumen | Lokasi di Kode | Bagaimana Ditentukan | Dikalibrasi? |
|---|---|---|---|
| Entry signal | `thresholds.py` → `executor.py` | CPD pada score_history → min/max rezim | ✅ Via `score` penalty |
| SL placement | `risk.py` baris 76-77, 88-93 | `min(regime)` / `max(regime)` | ❌ Formula tetap |
| TP placement | `risk.py` baris 76-77, 88-93 | `max(regime)` / `min(regime)` | ❌ Formula tetap |
| Leverage | `risk.py` baris 188-201 | `vol_history / vol_current` | ❌ Formula tetap |
| RR ratio | `executor.py` baris 198-208 | `reward / risk` | ❌ Dihitung, tidak dievaluasi |

**Ini adalah masalah arsitektur, bukan masalah logika.** Setiap formula di atas secara individual tidak salah. Tapi tidak satupun yang dikalibrasi — berbeda drastis dengan Layer 2 di mana setiap scope punya meta-learning.

---

## Meta-Learning Layer 3 Saat Ini

Di `src/layer3/meta_learning.py`, hanya ada 2 scope:

```python
# Scope 1: score penalty
meta_learn_score_penalty(score_history)
# → Menemukan penalty optimal untuk PELT pada distribusi score
# → Dipakai oleh thresholds.py untuk menentukan entry signal
# → Dikalibrasi via CH ✅

# Scope 2: price penalty  
meta_learn_price_penalty(price_history)
# → Menemukan penalty optimal untuk PELT pada harga
# → Dipakai oleh risk.py untuk menentukan batas rezim harga
# → Dikalibrasi via CH ✅
```

Kedua scope ini bersifat **statistika murni** — sama persis dengan scope L2. CH bisa mengevaluasi kualitas segmentasi tanpa perlu tahu hasil trading.

**Masalah:** Scope ini hanya mengkalibrasi BAGAIMANA PELT memotong data. Mereka **tidak** mengkalibrasi APA YANG DILAKUKAN dengan hasil potongan itu:

```
PELT memotong price_history menjadi rezim [2244 — 2276] ← dikalibrasi ✅
SL = 2276, TP = 2244 (ambil batas rezim mentah)            ← TIDAK dikalibrasi ❌
Leverage = vol_history / vol_current                        ← TIDAK dikalibrasi ❌
```

---

## Dua Kategori Instrumen Eksekusi

Inilah inti dari arsitektur yang perlu diterapkan. Instrumen eksekusi di Layer 3 terbagi menjadi **dua kategori yang secara fundamental berbeda**:

### Kategori A: Statistika Struktural
**Bisa dikalibrasi dari data market SEKARANG — sama seperti Layer 2.**

| Scope | Fungsi | Metrik Kalibrasi | Status |
|---|---|---|---|
| `entry_signal` | Kapan masuk (LONG/SHORT/Neutral) | CH pada segmentasi score | ✅ Ada |
| `regime_detection` | Dimana batas rezim harga | CH pada segmentasi price | ✅ Ada |

Kategori A sudah selesai. Meta-learning mengevaluasi kualitas segmentasi menggunakan Calinski-Harabasz. Tidak perlu data outcome.

### Kategori B: Kualitas Eksekusi
**Hanya bisa dikalibrasi dari HASIL NYATA — butuh data outcome.**

| Scope | Pertanyaan | Data Yang Dibutuhkan | Status |
|---|---|---|---|
| `sl_quality` | Apakah SL di batas rezim efektif? | SL hit rate per TF | ❌ Belum ada |
| `tp_quality` | Apakah TP di batas rezim realistis? | TP hit rate per TF | ❌ Belum ada |
| `leverage_quality` | Apakah vol ratio menghasilkan sizing tepat? | P&L per leverage level | ❌ Belum ada |
| `signal_confidence` | Apakah sinyal jenis ini historisnya profitable? | Win rate per signal type | ❌ Belum ada |

**Perbedaan fundamental:**

```
Kategori A: "Apakah PELT memotong data dengan benar?"
  → Jawab SEKARANG dari data yang ada
  → Metrik: CH (kualitas segmentasi)

Kategori B: "Apakah KEPUTUSAN dari potongan itu menghasilkan profit?"
  → Jawab NANTI setelah melihat apa yang terjadi di market
  → Metrik: win rate, hit rate, expected value
```

**Saat ini TTE memperlakukan semua instrumen eksekusi seolah-olah Kategori A.** SL = `min(regime)` selesai, tidak pernah dievaluasi. Ini sama seperti jika Layer 2 menggunakan `penalty = 1.0` hardcoded untuk semua scope tanpa meta-learning.

---

## Mengapa L2 Tidak Punya Kategori B

Di Layer 2, **semua scope secara alamiah adalah Kategori A**. Layer 2 bertugas MENGANALISIS struktur data — bukan membuat keputusan yang bisa "benar" atau "salah" berdasarkan hasil masa depan.

```
L2 scope ohlcv:
  Input:  50 candle OHLCV
  Proses: PELT → rezim → Z-score
  Output: z = -1.08
  Evaluasi: "Apakah segmentasi PELT berkualitas?" → CH bisa jawab SEKARANG

Tidak ada pertanyaan "apakah z-score -1.08 itu benar?" — itu adalah FAKTA struktural.
```

Layer 3 berbeda karena dia membuat **keputusan yang bisa gagal**:

```
L3 instrument SL:
  Input:  rezim harga [2244 — 2276]
  Proses: SL = min(rezim) = 2276 (untuk SHORT)
  Output: SL = 2276
  Evaluasi: "Apakah 2276 itu SL yang bagus?" → TIDAK BISA dijawab sekarang
            → Harus tunggu: apakah harga sentuh 2276 sebelum sentuh TP?
```

Inilah mengapa Layer 3 MEMBUTUHKAN Kategori B — dan Layer 2 tidak.

---

## Arsitektur Baru: Layer 3 + Layer 4

### Layer 3 (Execution Intelligence)

Layer 3 tetap bertugas sebagai pembuat keputusan trading. Perubahan arsitektur:

1. **Scope instrumen eksekusi didefinisikan secara eksplisit** (seperti `sources.yaml` di L2)
2. **Scope Kategori A** tetap dikalibrasi oleh meta-learning via CH (tidak berubah)
3. **Scope Kategori B** dikalibrasi dari data outcome yang dikirim oleh Layer 4

```
config/execution_scopes.yaml (BARU):

scopes:
  # === Kategori A: Statistika Struktural ===
  entry_signal:
    category: structural
    calibration: meta_learning_ch
    source: score_history
    description: "Menentukan entry LONG/SHORT/Neutral dari threshold CPD"
    
  regime_detection:
    category: structural
    calibration: meta_learning_ch
    source: price_history
    description: "Mendeteksi rezim harga untuk batas SL/TP"

  # === Kategori B: Kualitas Eksekusi ===
  sl_quality:
    category: outcome
    calibration: signal_outcome
    source: signal_history
    description: "Evaluasi efektivitas penempatan stop loss"
    
  tp_quality:
    category: outcome
    calibration: signal_outcome
    source: signal_history
    description: "Evaluasi realisasi target profit"

  leverage_quality:
    category: outcome
    calibration: signal_outcome
    source: signal_history
    description: "Evaluasi kesesuaian ukuran leverage"

  signal_confidence:
    category: outcome
    calibration: signal_outcome
    source: signal_history
    description: "Evaluasi profitabilitas per jenis sinyal"
```

**Menambah scope eksekusi baru** = tambah entry di YAML + implementasi evaluator. Sama mudahnya dengan menambah scope di Layer 2.

### Layer 4 (Monitor + Executor)

Layer 4 adalah layer baru yang memiliki dua fungsi:

**Fungsi 1: Eksekusi** — Menjalankan sinyal dari Layer 3 di exchange via API
**Fungsi 2: Monitor** — Mencatat outcome dan mengirimnya kembali ke Layer 3

```
Flow Data:

L1 → L2 (hirarki turun — L1 menyediakan data terverifikasi)
  L1 menyediakan: data market terverifikasi (exists/not exists)
  L2 memproses: analisis struktural → score

L2 → L3 (hirarki turun — L2 menyediakan metadata)
  L2 menyediakan: score + metadata analisis
  L3 memproses: keputusan eksekusi → sinyal trading

L3 → L4 (sinyal mengalir TURUN)
  L3 menyediakan: sinyal (SHORT/LONG, entry, SL, TP, leverage)
  L4 memproses: eksekusi di exchange + monitoring posisi

L4 → L3 (outcome mengalir NAIK — feedback loop)
  L4 menyediakan: data outcome (TP_HIT, SL_HIT, EXPIRED, P&L)
  L3 memproses: kalibrasi scope Kategori B dari data outcome
```

**Perbandingan L1→L2 vs L4→L3:**

| Aspek | L1 → L2 | L4 → L3 |
|---|---|---|
| Yang dikirim | Data market terverifikasi | Data outcome trading |
| Untuk apa | Kalibrasi scope analisis (CH) | Kalibrasi scope eksekusi (win rate) |
| Arah | Top-down (searah) | Bottom-up (feedback) |
| Frekuensi | Setiap run | Setiap sinyal selesai |

---

## Kondisi TTE Saat Ini vs Target

```
                    SEKARANG                         TARGET
                    ────────                         ──────

Layer 1          ✅ Solid                          ✅ Tidak berubah
(Existence)      5 scope di sources.yaml
                 Binary 0/1

Layer 2          ✅ Solid                          ✅ Tidak berubah
(Analysis)       5 scope instrumen analisis
                 100% terkalibrasi (CH)
                 Meta-learning per scope

Layer 3          ⚠️ Belum lengkap                  ✅ Scope terdefinisi
(Execution)      2 scope Kategori A (ada)          Kategori A: tetap (CH)
                 0 scope Kategori B (belum)        Kategori B: dari L4 outcome
                 Instrumen eksekusi hardcoded       Instrumen eksekusi = scope

Layer 4          ❌ Belum ada                       ✅ Monitor + Executor
(Monitor)                                          Eksekusi via exchange API
                                                   Catat outcome per sinyal
                                                   Feedback ke L3 Kategori B
```

---

## Roadmap Implementasi

### Phase 1: Signal Logger (Fondasi Data — Tanpa L4)
Sebelum L4 penuh, TTE bisa mulai MENCATAT sinyal tanpa mengeksekusi:

1. Tambah tabel `signal_history` di SQLite
2. Setiap `run_live.py` menghasilkan sinyal → catat ke tabel
3. Run berikutnya → cek sinyal sebelumnya: harga sudah sentuh TP/SL?
4. Update outcome di tabel

Ini mengumpulkan data Kategori B tanpa perlu L4 utuh.

### Phase 2: L3 Scope Definition
1. Buat `config/execution_scopes.yaml`
2. Definisikan scope Kategori A dan B secara eksplisit
3. Refactor `risk.py` dan `executor.py` agar setiap instrumen eksekusi menjadi scope yang bisa dikalibrasi

### Phase 3: L3 Outcome Calibration
Setelah cukup data outcome terkumpul (50+ sinyal per TF):
1. Implementasi evaluator per scope Kategori B
2. Meta-learning Kategori B: belajar dari data outcome
3. Scope Kategori B mulai aktif memfilter/mengkalibrasi keputusan eksekusi

### Phase 4: Layer 4 (Monitor + Executor)
1. Koneksi ke exchange API untuk eksekusi otomatis
2. Monitoring posisi real-time
3. Pencatatan outcome otomatis (menggantikan cek manual di Phase 1)
4. Feedback loop penuh L4→L3

---

## Prinsip Desain

1. **Scope adalah unit fundamental.** Seperti di L2, setiap instrumen harus menjadi scope yang terdefinisi, terkalibrasi, dan mudah ditambah.

2. **Kategori menentukan cara kalibrasi.** Kategori A = dari data market (CH). Kategori B = dari data outcome (win rate/hit rate). Keduanya valid, keduanya data-driven.

3. **Tidak ada hardcoded formula permanen.** `min(regime)` sebagai SL boleh menjadi DEFAULT awal, tapi harus bisa di-override oleh kalibrasi Kategori B setelah cukup data.

4. **Menambah scope harus semudah di L2.** Tambah YAML entry + implementasi evaluator. Tidak ada perubahan di orchestrator.

5. **Data is King — termasuk data outcome.** Data outcome dari L4 adalah data yang sama validnya dengan data market dari L1. Keduanya adalah fakta, bukan judgment.
