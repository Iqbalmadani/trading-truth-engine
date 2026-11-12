# Arsip Dokumentasi: Kategori B (EVR — Expected Value Ratio) & Calibration

> **Status Sistem**: DIBEKUKAN / DIARSIPKAN (Frozen / Archived)  
> Dokumen ini menyimpan sejarah, landasan teori, dan formula matematika dari **Kategori B (EVR)** yang pernah dirancang untuk Trading Truth Engine (TTE).  
> **Catatan Arsitektur**: Seluruh kode Kategori B (`outcome_evaluator.py` & `signal_logger.py`) telah **dihapus dari `src/layer3/`** agar Layer 1, 2, dan 3 tetap 100% murni, stateless, dan berbasis data struktural (Kategori A).

---

## 1. Definisi Kategori B vs Kategori A

* **Kategori A (Ex-Ante / Structural):** Parameter eksekusi mentah yang dihitung langsung dari sifat data dan struktur harga saat ini (PELT Change Point Detection, Z-Score, Regime Width). Kategori A bekerja secara *real-time* tanpa memerlukan riwayat hasil trading (*trade outcomes*).
* **Kategori B (Ex-Post / Empirical Learning):** Parameter eksekusi yang disesuaikan secara retrospektif berdasarkan evaluasi performa eksekusi terdahulu (TP_HIT, SL_HIT, EXPIRED).

---

## 2. Landasan Matematika EVR (Expected Value Ratio)

EVR dirancang untuk mengukur efisiensi relatif dari penempatan Stop Loss (SL) dan Take Profit (TP) berdasarkan hasil trade nyata:

$$\text{EVR} = \frac{\mathbb{E}[\text{Profit}]}{\mathbb{E}[\text{Loss}]} = \frac{p \times \text{Average Win}}{(1 - p) \times \text{Average Loss}}$$

Dimana:
* $p$ = Probabilitas trade berhasil (Win Rate)
* $\text{Average Win}$ = Rata-rata jarak persentase eksekusi menyentuh TP
* $\text{Average Loss}$ = Rata-rata jarak persentase eksekusi menyentuh SL

### Multiplier Dihasilkan EVR:
1. **SL Multiplier ($m_{\text{SL}}$):** Jika rasio kekalahan terlalu cepat akibat kebisingan pasar (*noise*), multiplier melonggarkan SL ($m_{\text{SL}} > 1.0$).
2. **TP Multiplier ($m_{\text{TP}}$):** Jika target TP terlalu optimis dan jarang tersentuh, multiplier mengetatkan TP ($m_{\text{TP}} < 1.0$).
3. **Leverage Multiplier ($m_{\text{Lev}}$):** Menyesuaikan eksposur berdasarkan ekspektasi nilai positif dari rangkaian trade.

---

## 3. Mengapa Kategori B Dipindahkan / Dibekukan dari Layer 3?

1. **Prinsip Separation of Concerns (Separasi Arsitektur):**
   Layer 1, 2, dan 3 bertindak sebagai **Mesin Analisis & Penghasil Sinyal Mentah**. Menggabungkan evaluasi hasil database ke Layer 3 menciptakan *circular dependency* dan membuat Layer 3 bersifat *stateful*.
2. **Standardisasi Industri Quant:**
   Di industri trading kuantitatif, evaluasi performa historis dan penyesuaian parameter eksekusi berada di bawah domain **Layer 4 (Execution & Portfolio Management System)**.

---

## 4. Riwayat Referensi File (Sudah Dihapus dari `src/`)

File-file yang pernah mengimplementasikan konsep ini sebelum diarsip:
* `src/layer3/outcome_evaluator.py` (Fungsi evaluasi EVR)
* `src/layer3/signal_logger.py` (Logging sinyal ke DB `signal_history`)

---
*Arsip dibuat pada: 2026-07-22 untuk menjaga keutuhan riwayat intelektual Trading Truth Engine.*
