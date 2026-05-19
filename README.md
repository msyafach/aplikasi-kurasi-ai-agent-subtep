# Aplikasi Kurasi Dataset Kendaraan

Aplikasi web berbasis Flask untuk kurasi dan anotasi dataset gambar kendaraan. Reviewer dapat menelusuri data satu per satu, memberikan label **Approved / Rejected / Skip**, memilih kategori penolakan, dan mengekspor hasilnya dalam format CSV maupun JSON siap pakai untuk finetuning model AI.

---

## Quickstart

**1. Clone & install dependencies**

```bash
git clone <repo-url>
cd aplikasi-kurasi
pip install -r requirements.txt
```

**2. Siapkan dataset**

Letakkan file CSV dataset di folder `data/`. Kolom minimal yang diperlukan:

| Kolom | Keterangan |
|---|---|
| `Foto Kendaraan` | URL gambar kendaraan |
| `Status` | Label awal (opsional) |

Atau gunakan preset lain (`url` + `reviewer_label`) dan pilih kolom secara manual di UI.

**3. Jalankan aplikasi**

```bash
python app.py
```

Buka browser ke `http://127.0.0.1:5000`.

**Variabel environment (opsional)**

| Variabel | Default | Keterangan |
|---|---|---|
| `CSV_PATH` | `data/kendaraan.screen_document_180526.csv` | Path dataset default |
| `PORT` | `5000` | Port server |
| `URL_COL` | `Foto Kendaraan` | Nama kolom URL gambar |
| `LABEL_COL` | `Status` | Nama kolom label |
| `IMAGE_TIMEOUT_SECONDS` | `8` | Timeout fetch gambar |

---

## Langkah-Langkah Anotasi

### 1. Pilih Dataset

- Klik tombol **Ganti Dataset** di header.
- Pilih file CSV dari daftar yang tersedia, atau upload file baru.
- Pilih **Format Preset** yang sesuai:
  - *Data Dashboard (kendaraan)* — kolom `Foto Kendaraan` + `Status`
  - *Data Train* — kolom `url` + `reviewer_label`
  - *Custom* — pilih kolom secara manual.

### 2. Pilih Agent Key

Sebelum mulai anotasi, pilih **Agent Key** yang sesuai dengan tipe data:

- `carphoto.*` — untuk data foto kendaraan
- `ocr_stnk.*` — untuk data foto STNK

Agent key menentukan kelompok kategori penolakan yang ditampilkan.

### 3. Tinjau Setiap Gambar

Untuk setiap baris data, aplikasi menampilkan:

- Gambar kendaraan / STNK dari URL
- Informasi tambahan: Nopol, hasil pembacaan AI, status sebelumnya

Reviewer memilih salah satu aksi:

| Tombol | Keterangan |
|---|---|
| **Approve** | Gambar lolos verifikasi → masuk bucket `approved` |
| **Reject** | Gambar ditolak → wajib pilih **kategori penolakan** dan isi deskripsi |
| **Skip** | Data meragukan, tinjau nanti → masuk bucket `skip` |
| **Back** | Kembali ke data sebelumnya untuk koreksi |

### 4. Isi Anotasi Penolakan (jika Reject)

Saat memilih Reject, lengkapi:

- **Kategori** — pilih dari daftar kode penolakan (mis. `C-01 Nopol tidak cocok`, `K-03 STNK buram`)
- **Deskripsi** — penjelasan singkat alasan penolakan

### 5. Simpan Progress

Progress tersimpan otomatis setiap aksi. Untuk menyimpan dan melanjutkan nanti:

- Klik **Save** — simpan progress, lanjutkan sesi berikutnya dari posisi terakhir.
- Klik **Stop** — simpan progress dan tutup sesi.

File progress disimpan di `.progress/`.

### 6. Export Hasil

Setelah selesai anotasi, klik **Export** dan pilih:

| Scope | Isi |
|---|---|
| `approved` | Data yang lolos |
| `rejected` | Data yang ditolak |
| `skipped` | Data yang diskip |
| `all` | Seluruh data |

Format export yang tersedia:

| Format | Keterangan |
|---|---|
| `json_labelling` | JSON terstruktur per group/agent, siap untuk finetuning |
| `raw` | CSV/JSON mentah sesuai mode dataset |
| `test_reason_json` | JSON lengkap dengan kode penolakan, untuk evaluasi model |
| `data_train_reviewed` | CSV dengan kolom label yang sudah diupdate hasil review |

Hasil export disimpan di `data/kurasi_outputs/<dataset>/<run_id>/`.

---

## Struktur Output

```
data/kurasi_outputs/
└── <nama_dataset>_<hash>/
    └── <run_id>/
        ├── <dataset>_approved.csv
        ├── <dataset>_rejected.csv
        ├── <dataset>_skip.csv
        ├── <dataset>_raw.csv
        └── export_csv_<scope>_<format>.json
```
