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

**2. Install ExifTool (untuk fitur Forensik)**

Fitur analisis forensik memerlukan [ExifTool](https://exiftool.org/) oleh Phil Harvey.

- **Windows:** Download installer dari https://exiftool.org/ → pilih *Windows Executable*. Install ke lokasi default (`C:\Users\<user>\AppData\Local\Programs\ExifTool\`). Aplikasi akan mendeteksi path ini otomatis meski ExifTool tidak ada di `PATH` sistem.
- **macOS:** `brew install exiftool`
- **Linux:** `sudo apt install libimage-exiftool-perl`

> Tanpa ExifTool, aplikasi tetap berjalan normal. Fitur Forensik akan menampilkan pesan error jika dibuka.

**3. Siapkan dataset**

Letakkan file CSV dataset di folder `data/`. Kolom minimal yang diperlukan:

| Kolom | Keterangan |
|---|---|
| `Foto Kendaraan` | URL gambar kendaraan |
| `Status` | Label awal (opsional) |

Atau gunakan preset lain (`url` + `reviewer_label`) dan pilih kolom secara manual di UI.

**4. Jalankan aplikasi**

```bash
python app.py
```

Buka browser ke `http://127.0.0.1:5000`.

---

## Langkah-Langkah Anotasi

### 1. Pilih Dataset

- Pilih file CSV dari dropdown **Dataset**, atau upload file baru via tombol **Upload CSV**.
- Pilih **Format Preset** yang sesuai:
  - *Data Dashboard (kendaraan)* — kolom `Foto Kendaraan` + `Status`
  - *Data Train* — kolom `url` + `reviewer_label`
  - *Custom* — pilih kolom secara manual.
- Jika dataset memiliki kolom `agent_key` (seperti format *Data Train*), petakan kolom tersebut di form load dataset. Aplikasi akan membaca agent key otomatis dari setiap baris data.
- Klik **Muat Path** untuk memulai sesi.

Pilihan dataset, format, dan kolom tersimpan di database — sesi dilanjutkan otomatis setelah browser refresh atau server restart.

### 2. Pilih Agent Key (jika belum ada di data)

Langkah ini **hanya diperlukan** jika dataset tidak memiliki kolom `agent_key` (misalnya format *Data Dashboard*).

- Di bagian **Agent key** pada toolbar, pilih agent key yang sesuai dari dropdown:
  - `carphoto.*` — untuk data foto kendaraan
  - `ocr_stnk.*` — untuk data foto STNK
- Klik tombol **Tandai Agent** untuk menerapkannya ke sesi.

Untuk format CSV (tanpa kolom agent_key), agent key dapat diganti kapan saja dengan memilih dari dropdown — perubahan langsung diterapkan tanpa perlu klik tombol.

Agent key menentukan kelompok kategori penolakan yang ditampilkan dan digunakan saat export `json_labelling`.

### 3. Tinjau Setiap Gambar

Untuk setiap baris data, aplikasi menampilkan:

- Gambar kendaraan / STNK dari URL
- Informasi tambahan: Nopol, hasil pembacaan AI, status sebelumnya

Reviewer memilih salah satu aksi:

| Tombol | Shortcut | Keterangan |
|---|---|---|
| **Approved** | `1` | Gambar lolos verifikasi → masuk bucket `approved` |
| **Rejected** | `2` | Gambar ditolak → wajib pilih kategori dan isi deskripsi |
| **Skip** | `3` | Data meragukan, tinjau nanti → masuk bucket `skip` |
| **Back** | `←` | Kembali ke data sebelumnya untuk koreksi |
| **Next** | `→` | Maju ke data berikutnya tanpa mengubah label (hanya aktif jika baris sudah pernah diulas) |

> Shortcut keyboard tidak aktif saat fokus berada di dalam input/textarea. Tekan **Esc** untuk keluar dari field teks.

### 4. Isi Anotasi Penolakan (jika Reject)

Saat memilih Reject, lengkapi:

- **Kategori** — pilih dari daftar kode penolakan (mis. `C-01 Nopol tidak cocok`, `K-03 STNK buram`). Bisa lebih dari satu kategori.
- **Deskripsi** — penjelasan singkat alasan penolakan.

### 5. Forensik Gambar

Klik tombol **Forensik** di baris aksi untuk membuka panel analisis forensik. Panel ini menampilkan:

- **ELA (Error Level Analysis)** — mendeteksi area gambar yang kemungkinan telah diedit atau dimanipulasi. Area yang terang/merah mengindikasikan inkonsistensi kompresi, yang bisa mengindikasikan adanya editan digital atau gambar hasil AI.
- **Metadata** — data teknis gambar dari ExifTool, dikelompokkan per namespace (EXIF, JFIF, ICC_Profile, XMP, Composite, dll.). Informasi ini membantu mendeteksi:
  - Software yang digunakan untuk mengedit gambar
  - Tanggal/waktu pengambilan foto
  - Perangkat kamera
  - Tanda-tanda manipulasi di history metadata

**Kontrol ELA:**
- **Kualitas re-kompresi** — slider 50–99%. Nilai lebih rendah membuat perbedaan lebih terlihat.
- **Amplifikasi** — slider 1–50×. Nilai lebih tinggi memperjelas area yang mencurigakan.
- Klik **Refresh ELA** untuk menerapkan ulang dengan nilai slider terbaru.

**Indikator risiko:**

| Risiko | Kondisi |
|---|---|
| `RENDAH` | Tidak ada indikator manipulasi terdeteksi |
| `SEDANG` | Ditemukan tanda-tanda editing (software seperti Photoshop, metadata tidak konsisten) |
| `TINGGI` | Ditemukan indikator kuat manipulasi (riwayat editing, watermark removal, dll.) |

> Forensik bersifat **indikatif**, bukan konklusif. Keputusan akhir tetap di tangan reviewer.

### 6. Simpan Progress

Progress tersimpan otomatis ke database SQLite (`kurasi.db`) setiap aksi. Untuk menyimpan dan melanjutkan nanti:

- Klik **Simpan** — simpan progress, lanjutkan sesi berikutnya dari posisi terakhir.
- Klik **Stop & Simpan** — simpan progress dan hentikan sesi.

Saat server dinyalakan kembali, banner **"Lanjutkan Session"** akan muncul otomatis jika ada sesi sebelumnya yang belum selesai.

### 7. Export Hasil

Gunakan panel **Export data** untuk mengekspor hasil anotasi:

- Pilih **scope**: `Labeled`, `Approved`, `Rejected`, `Skip`, `Raw`, `Reviewed`, atau `All`.
- Pilih **format** yang diinginkan.
- Klik **Preview** untuk melihat sample output sebelum export.
- Klik **Export** untuk menyimpan file.

Format export yang tersedia:

---

#### `json_labelling` — JSON terstruktur per group/agent

Format utama untuk **finetuning model AI**. Record dikelompokkan berdasarkan `group_key` dan `agent_key` sehingga langsung bisa dikonsumsi pipeline training.

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

---

#### `test_reason_json` — JSON evaluasi model lengkap

Format untuk **evaluasi dan testing model verifikasi**. Setiap record mengandung field lengkap untuk membandingkan prediksi model dengan hasil review manusia, termasuk kode penolakan terstandar.

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

Mengekspor dataset dalam format **CSV** dengan semua kolom asli dipertahankan, namun dua kolom diperbarui berdasarkan hasil review:

- Kolom label (`Status` / `reviewer_label`) → diisi nilai `APPROVED`, `REJECTED`, atau `SKIP`.
- Kolom `reviewer_notes` → diisi catatan tambahan reviewer (jika ada).

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

---

## Migrasi dari Versi Lama

Jika sebelumnya menggunakan versi yang menyimpan progress di folder `.progress/` (file `.json`), gunakan tombol **Migrasi Data Lama** di halaman utama untuk memindahkan semua data ke database SQLite secara otomatis. Proses ini tidak menghapus file lama.
