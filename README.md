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
- Jika dataset memiliki kolom `agent_key` (seperti format *Data Train*), petakan kolom tersebut di form load dataset. Aplikasi akan membaca agent key otomatis dari setiap baris data.

### 2. Pilih Agent Key (jika belum ada di data)

Langkah ini **hanya diperlukan** jika dataset tidak memiliki kolom `agent_key` (misalnya format *Data Dashboard*).

- Di bagian **Agent key** pada toolbar, pilih agent key yang sesuai:
  - `carphoto.*` — untuk data foto kendaraan
  - `ocr_stnk.*` — untuk data foto STNK
- Klik tombol **Tandai Agent** untuk menerapkannya.

Agent key menentukan kelompok kategori penolakan yang ditampilkan dan digunakan saat export `json_labelling`.

**Mengganti agent key di tengah anotasi:**

Jika dataset berisi campuran tipe data (misalnya sebagian foto kendaraan, sebagian STNK), agent key bisa diganti kapan saja tanpa harus reset sesi:

1. Pilih agent key baru dari dropdown **Agent key** di toolbar.
2. Klik **Tandai Agent**.
3. Anotasi selanjutnya akan menggunakan agent key yang baru.

> Baris yang sudah di-approve/reject sebelumnya tidak terpengaruh — agent key mereka sudah tersimpan di anotasi masing-masing.

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

---

#### `json_labelling` — JSON terstruktur per group/agent

Format utama untuk **finetuning model AI**. Record dikelompokkan berdasarkan `group_key` dan `agent_key` sehingga langsung bisa dikonsumsi pipeline training.

Setiap record hanya memuat 4 field inti:

```json
{
  "foto_kendaraan": {
    "carphoto.vehicle_verification": [
      {
        "url": "https://...",
        "expected": "APPROVED",
        "category": "",
        "description": "Foto LOLOS verifikasi"
      },
      {
        "url": "https://...",
        "expected": "REJECTED",
        "category": "Nopol tidak cocok",
        "description": "Nomor polisi pada foto berbeda dengan data"
      }
    ]
  }
}
```

> Jika data tidak memiliki `agent_key`, output ditulis sebagai flat list JSON (tanpa pengelompokan).

---

#### `raw_json` — JSON flat tanpa pengelompokan

Sama dengan `json_labelling` dari sisi field record (`url`, `expected`, `category`, `description`), tapi ditulis sebagai **array datar** tanpa struktur group/agent.

Cocok untuk inspeksi cepat atau pipeline yang tidak membutuhkan pembagian per agent.

```json
[
  {
    "url": "https://...",
    "expected": "REJECTED",
    "category": "STNK buram",
    "description": "Foto STNK tidak terbaca"
  }
]
```

---

#### `raw` — Data mentah asli

Mengekspor baris dataset persis seperti aslinya, **tanpa transformasi**:

- Mode CSV → file `.csv` dengan semua kolom original dataset.
- Mode Finetune JSON → file `.json` berisi record original dari JSON sumber, dengan field anotasi (`expected`, `category`, `description`) yang sudah di-overlay dengan hasil review.

Gunakan format ini jika perlu memeriksa seluruh kolom asli atau untuk keperluan audit.

---

#### `test_reason_json` — JSON evaluasi model lengkap

Format untuk **evaluasi dan testing model verifikasi**. Setiap record mengandung field lengkap yang dibutuhkan untuk membandingkan prediksi model dengan hasil review manusia, termasuk kode penolakan terstandar.

```json
[
  {
    "web_registerasi_detail_id": "12345",
    "web_register_id": "6789",
    "police_number": "B1234XYZ",
    "nomor_rangka": "MHFXX...",
    "stnk_photo": "",
    "vehicle_photo": "https://...",
    "fuel_oil_type": "Bensin",
    "wheel_count": "4",
    "cubicle_centimeter": "1500",
    "plate_color": "Hitam",
    "mapped_rejection_code": "C-01",
    "mapped_rejection_category": "Nopol tidak cocok",
    "mapped_rejection_message": "",
    "expected": "REJECTED",
    "description": "Nomor polisi pada foto tidak cocok",
    "is_valid_verifikasi_ulang": null,
    "status": "REJECTED"
  }
]
```

Kode penolakan (`mapped_rejection_code`) dipetakan otomatis dari kategori yang dipilih reviewer, misalnya `Nopol tidak cocok` → `C-01`, `STNK buram` → `K-03`.

---

#### `data_train_reviewed` — CSV dengan label diperbarui

> **Catatan:** Format ini hanya digunakan untuk dataset dengan preset **Data Train** (`url` + `reviewer_label` + `agent_key`). Tidak relevan untuk format Data Dashboard atau Custom.

Mengekspor dataset dalam format **CSV** dengan semua kolom asli dipertahankan, namun dua kolom diperbarui berdasarkan hasil review:

- Kolom label (`Status` / `reviewer_label`) → diisi nilai `APPROVED`, `REJECTED`, atau `SKIP` dari hasil review.
- Kolom `reviewer_notes` → diisi catatan tambahan reviewer (jika ada).

Cocok sebagai dataset training baru yang siap digabung dengan dataset sebelumnya tanpa perlu transformasi tambahan.

---

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
