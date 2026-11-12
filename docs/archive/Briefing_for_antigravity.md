# BRIEFING UNTUK ANTIGRAVITY — TRADING TRUTH ENGINE
**Status: DISKUSI DULU, BELUM EKSEKUSI KODE**

---

## KONTEKS SISTEM

Saya sedang membangun sistem trading algoritmik bernama **Trading Truth Engine** dengan filosofi inti:
- Semua keputusan lahir dari data, bukan asumsi manusia
- Tiga layer yang saling terisolasi
- Self-mirroring: setiap layer pakai prinsip yang sama

Blueprint lengkap sudah ada di file terpisah (BLUEPRINT_TRADING_TRUTH_ENGINE.md). Baca itu dulu sebelum diskusi.

---

## INSTRUKSI UTAMA

**JANGAN eksekusi kode apapun sebelum semua poin di bawah selesai didiskusikan dan disepakati.**

Blueprint saat ini baru 60% selesai. Yang belum ada harus diselesaikan dulu lewat diskusi sebelum satu baris kode pun ditulis.

---

## YANG HARUS DIDISKUSIKAN — PRIORITAS TINGGI

### 1. Scope Layer 1 — API apa saja yang masuk

Layer 1 itu existence filter — dia hanya verifikasi data ada atau tidak (0/1). Tapi kita belum tentukan scope apa saja yang masuk. Yang perlu didiskusikan:

- API dari exchange (Gate.io / Binance) — scope apa saja yang tersedia dan valid?
- API on-chain — scope apa saja yang bisa dibuktikan eksis, bukan asumsi?
- API luar exchange lainnya — ada atau tidak yang relevan dan bisa diverifikasi?
- Untuk setiap scope: apakah datanya bisa dijamin ada secara konsisten atau ada risiko kosong?

### 2. Input dari user — apa saja yang boleh dimasukkan manusia

Saat ini disepakati hanya 3 input dari manusia:
- Pair (contoh: ETH_USDT)
- Platform (Gate.io / Binance)
- Timeframe (harian, mingguan, dll)

Yang perlu didiskusikan:
- Apakah ada input lain yang perlu ditambahkan?
- Bagaimana input ini divalidasi sebelum sistem jalan?
- Kalau input tidak valid (pair tidak ada di exchange), sistem harus bagaimana?

### 3. Edge Cases — kondisi yang belum ada di blueprint

Diskusikan dan tentukan perilaku sistem untuk setiap kondisi berikut:

- Semua scope Layer 1 = 0 (tidak ada data sama sekali) → sistem harus bagaimana?
- Sebagian besar scope = 0, hanya sedikit yang = 1 → apakah ada minimum scope yang harus lolos?
- API timeout atau error sementara → dianggap 0 atau ditunggu?
- Data dari API ada tapi tidak lengkap (sebagian field kosong) → 0 atau 1?
- Exchange sedang maintenance → sistem berhenti atau pakai data terakhir?

### 4. False Positive di Layer 1

Layer 1 saat ini hanya cek: ada atau tidak. Tapi ada risiko false positive:
- API mengembalikan data tapi nilainya jelas tidak valid (contoh: volume = -999)
- API mengembalikan data lama yang di-cache, bukan data real-time
- API mengembalikan format berbeda dari yang diharapkan

Diskusikan: apakah validasi teknis (bukan validasi makna) boleh ditambahkan di Layer 1 tanpa melanggar filosofi?

### 5. Minimum viable scope

Berapa scope minimum yang harus lolos Layer 1 agar Layer 2 bisa menghasilkan score yang bermakna? Atau tidak ada minimum — berapapun yang lolos tetap diproses?

---

## YANG SUDAH SOLID — JANGAN DIUBAH

Ini sudah disepakati dan tidak boleh diganggu gugat:

```
Layer 1 → existence only, 0/1, tidak ada angka threshold
Layer 2 → CPD per scope + z-score dalam window CPD, tidak ada weight manual
Layer 3 → threshold dari CPD pada distribusi score, SL/TP dari batas alami window harga
Meta-learning → evaluasi dengan homogenitas segmen, bukan profit
Leverage → dari rasio volatility data, batas keras dari API exchange
```

Kalau dalam diskusi ada saran yang mengubah hal-hal di atas, tolak dan jelaskan kenapa itu melanggar filosofi.

---

## CARA DISKUSI YANG DIHARAPKAN

- Ajukan pertanyaan satu per satu, mulai dari yang paling fundamental
- Kalau ada celah filosofi yang ditemukan, tunjukkan dengan konkret
- Jangan langsung kasih solusi kode — diskusi dulu sampai logikanya benar
- Kalau ada trade-off, jelaskan pilihan-pilihannya dan biarkan saya yang putuskan
- Setelah semua poin di atas selesai, buat dokumen tambahan yang melengkapi blueprint
- Baru setelah itu eksekusi kode boleh dimulai

---

## TARGET AKHIR DISKUSI

Blueprint yang mencapai 100% artinya:
- Semua scope Layer 1 sudah terdefinisi
- Semua input user sudah terdefinisi dan cara validasinya jelas
- Semua edge cases sudah punya jawaban yang konsisten dengan filosofi
- Tidak ada pertanyaan yang tersisa yang bisa bikin eksekutor salah interpretasi