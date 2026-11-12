# BLUEPRINT SUPPLEMENT: TRADING TRUTH ENGINE
**Status: Ekstensi Blueprint Utama — Bukan Pengganti**  
**Tanggal: 23 Feb 2026**  
**Referensi: Blueprint_trading_truth_engine.md**

---

> [!IMPORTANT]
> Dokumen ini adalah lampiran resmi dari blueprint utama. Setiap keputusan di sini **berakar pada prinsip yang sudah ada di blueprint**. Tidak ada prinsip baru yang ditambahkan — hanya spesifikasi teknis yang melengkapi celah-celah yang belum terdefinisi.

---

## 1. SCOPE AWAL LAYER 1

**Prinsip asal:** *Layer 1 tidak peduli sumber datanya dari mana* (Blueprint, Layer 1 Larangan keras)

Layer 1 secara arsitektur **tidak terikat pada daftar API tertentu**. Logika existence check (0/1) bekerja identik untuk sumber data apapun. Menambah sumber data baru di masa depan hanya membutuhkan connector baru — tidak ada perubahan logika Layer 1.

### Scope Awal (Foundation)

Tiga scope dipilih sebagai fondasi awal karena ketersediaan konsisten di target platform:

| # | Scope | Alasan Dipilih | Sumber |
|---|---|---|---|
| 1 | **OHLCV** (candlestick) | Paling reliable, tersedia di semua exchange | Exchange API (kline endpoint) |
| 2 | **Funding Rate** | Ada di perpetual futures, membuktikan tekanan posisi pasar | Exchange API (funding rate endpoint) |
| 3 | **Open Interest** | Ada di perpetual futures, membuktikan modal nyata ter-commit | Exchange API (open interest endpoint) |

### Target Trading

- **Market type:** Perpetual futures (bisa diperluas ke spot/quarterly di masa depan)
- **Exchange:** Gate.io dan Binance

### Penambahan Scope di Masa Depan

Menambah scope baru (on-chain, forex, makro, dll.) bersifat **aditif murni:**
1. Buat connector baru di `src/layer1/connectors/`
2. Daftarkan di `config/sources.yaml`
3. Tidak ada refactoring, tidak ada perubahan logika

---

## 2. INPUT DARI MANUSIA

