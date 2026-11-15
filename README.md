# TANGGUH.AI

Aplikasi web pendataan kerusakan rumah pascabencana (Kab. Solok) dengan tiga fungsi AI:

| Pendekatan | Modul | Fungsi |
|---|---|---|
| **Rule-Based** | `tangguh/rules.py`, `batch.py` | Cek kelengkapan, format NIK/KK/GPS, duplikasi, konsistensi, dan selisih rekap vs detail |
| **Predictive** | `tangguh/ml/` | MobileNetV2 (transfer learning): foto → ringan / sedang / berat (rekomendasi awal) |
| **Generative** | `tangguh/llm.py` | Ringkasan situasi dari data **terverifikasi** (Gemini gratis, angka dicek terhadap database) |

Alur: Input laporan → Rule-Based → CNN → **verifikasi admin** → data terverifikasi → ringkasan LLM.
AI hanya menyarankan; keputusan akhir selalu manusia.

Stack gratis: Python, Flask, SQLite (satu file, tanpa instal), Gemini API free tier, TensorFlow/Keras.

## 1. Menjalankan (tanpa training dulu)

```bash
git clone https://github.com/itsnai/TANGGUH.AI.git && cd TANGGUH.AI
python -m venv .venv
.venv\Scripts\activate          # Windows   (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # opsional: isi GEMINI_API_KEY
python run.py                   # buka http://127.0.0.1:5000
```

Aplikasi tetap jalan tanpa model CNN (kategori diisi manual) dan tanpa API key (ringkasan pakai template).

## 2. Data

Taruh file asli di `data/raw/` (**folder ini di-gitignore: berisi NIK warga, jangan di-commit**).

```bash
# a) Periksa data dengan Rule-Based, hasil CSV di data/laporan_validasi/
python scripts/check_data.py --file data/raw/DATA_BENCANA_LAMPIRAN_SK_KONFIRMASI.xlsx \
   --sheet "LAMPIRAN UJI PUBLIK" --rekap data/rekap_solok_2025.csv \
   --compare data/raw/DATA_BENCANA_LAMPIRAN_SKAutoRecovered.xlsx

# b) Isi database demo dari data resmi (status terverifikasi) untuk dashboard & ringkasan
python scripts/seed_db.py --file data/raw/DATA_BENCANA_LAMPIRAN_SK_KONFIRMASI.xlsx --reset
```

File yang dipakai:
- Rule-Based: `DATA_BENCANA_LAMPIRAN_SK_KONFIRMASI.xlsx` (data utama), `data/rekap_solok_2025.csv` (rekap agregat, aman di-commit), `Hasil_Sandingan_Dukcapil_-_DPRKPP.xls` (pembanding NIK).
- CNN: `REKAP_DATA_VERIFIKASI_4-6_JANUARI_2026_2.pdf` (train/val/test) dan `REKAPITULASI_DATA_BENCANA_AROSUKA...pdf` (uji eksternal).

## 3. Dataset foto + training CNN

```bash
pip install -r requirements-ml.txt            # TensorFlow (berat; boleh di Google Colab)
python -m tangguh.ml.extract_dataset          # PDF -> data/dataset/<kelas>/*.jpg + manifest.csv
# PERIKSA MANUAL isi data/dataset/ringan|sedang|berat: buang foto bukan rumah (mis. foto bantuan) & label janggal
python -m tangguh.ml.train                    # -> models/damage_mobilenetv2.keras, classes.json, metrics.json
python run.py                                 # model otomatis terpakai bila file ada di models/
```

Di Google Colab: buka `notebooks/train_colab.ipynb` (Runtime → GPU). Setelah selesai, unduh `models/` dan taruh di folder `models/` lokal.

Catatan jujur tentang datanya: dari kedua PDF hanya ± 276 foto berlabel RR/RS/RB (± 151 ringan, 55 sedang, 70 berat); kelas sedang paling sedikit.
Akurasi akan terbatas, jadi laporkan **confusion matrix & per-kelas recall** (bukan hanya akurasi) di presentasi dan jelaskan CNN sebagai rekomendasi awal.
Split dilakukan **per rumah** agar tidak bocor. Foto yang labelnya HANYUT/TRD tidak dipakai.

## 4. Gemini gratis

Buat API key di https://aistudio.google.com/apikey → isi `GEMINI_API_KEY` di `.env`.
Nama model di `GEMINI_MODEL` bisa berubah; bila error 404, cek nama model terbaru di dokumentasi Gemini.
Jika gagal/kuota habis, aplikasi otomatis memakai ringkasan template.

## 5. Tes

```bash
python -m pytest -q
```

## 6. Pembagian kerja bertiga + alur GitHub

| Orang | Tanggung jawab | File utama |
|---|---|---|
| A (Data & Rule-Based) | aturan validasi, `check_data`, tes rules | `tangguh/rules.py`, `batch.py`, `tests/` |
| B (CNN) | ekstrak dataset, bersihkan label, training, evaluasi | `tangguh/ml/`, `notebooks/` |
| C (Web & LLM) | tampilan sesuai Figma, integrasi, prompt LLM | `app/`, `tangguh/llm.py` |

Alur aman: `main` selalu bisa jalan. Tiap orang bikin branch (`git checkout -b fitur-rules`), commit kecil, push, buka Pull Request, gabungkan setelah teman cek.
**Jangan pernah commit** `data/raw/`, `.env`, `uploads/`, atau file `.sqlite3` (sudah di `.gitignore`).
Model `.keras` jangan di repo; simpan di Google Drive.

## 7. Struktur

```
app/            Flask: routes + templates + css
tangguh/        rules.py, batch.py, data_loader.py, db.py, llm.py, ml/{extract_dataset,train,predict}.py
scripts/        check_data.py, seed_db.py
tests/          pytest
data/raw/       (lokal) file asli;  data/rekap_solok_2025.csv rekap agregat
models/         (lokal) hasil training
```
