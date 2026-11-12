# Layer 3 BIC Penalty Range Fix

## Perbaikan

File yang diubah:
- `src/layer3/meta_learning.py`

Perubahan:
- Rentang kandidat penalty BIC diperluas dari `0.1x - 10x` menjadi `0.001x - 10x`.
- Implementasi sebelumnya menggunakan:
  - `multipliers = np.logspace(-1, 1, 10)`
- Sekarang menggunakan:
  - `multipliers = np.logspace(-3, 1, 13)`

## Alasan

Layer 3 memilih penalty CPD berdasarkan BIC-scaled candidates. Namun untuk data score saat ini, nilai `bic_base` sudah cukup besar, sehingga rentang kandidat terendah (`0.1x bic_base`) masih terlalu tinggi.

Akibatnya:
- CPD memilih penalty terlalu besar
- Terjadi under-segmentation
- Regime terakhir menjadi terlalu besar
- Threshold UP/DOWN menjadi terlalu lebar
- Sistem sering stuck di `NEUTRAL`

Dengan rentang baru, Layer 3 tetap mempertahankan kandidat konservatif tinggi, tetapi juga sekarang dapat memilih penalty jauh lebih rendah ketika data membutuhkan segmentasi yang lebih halus.

## Dampak yang Diharapkan

- Lebih banyak kandidat penalty rendah tersedia untuk pemilihan
- Segmentasi CPD dapat menjadi lebih responsif terhadap perubahan regime
- Threshold score diharapkan lebih realistis dan tidak terlalu lebar

## Catatan

Perbaikan ini hanya mengubah pencarian kandidat penalty di Layer 3. Logika agregasi skor Layer 2 tidak diubah.
