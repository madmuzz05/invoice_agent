# Invoice Verifier

Aplikasi ekstraksi & verifikasi keaslian dokumen perjalanan dinas — tiket, invoice/receipt hotel, struk tol & BBM, kwitansi sewa kendaraan — dalam format PDF atau gambar (PNG/JPG). Dibangun dengan [Google Agent Development Kit (ADK)](https://google.github.io/adk-docs/) dan FastAPI.

## Fitur

- Upload PDF/PNG/JPG via multipart/form-data (integrasi M2M dengan PISmart),
  proses asynchronous dengan polling status
- Klasifikasi `doc_type` (`invoice` / `receipt` / `boarding_pass` / `unknown`) dan
  `document_subtype` (`hotel`, `flight`, `train`, `ship`, `bus`, `toll`, `fuel`,
  `vehicle_rental`, `others`)
- Ekstraksi data terstruktur: vendor/merchant, tanggal, rute, kamar, nominal,
  item, jenis perjalanan (sekali jalan / PP), detail tol, BBM, sewa kendaraan
- **Semua halaman dibaca**; halaman scan/foto di-OCR (Tesseract) dan gambarnya
  ikut dikirim ke LLM vision
- **Banyak dokumen dalam satu file** (mis. 3 booking hotel, 4 struk tol, 4 struk
  BBM dalam satu foto) → tiap dokumen satu baris item, total dijumlahkan
  (`document_count`)
- **Dokumen campuran** (subtype berbeda, mis. hotel + pesawat) → ekstraksi
  dihentikan (`document_subtype="mixed"`). Pengecualian: paket kendaraan
  (sewa + tol/BBM) tetap diekstrak sebagai `vehicle_rental`
- Pra-proses foto tanpa model: deteksi kotak tiap struk, struk yang ditempel
  menyamping ditegakkan, rotasi halaman PDF dikoreksi
- Pengecekan deterministik setelah LLM: struk ganda (No. Seri / No. Trans sama)
  dibuang, nominal tol yang ternyata sisa saldo dikoreksi/ditandai, total
  dihitung ulang
- Analisis keaslian rule-based (tanpa LLM) dari metadata PDF dan **forensik
  struktur PDF** (isi berubah antar revisi, kotak putih menutupi teks, angka
  tertimpa, font angka asing) → **tingkat indikasi**, bukan vonis:
  `TIDAK ADA INDIKASI`, `INDIKASI LEMAH`, `INDIKASI KUAT`, atau
  `TIDAK DAPAT DIANALISIS` (scan/foto struk kertas), lengkap dengan
  `verdict_description` dan daftar temuan
- `requires_manual_review` + `review_reasons` untuk hasil yang perlu dicek manusia

## Cara Kerja

1. **Baca dokumen** (tanpa LLM): teks semua halaman via PyMuPDF; halaman
   scan/foto → deteksi kotak struk & teks menyamping (numpy) → OCR Tesseract.
2. **Pemetaan halaman** (PDF > 1 halaman, 1 panggilan LLM pendek): halaman
   dikelompokkan per dokumen; tiap dokumen diekstrak terpisah secara paralel.
3. **Ekstraksi** (1 panggilan LLM per dokumen): teks OCR + gambar halaman
   scan → JSON `TravelDocumentResult`.
4. **Post-processing** (tanpa LLM): normalisasi tipe, dedup struk, cek nominal
   tol, penjumlahan, aturan mixed, confidence & alasan review.
5. **Analisis keaslian** (tanpa LLM) dari metadata dan struktur file asli
   (revisi, kotak putih, angka tertimpa, font asing) → tingkat indikasi.

Detail kontrak response & aturan tiap subtype: [`invoice_verifier/docs/api/API.md`](invoice_verifier/docs/api/API.md).

## Struktur Project

```
invoice_agent/
├── invoice_verifier/
│   ├── baca_invoice/              # Core agent package (Google ADK)
│   │   ├── agent.py               # Entry point ADK CLI
│   │   ├── agents/
│   │   │   ├── document.py        # Pipeline: extractor (tanpa LLM) → formatter (LLM)
│   │   │   ├── segmenter.py       # LLM pemetaan halaman → dokumen (PDF multi-halaman)
│   │   │   ├── postprocess.py     # Post-processing deterministik output LLM
│   │   │   └── prompts.py         # Prompt formatter & segmenter
│   │   ├── models/                # Pydantic: TravelDocumentResult, DocumentAuthenticity
│   │   ├── tools/
│   │   │   ├── pdf.py             # Baca PDF/gambar, OCR, gambar halaman untuk LLM
│   │   │   ├── regions.py         # Deteksi kotak struk & teks menyamping (numpy)
│   │   │   ├── combined.py        # Baca dokumen + analisis keaslian sekaligus
│   │   │   ├── authenticity.py    # Analisis keaslian rule-based (tingkat indikasi)
│   │   │   ├── pdf_forensics.py   # Forensik struktur PDF (revisi, whiteout, font)
│   │   │   ├── boarding_pass.py   # Pre-check boarding pass tanpa LLM
│   │   │   └── constants.py       # Provider & software pengedit
│   │   └── utils/                 # Aturan murni (mixed, multi dokumen, merge, cek tol, ...)
│   ├── web/                       # FastAPI web server
│   │   ├── main.py                # App entrypoint & lifespan
│   │   ├── config.py              # Konfigurasi dari env vars
│   │   ├── api/v1_upload.py       # PINTER API endpoints
│   │   ├── db/sqlite.py           # Penyimpanan job (SQLite)
│   │   ├── services/
│   │   │   ├── agent_runner.py    # Job, segmentasi, ekstraksi paralel, merge
│   │   │   ├── result_validator.py# Normalisasi & validasi hasil akhir
│   │   │   └── job_errors.py      # error_code & HTTP status job gagal
│   │   └── static/                # Frontend (HTML + CSS + JS)
│   └── docs/api/API.md            # Dokumentasi API lengkap
├── tests/                         # Unit, integration & security tests
├── run_web.py                     # Jalankan web server
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## Prasyarat

- Python 3.11+
- Tesseract OCR (untuk halaman scan/foto): `brew install tesseract` (macOS) /
  `apt-get install tesseract-ocr tesseract-ocr-eng` (Debian/Ubuntu). Sudah
  terpasang di image Docker.
- Google AI Studio API Key → [aistudio.google.com](https://aistudio.google.com/app/apikey), atau kredensial untuk endpoint OpenAI-compatible
  (model harus mendukung input gambar/vision untuk dokumen scan/foto)

## Instalasi

**1. Clone repository**

```bash
git clone https://github.com/madmuzz05/invoice_agent.git
cd invoice_agent
```

**2. Buat virtual environment**

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

**4. Konfigurasi environment**

```bash
cp .env.example .env
```

Edit `.env` — minimal isi kredensial LLM dan `PINTER_API_KEY`. Daftar lengkap:

| Env | Default | Keterangan |
|---|---|---|
| `OPENAI_BASE_URL` | kosong | Endpoint OpenAI-compatible; jika diisi, mode ini yang dipakai |
| `OPENAI_API_KEY` | – | Wajib jika `OPENAI_BASE_URL` diisi |
| `OPENAI_MODEL` | – | Wajib jika `OPENAI_BASE_URL` diisi; harus mendukung input gambar |
| `GOOGLE_API_KEY` | – | Mode Gemini (dipakai jika `OPENAI_BASE_URL` kosong) |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model untuk mode Gemini |
| `LLM_REASONING_EFFORT` | `low` | Tingkat reasoning (`low`/`medium`/`high`). `low` 2–4× lebih cepat dengan akurasi sama pada dokumen uji. Kosongkan jika model tidak mendukung parameter ini. |
| `LLM_SEND_PAGE_IMAGES` | `true` | Kirim gambar halaman scan/foto ke LLM. `false` = hanya teks OCR (lebih cepat, akurasi scan turun). |
| `LLM_MAX_PARALLEL_CALLS` | `4` | Batas panggilan LLM bersamaan untuk semua job (sesuaikan dengan rate limit endpoint) |
| `PINTER_API_KEY` | kosong | Header `X-API-Key` untuk `/api/pinter/*`; kosong = auth mati (development saja) |
| `PINTER_TRX_TTL_DAYS` | `7` | Umur `trx_id`; setelah itu `/extract` → `TRX_EXPIRED` |
| `APP_ENV` | `development` | `production` menyembunyikan detail error di response |
| `ENABLE_DOCS` | `1` | `0` mematikan `/docs` (Swagger) |
| `MAX_UPLOAD_MB` | `20` | Batas ukuran upload |
| `UPLOAD_DIR` | `tmp_uploads` | Folder file upload sementara (relatif terhadap `invoice_verifier/`) |
| `SQLITE_DB_PATH` | `data/invoice_verifier.db` | Lokasi database job (relatif terhadap `invoice_verifier/`) |
| `MAX_CONCURRENT_JOBS` | `10` | Job ekstraksi yang berjalan bersamaan |
| `JOB_TTL_SECONDS` | `3600` | Job selesai dihapus dari memori setelah ini (hasil tetap di SQLite) |

## Menjalankan Web App

```bash
python run_web.py
```

Atau dengan auto-reload saat development:

```bash
python run_web.py --reload
```

## Menjalankan via Docker

```bash
docker build --platform linux/amd64 -t invoice-verifier .
docker run -p 8080:8080 --env-file .env invoice-verifier
```

Image hanya berisi kode aplikasi: `.env`, database lokal (`invoice_verifier/data`),
file upload, dan tes dikecualikan lewat `.dockerignore`. Data runtime (SQLite &
upload sementara) ada di `/app/runtime` di dalam container.

> `.env` untuk development biasanya berisi path relatif (`UPLOAD_DIR=tmp_uploads`)
> yang tidak bisa ditulis di container. Saat memakai `--env-file .env`, hapus
> `UPLOAD_DIR`/`SQLITE_DB_PATH` dari file itu atau timpa:
> `-e UPLOAD_DIR=/app/runtime/uploads -e SQLITE_DB_PATH=/app/runtime/invoice_verifier.db`.

### Build & deploy manual ke Cloud Run

```bash
IMAGE=REGION-docker.pkg.dev/PROJECT_ID/REPO/invoice-verifier:$(git rev-parse --short HEAD)

gcloud auth configure-docker REGION-docker.pkg.dev
docker build --platform linux/amd64 -t "$IMAGE" .
docker push "$IMAGE"

gcloud run deploy invoice-verifier \
  --image "$IMAGE" --region REGION \
  --memory 2Gi --cpu 2 \
  --no-cpu-throttling \
  --min-instances 1 --max-instances 1 \
  --set-env-vars OPENAI_BASE_URL=...,OPENAI_MODEL=... \
  --set-secrets OPENAI_API_KEY=openai-api-key:latest,PINTER_API_KEY=pinter-api-key:latest
```

Kenapa flag-flag ini:

- `--no-cpu-throttling`: ekstraksi berjalan di background setelah `/upload`
  membalas; tanpa ini CPU dihentikan Cloud Run setelah response terkirim.
- `--min-instances 1 --max-instances 1`: job & hasil disimpan di SQLite lokal
  per instance. Lebih dari 1 instance → `/extract` bisa mendarat di instance lain
  (404); scale ke nol menghentikan job yang sedang berjalan.
- Disk container Cloud Run sementara: database & hasil ekstraksi **hilang saat
  deploy/restart**. Untuk data yang harus bertahan / multi-instance, pindahkan
  penyimpanan job ke Cloud SQL atau Firestore.
- `--memory 2Gi`: OCR, render gambar, dan numpy cukup berat untuk foto/scan.
- Port diambil dari env `PORT` (diisi Cloud Run); IP klien asli dibaca dari
  `X-Forwarded-For` (`--proxy-headers`) sehingga rate limit upload berlaku per
  klien, bukan per IP proxy.

## Menjalankan via ADK CLI

```bash
adk run baca_invoice
```

Kemudian kirim path file di terminal:

```
file_path: /path/to/dokumen.pdf
```

## Menjalankan Tests

```bash
pip install -r requirements-dev.txt
ruff check .
pytest tests/ -v
```

Tes tidak memanggil LLM dan tidak membaca `PINTER_API_KEY` dari `.env`.

## CI/CD

Repo ini di-mirror ke GitHub dan GitLab kantor; keduanya menjalankan lint dan tes.

| | GitHub Actions (`.github/workflows/ci.yml`) | GitLab CI (`.gitlab-ci.yml`) |
|---|---|---|
| Setiap PR/MR & push | ruff, pytest, docker build (tanpa push) | ruff, pytest (+ coverage), SBOM Trivy |
| `development` | sama | + SonarQube, image `:development` ke registry GitLab, deploy ke server dev (docker compose), health check |
| `main` | sama | + image `:production` (deploy production manual) |

Variabel CI/CD yang dibutuhkan GitLab tertulis di bagian atas `.gitlab-ci.yml`.
Di server dev, data SQLite & upload disimpan di volume Docker
`pismart-invoice-agent-runtime`, jadi tetap ada saat redeploy.

## API Endpoint

Base URL: `http://localhost:8080`

Autentikasi: `X-API-Key` header (dinonaktifkan otomatis jika `PINTER_API_KEY` tidak di-set).

### POST `/api/pinter/upload`

Upload dokumen (PDF, PNG, atau JPG) untuk diekstraksi. Proses berjalan di background.

**Request** — `multipart/form-data`

| Field | Tipe | Keterangan |
|---|---|---|
| `file` | file | PDF / PNG / JPG, maks 20 MB |

**Response `200`**

```json
{
  "trx_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "progress",
  "message": "Dokumen diterima dan sedang diproses."
}
```

---

### GET `/api/pinter/extract?trx_id={trx_id}`

Poll hasil ekstraksi. Ulangi setiap 3–5 detik sampai `status` bukan `progress`.

**Query Param**

| Param | Tipe | Keterangan |
|---|---|---|
| `trx_id` | string (UUID) | ID dari response upload |

**Response `200` — masih diproses**

```json
{
  "trx_id": "550e8400-...",
  "status": "progress",
  "message": "Dokumen sedang diproses.",
  "data": null
}
```

**Response `200` — selesai** (ringkas; field lengkap & aturannya di
[API.md](invoice_verifier/docs/api/API.md))

```json
{
  "trx_id": "550e8400-...",
  "status": "success",
  "message": "Ekstraksi berhasil.",
  "data": {
    "doc_type": "receipt",
    "document_subtype": "fuel",
    "detected_subtypes": ["fuel"],
    "document_count": 2,
    "merchant_name": "SPBU Pertamina",
    "items_purchased": [
      {"description": "2026-04-20 SPBU 31.411.02 PERTALITE No. Trans 100001", "quantity": 25.0, "price": 250000.0},
      {"description": "2026-04-23 SPBU 3141101 PERTALITE No. Trans 100002", "quantity": 29.37, "price": 293700.0}
    ],
    "fuel_type": "PERTALITE",
    "fuel_volume_liters": 54.37,
    "total_payment": 543700.0,
    "currency": "IDR",
    "authenticity": {
      "verdict": "TIDAK DAPAT DIANALISIS",
      "verdict_description": "Keaslian tidak bisa dianalisis dari file ini, misalnya hasil scan/foto struk kertas ...",
      "is_suspicious": false,
      "confidence_score": 0.0,
      "warning_flags": []
    },
    "extraction_confidence": 0.85,
    "requires_manual_review": false,
    "review_reasons": [],
    "summary": "2 struk BBM Pertalite dengan total Rp 543.700."
  }
}
```

Tipe setiap field stabil (tidak pernah `null`, string tidak berubah jadi
array, dst.) dan dijaga oleh `tests/unit/test_response_contract.py`.

**Response `5xx` — gagal** (HTTP status mengikuti `error_code`)

```json
{
  "trx_id": "550e8400-...",
  "status": "fail",
  "message": "Terjadi kesalahan saat memproses dokumen.",
  "data": null,
  "error_code": "LLM_UNAVAILABLE"
}
```

**Status Enum**

| Status | Arti |
|---|---|
| `progress` | Masih diproses, poll lagi |
| `success` | Selesai, `data` tersedia |
| `fail` | Gagal diproses |

**Error Codes**

| `error_code` | HTTP | Kondisi |
|---|---|---|
| `MISSING_FILE` | 400 | Field `file` tidak ada |
| `MISSING_TRX_ID` | 400 | Query `trx_id` kosong |
| `RATE_LIMITED` | 429 | Terlalu banyak upload per menit dari klien yang sama |
| `INVALID_FILE_TYPE` | 400 | Bukan PDF/PNG/JPG atau magic bytes tidak cocok |
| `FILE_TOO_LARGE` | 413 | Melebihi 20 MB |
| `TRX_NOT_FOUND` | 404 | `trx_id` tidak dikenal |
| `TRX_EXPIRED` | 410 | `trx_id` sudah > 7 hari |
| `INTERNAL_ERROR` | 500 | Kesalahan internal server |
| `UNAUTHORIZED` | 401 | `X-API-Key` salah atau tidak ada |
| `EXTRACTION_FAILED` | 500 | Job ekstraksi gagal (error umum) |
| `INVALID_LLM_OUTPUT` | 502 | LLM tidak mengembalikan JSON yang valid |
| `LLM_UNAVAILABLE` | 502 | Endpoint LLM tidak bisa diakses / error (mis. 404, 5xx) |
| `LLM_AUTH_FAILED` | 502 | Kredensial LLM ditolak |
| `LLM_RATE_LIMITED` | 503 | Kuota / rate limit LLM habis |
| `JOB_INTERRUPTED` | 503 | Proses terputus karena server restart (upload ulang) |
| `LLM_TIMEOUT` | 504 | LLM tidak merespons tepat waktu |

---

### GET `/health`

Health check server.

**Response `200`**

```json
{
  "status": "ok",
  "jobs_active": 2
}
```

## Tech Stack

| Komponen | Teknologi |
|---|---|
| Agent framework | Google ADK; LLM Gemini atau endpoint OpenAI-compatible via LiteLLM (mis. GLM-5.3 Flash) |
| Web server | FastAPI + Uvicorn |
| PDF / gambar | PyMuPDF (fitz), numpy (deteksi kotak & orientasi) |
| OCR | Tesseract (via PyMuPDF) |
| Persistent storage | SQLite (aiosqlite) |
| Frontend | Vanilla JS, CSS glassmorphism |
| Testing | pytest + pytest-asyncio + httpx |