**Prinsip asal:** *Manusia hanya berperan sebagai penyala pertama* (Blueprint, Prinsip Dasar #5)

### Daftar Input

| # | Input | Contoh | Default | Keterangan |
|---|---|---|---|---|
| 1 | **Pair** | `ETH_USDT` | — (wajib) | Pasangan trading |
| 2 | **Platform** | `gateio`, `binance` | — (wajib) | Exchange target |
| 3 | **Timeframe** | `1h`, `4h`, `1d` | — (wajib) | Interval waktu |
| 4 | **Market Type** | `perpetual`, `spot`, `quarterly` | `perpetual` | Tipe pasar |

### Validasi Input

Setiap input divalidasi **sebelum** pipeline berjalan:

| Input | Metode Validasi | Sumber Kebenaran |
|---|---|---|
| Pair | Query exchange API — apakah kontrak ini ada? | Exchange API (contracts endpoint) |
| Platform | Cek terhadap daftar platform yang didukung | Konstanta di kode (ini batas sistem, bukan judgment data) |
| Timeframe | Cek terhadap interval yang didukung exchange API | Exchange API documentation |
| Market Type | Cek terhadap tipe pasar yang didukung | Konstanta di kode |

### Perilaku Jika Input Tidak Valid

**Hard stop. Tidak ada fallback. Tidak ada tebakan.**

```
Input tidak valid → Sistem TIDAK berjalan → Error message ke user
```

- Pair tidak ada di exchange → hard stop
- Platform tidak didukung → hard stop
- Timeframe tidak tersedia → hard stop
- Satu input saja yang gagal → seluruh pipeline tidak jalan

**Prinsip:** Fallback behavior (misal: "pair tidak ditemukan, coba yang mirip") adalah asumsi manusia yang diinjeksi ke sistem. Ini melanggar Prinsip Dasar #1.

---

## 3. STRUCTURAL SANITY CHECK DI LAYER 1

**Prinsip asal:** *Layer 1 hanya: ada atau tidak* (Blueprint, Layer 1) — diperluas dengan definisi teknis "ada"

### Definisi

"Data ada" bukan hanya "bytes diterima". Data dianggap **ada (= 1)** jika:
1. Data diterima (bukan `None`, bukan empty)
2. Data bisa di-parse sesuai tipe yang diharapkan
3. Timestamp data berada dalam interval yang sedang berjalan (bukan data basi)
4. Nilai berada dalam rentang yang **secara definisi** mungkin (bukan judgment manusia)

### Batas Tegas: Format vs. Makna

| ✅ Boleh di Layer 1 (format/structural) | ❌ Tidak boleh di Layer 1 (makna/judgment) |
|---|---|
| Apakah ini bisa di-parse sebagai tipe data yang benar? | Apakah nilai ini cukup tinggi? |
| Apakah timestamp dalam interval saat ini? | Apakah nilai ini anomali? |
| Apakah volume ≥ 0? (definisi: volume negatif secara matematis mustahil) | Apakah nilai ini menunjukkan arah? |
| Apakah semua field wajib terisi? | Apakah nilai ini lebih baik dari kemarin? |

**Prinsip:** Volume = -999 bukan "data jelek menurut manusia", tapi **data rusak menurut definisi teknis**. Layer 1 boleh menolak data rusak tanpa melanggar filosofi.

### Required Fields Per Scope

| Scope | Field Wajib | Validasi Struktural |
|---|---|---|
| **OHLCV** | open, high, low, close, volume, timestamp | Semua harus numerik dan parseable. Volume ≥ 0. High ≥ Low. Timestamp dalam interval. |
| **Funding Rate** | rate, timestamp | Rate harus numerik. Timestamp dalam interval. |
| **Open Interest** | value, timestamp | Value harus numerik dan ≥ 0. Timestamp dalam interval. |

Jika salah satu field wajib tidak ada atau gagal validasi struktural → **seluruh scope = 0**.

---

## 4. EDGE CASES — PERILAKU SISTEM

**Prinsip asal:** *Data adalah satu-satunya hakim* (Blueprint, Prinsip Dasar #1)

### 4A. Semua Scope Layer 1 = 0

**Hard stop. Tidak ada sinyal.**

Layer 2 tidak menerima data apapun → tidak bisa menghasilkan score → pipeline berhenti untuk interval ini. Event dicatat di log. Sistem menunggu interval berikutnya.

### 4B. Sebagian Scope = 0, Sisanya = 1

**Proses apa yang ada. Tidak ada minimum threshold.**

- Layer 2 menerima hanya scope yang lolos (= 1)
- Score dihitung dari scope yang tersedia
- **Scope count dilampirkan ke output score** → Layer 3 bisa melihat "score ini dari 1 sumber" vs "score ini dari 3 sumber"
- Apakah single-source score berperilaku berbeda → biarkan CPD di Layer 3 yang menemukan secara struktural
- Kita **tidak** hardcode judgment bahwa "1 sumber tidak cukup"

**Prinsip:** Minimum threshold adalah angka manusia. Jika dimasukkan, itu melanggar aturan konstitusi #7: *setiap angka yang muncul di kode harus bisa dijawab "dari mana angka ini?"*

### 4C. API Timeout atau Error Sementara

**Retry 3x, lalu mark sebagai 0.**

- Retry count (3) adalah parameter infrastruktur, bukan judgment data — setara dengan `PENALTY_CANDIDATES` yang manusia sediakan rentangnya
- Tidak menunggu tanpa batas (stall pipeline)
- Tidak menganggap timeout sebagai "mungkin 1" — ambiguitas bukan eksistensi
- Event dicatat di log

### 4D. Data Ada Tapi Tidak Lengkap

**Cek field wajib (lihat Bagian 3). Field hilang = 0.**

Ini adalah penerapan langsung dari structural sanity check. OHLCV tanpa `close` adalah data rusak, bukan data parsial yang bisa dipakai.

### 4E. Exchange Maintenance

**Perlakukan sama dengan 4C. API call gagal → retry → 0.**

- Sistem tidak perlu tahu *mengapa* data tidak tersedia
- Maintenance, outage, DNS failure — semua terlihat sama bagi Layer 1
- **Tidak boleh pakai data terakhir yang diketahui** — data basi melanggar validasi timestamp di structural sanity check
- Data basi yang dipakai sebagai data saat ini = data yang berpura-pura ada padahal tidak → pelanggaran langsung terhadap filosofi Layer 1

---

## 5. PERUBAHAN PADA LAYER 2 OUTPUT

**Prinsip asal:** *Score tidak boleh dimodifikasi sebelum dikirim ke Layer 3* (Blueprint, Layer 2 Larangan keras)

Score dari Layer 2 tetap tidak dimodifikasi. Namun output Layer 2 **diperluas** dengan metadata:

```python
# Output Layer 2 (diperluas)
{
    "score": float,           # Score agregat — tidak berubah
    "scope_count": int,       # Berapa scope yang berkontribusi
    "scopes_active": list,    # Nama scope yang lolos Layer 1
    "timestamp": str          # Timestamp interval
}
```

**Prinsip:** Ini bukan modifikasi score — ini metadata yang melekat. Score tetap `float` yang sama. `scope_count` adalah fakta, bukan filter.

---

## 6. RINGKASAN KEPUTUSAN

| # | Keputusan | Prinsip yang Dijaga |
|---|---|---|
| 1 | Scope awal: OHLCV, Funding Rate, Open Interest | Layer 1 source-agnostic, penambahan aditif |
| 2 | 4 input manusia: Pair, Platform, Timeframe, Market Type | Manusia = penyala pertama saja |
| 3 | Hard stop jika input invalid, tidak ada fallback | Data = satu-satunya hakim |
| 4 | Structural sanity check: format saja, bukan makna | Layer 1 = existence only |
| 5 | Tidak ada minimum scope threshold | Tidak ada angka manusia di logika |
| 6 | Scope count dilampirkan ke score | Score tidak dimodifikasi, metadata ditambahkan |
| 7 | Retry 3x lalu 0 untuk timeout/error | Parameter infrastruktur, bukan judgment |
| 8 | Tidak boleh pakai data basi dalam kondisi apapun | Eksistensi harus nyata, bukan cache |

---

## ATURAN: DOKUMEN INI TIDAK BERDIRI SENDIRI

Dokumen ini **harus dibaca bersama** dengan `Blueprint_trading_truth_engine.md`. Jika ada kontradiksi (seharusnya tidak ada), blueprint utama yang menang. Semua aturan konstitusi di blueprint utama tetap berlaku tanpa pengecualian.
