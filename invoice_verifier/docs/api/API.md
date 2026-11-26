# Invoice Verifier — API Reference

Base URL: `http://localhost:8080`

Autentikasi: header `X-API-Key` (env `PINTER_API_KEY`; nonaktif kalau tidak diset).
Format response error seragam:
```json
{ "status": "fail", "message": "...", "error_code": "MACHINE_READABLE_CODE" }
```

---

## Endpoints

### 1. `POST /api/pinter/upload`

Upload PDF untuk diekstraksi. Proses berjalan async di background.

**Request**:
- `Content-Type: multipart/form-data`
- Field `file`: PDF, max 20 MB (env `MAX_UPLOAD_MB`)
- Header `X-API-Key` (opsional, tergantung config)

**Response 200 — Doc type invoice/receipt**:
```json
{
  "trx_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "progress",
  "message": "Dokumen diterima dan sedang diproses."
}
```

**Response 200 — Doc type unknown**:
```json
{
  "trx_id": "...",
  "status": "progress",
  "message": "Dokumen diterima. Tidak dikenali sebagai invoice/receipt — akan dikembalikan dengan doc_type='unknown'."
}
```

**Response Errors**:

| HTTP | error_code | Penyebab |
|------|------------|----------|
| 400 | `MISSING_FILE` | Field `file` kosong |
| 400 | `INVALID_FILE_TYPE` | Bukan PDF (ekstensi/magic bytes) |
| 401 | `UNAUTHORIZED` | X-API-Key salah/tidak ada |
| 413 | `FILE_TOO_LARGE` | Melebihi 20 MB |
| 429 | `RATE_LIMITED` | > 10 upload/menit/IP |
| 500 | `INTERNAL_ERROR` | Error server |

---

### 2. `GET /api/pinter/extract?trx_id={trx_id}`

Poll hasil ekstraksi.

**Response 200 — Masih progress**:
```json
{
  "trx_id": "...",
  "status": "progress",
  "message": "Dokumen sedang diproses.",
  "data": null
}
```

**Response 200 — Success (doc_type=invoice)**:
```json
{
  "trx_id": "...",
  "status": "success",
  "message": "Ekstraksi berhasil.",
  "data": {
    "doc_type": "invoice",
    "invoice_number": "INV-2026/001",
    "vendor_name": "PT Hotel Indah",
    "buyer_name": "...",
    "line_items": [
      { "description": "...", "quantity": 2, "unit_price": 850000, "subtotal": 1700000 }
    ],
    "subtotal": 1700000.0,
    "tax": 187000.0,
    "total_payment": 1887000.0,
    "currency": "IDR",
    "authenticity": { /* DocumentAuthenticity */ },
    "extraction_confidence": 0.85,
    "requires_manual_review": false,
    "summary": "Invoice PT Hotel Indah, INV-2026/001, Total: Rp 1.887.000."
  }
}
```

**Response 200 — Success (doc_type=receipt)**:
```json
{
  "trx_id": "...",
  "status": "success",
  "message": "Ekstraksi berhasil.",
  "data": {
    "doc_type": "receipt",
    "receipt_number": "TRX-987654321",
    "merchant_name": "Garuda Indonesia",
    "payer_name": "Budi Santoso",
    "items_purchased": [
      { "description": "Tiket CGK→DPS", "quantity": 1, "price": 1250000 }
    ],
    "total_payment": 1275000.0,
    "currency": "IDR",
    "payment_method": "Kartu Kredit",
    "payment_status": "paid",
    "authenticity": { /* DocumentAuthenticity */ },
    "extraction_confidence": 0.85,
    "summary": "Garuda Indonesia CGK→DPS. Total: Rp 1.275.000."
  }
}
```

**Response 200 — Success (doc_type=unknown)**:

Untuk dokumen tidak terklasifikasi, sistem **tidak memanggil AI** — hanya analisis `authenticity` PDF.

```json
{
  "trx_id": "...",
  "status": "success",
  "message": "Ekstraksi berhasil.",
  "data": {
    "doc_type": "unknown",
    "authenticity": { /* DocumentAuthenticity */ },
    "extraction_confidence": 0.0,
    "requires_manual_review": true,
    "review_reasons": ["Dokumen tidak dikenali sebagai invoice atau receipt."],
    "summary": "Dokumen tidak terklasifikasi. Tidak ada data yang diekstraksi."
  }
}
```

**Response 5xx — Status fail** (HTTP status mengikuti `error_code`, lihat tabel di bawah):
```json
{
  "trx_id": "...",
  "status": "fail",
  "message": "Terjadi kesalahan saat memproses dokumen.",
  "data": null,
  "error_code": "LLM_UNAVAILABLE"
}
```

**Response Errors**:

| HTTP | error_code | Penyebab |
|------|------------|----------|
| 400 | `MISSING_TRX_ID` | Query `trx_id` tidak ada |
| 400 | `VALIDATION_ERROR` | Format param tidak valid |
| 401 | `UNAUTHORIZED` | X-API-Key salah/tidak ada |
| 404 | `TRX_NOT_FOUND` | trx_id tidak ada di DB |
| 410 | `TRX_EXPIRED` | trx_id > `PINTER_TRX_TTL_DAYS` (default 7 hari) |
| 500 | `INTERNAL_ERROR` | Error server |
| 500 | `EXTRACTION_FAILED` | Job ekstraksi gagal (error umum) |
| 502 | `INVALID_LLM_OUTPUT` | LLM tidak mengembalikan JSON yang valid |
| 502 | `LLM_UNAVAILABLE` | Endpoint LLM tidak bisa diakses / error (mis. 404, 5xx) |
| 502 | `LLM_AUTH_FAILED` | Kredensial LLM ditolak |
| 503 | `LLM_RATE_LIMITED` | Kuota / rate limit LLM habis |
| 503 | `JOB_INTERRUPTED` | Proses terputus karena server restart (upload ulang) |
| 504 | `LLM_TIMEOUT` | LLM tidak merespons tepat waktu |

---

### 3. `GET /health`

Liveness check.

**Response 200**:
```json
{
  "status": "ok",
  "jobs_active": 0
}
```

---

## DocumentAuthenticity Schema

Sub-schema yang muncul di response semua doc_type:

Hasil analisis keaslian berupa **tingkat indikasi**, bukan vonis asli/palsu:
sistem hanya membaca jejak di metadata dan struktur file, tidak bisa memastikan
kebenaran isi dokumen.

```json
{
  "verdict": "TIDAK ADA INDIKASI",
  "verdict_description": "Tidak ditemukan indikasi perubahan pada metadata maupun struktur dokumen. Ini bukan jaminan keaslian: dokumen yang dibuat ulang dari awal tidak meninggalkan jejak struktur.",
  "is_suspicious": false,
  "confidence_score": 0.95,
  "detected_provider": "tiket.com",
  "pdf_creator": "Mozilla/5.0...HeadlessChrome...",
  "pdf_producer": "Skia/PDF m140",
  "creation_date": "2026-04-23T14:17:38",
  "modification_date": "2026-04-23T14:17:38",
  "was_modified": false,
  "warning_flags": [],
  "fake_evidence": [],
  "analysis_notes": "Tidak ditemukan indikasi perubahan dokumen."
}
```

| `verdict` | Arti (`verdict_description`) |
|---|---|
| `TIDAK ADA INDIKASI` | Tidak ditemukan indikasi perubahan pada metadata maupun struktur. Bukan jaminan keaslian — dokumen yang dibuat ulang dari awal tidak meninggalkan jejak. |
| `INDIKASI LEMAH` | Ada tanda yang bisa berarti dokumen diubah, tapi juga bisa muncul pada dokumen wajar (metadata tidak sesuai provider, tambalan putih di belakang angka, font angka berbeda). Perlu dicek manual. |
| `INDIKASI KUAT` | Jejak langsung perubahan isi: software pengedit di metadata, isi berbeda antar revisi file, atau angka lama tertimpa angka baru. Cocokkan dengan dokumen sumber. |
| `TIDAK DAPAT DIANALISIS` | Tidak bisa dianalisis dari file ini: scan/foto struk kertas, file gagal dibaca, atau dokumen tidak dikenali. |
| `-` | Belum dianalisis. |

`verdict_description` selalu diisi sistem sesuai `verdict` (tidak dari LLM).
`fake_evidence` berisi **temuan** yang mendasari tingkat indikasi (bukan bukti
pemalsuan), mis. *"Revisi awal file berisi 1.062.145, versi akhir berisi 3.062.145"*.

**Parameter analisis** (`warning_flags`, dengan potongan `confidence_score`):

| Flag | Potongan | Sumber | Pengaruh ke `verdict` |
|---|---|---|---|
| `editing_software_detected` | 0.55 | Metadata: software pengedit (Acrobat, Word, Photoshop, iLovePDF, …) | INDIKASI KUAT |
| `content_changed_after_creation` | 0.55 | Struktur: teks halaman berbeda antara revisi awal dan versi akhir file (revisi yang hanya menambah tanda tangan/anotasi tidak dihitung) | INDIKASI KUAT |
| `hidden_text_under_overlay` | 0.55 | Struktur: dua angka berbeda di posisi yang sama (angka lama tertimpa) | INDIKASI KUAT |
| `text_covered_by_white_box` | 0.55 | Struktur: kotak putih digambar di atas teks | INDIKASI LEMAH |
| `white_patch_behind_amount` | 0.30 | Struktur: angka ditulis di atas tambalan putih seukuran angka (jejak alat redact) | INDIKASI LEMAH |
| `foreign_font_in_amount` | 0.30 | Struktur: angka memakai keluarga font yang tidak dipakai teks lain | INDIKASI LEMAH |
| `modified_after_creation` | 0.30 | Metadata: tanggal modifikasi > 5 menit setelah dibuat | skor |
| `metadata_mismatch` | 0.20 | Metadata: pembuat PDF tidak sesuai provider | skor |
| `unknown_provider` | 0.15 | Isi: provider tidak dikenal | skor |
| `missing_metadata` | 0.10 | Metadata: creator & producer kosong | skor |

Flag "skor" hanya mengurangi `confidence_score`; bila skor < 0.5 → `INDIKASI LEMAH`.
Forensik struktur hanya berlaku untuk PDF digital (halaman scan/foto dilewati).
"Save As" menghapus riwayat revisi, tetapi kotak putih, angka tertimpa, dan font
asing tetap terdeteksi karena ada di isi halaman. Dokumen yang dibuat ulang dari
nol (mis. diedit di browser lalu *print to PDF*) tidak meninggalkan jejak
struktur.

---

## Klasifikasi 2 Dimensi

Sistem mengklasifikasikan dokumen pada 2 dimensi independen, keduanya dikerjakan
oleh `document_agent` dalam satu LLM call:

1. **`doc_type`** → `"invoice"` / `"receipt"` / `"boarding_pass"` / `"unknown"` (kategori publik)
2. **`document_subtype`** → `"hotel"` / `"flight"` / `"train"` / `"ship"` / `"bus"` / `"toll"` / `"fuel"` / `"vehicle_rental"` / `"others"` / `"unknown"`

**Dokumen campuran (`mixed`)**: jika satu file berisi >1 subtype berbeda
(mis. hotel + flight, paket "Pesawat + Hotel", tol + BBM), ekstraksi
dihentikan. Response tetap `status="success"` (HTTP 200) dengan
`document_subtype="mixed"`, `detected_subtypes` (mis. `["hotel","flight"]`),
semua field data default, `extraction_confidence=0.0`,
`requires_manual_review=true`, dan `review_reasons` meminta unggah terpisah.
Analisis keaslian tetap disertakan. Status campuran diputuskan oleh sistem
dari `detected_subtypes` (≥2 subtype **berbeda**), bukan oleh LLM. Satu
subtype dengan beberapa segmen (PP, transit, e-ticket + boarding pass yang
sama) atau beberapa dokumen terpisah dengan subtype sama (mis. 2 booking hotel
berbeda) **tidak** dianggap campuran.

**Struk tol (`toll`) & BBM (`fuel`)** → `doc_type="receipt"` dengan field khusus:
tol `toll_gate`, `vehicle_class`; BBM `gas_station_id`, `fuel_type`,
`fuel_volume_liters`, `fuel_price_per_liter`; keduanya `vehicle_plate`.
Karena umumnya foto struk kertas, flag metadata/provider diabaikan dan verdict
menjadi `"TIDAK DAPAT DIANALISIS"`, kecuali ada temuan pengeditan lain yang
tetap dilaporkan.
SPBU di rest area tol tetap `fuel`; `toll` hanya untuk struk transaksi tol
(asal gerbang, golongan, saldo e-toll).

**Sewa kendaraan (`vehicle_rental`)** → surat keterangan sewa kendaraan,
kwitansi sewa mobil (termasuk tulisan tangan), atau invoice rental. Field
khusus: `vehicle_type`, `vehicle_plate`, `rental_start_date`,
`rental_end_date` (YYYY-MM-DD), `rental_days`; `merchant_name` = perusahaan
rental, `payer_name` = pembayar. Seperti struk tol/BBM, flag
metadata/provider diabaikan (verdict `"TIDAK DAPAT DIANALISIS"` kecuali ada
temuan pengeditan lain). Surat keterangan + kwitansi untuk sewa yang sama dihitung
**satu** transaksi (tidak dijumlah dua kali).

**Beberapa dokumen sejenis dalam satu file** (hotel, flight, train, ship, bus,
toll, fuel; mis. 2 booking hotel berbeda, beberapa tiket, 4 struk tol): semua
diekstrak dan dijumlahkan. `document_count` = jumlah dokumen, tiap dokumen
menjadi satu baris `items_purchased` (receipt) / `line_items` (invoice) dengan
nominal dokumen itu, `total_payment` = jumlah semua dokumen (dihitung ulang
oleh sistem), nomor struk/pesanan dipisah koma, dan untuk BBM
`fuel_volume_liters` = total liter. Field detail lain diambil dari dokumen
pertama. Indikasi palsu/edit tidak menghentikan ekstraksi; hanya tercatat di
`authenticity` dan `review_reasons`.

**Pembacaan halaman**: semua halaman PDF dibaca. Halaman hasil scan/foto atau
yang sebagian besar berupa gambar dibaca dengan OCR (Tesseract, 300 dpi);
akurasi OCR struk thermal terbatas, angka/tanggal bisa salah baca. PDF
berpassword ditolak sebagai gagal baca.

**`boarding_pass`**: pas naik pesawat tanpa bukti pembayaran. Selalu
`document_subtype="flight"`, `extraction_confidence=0.0`,
`requires_manual_review=true` (bukan bukti transaksi), data tidak diekstraksi,
analisis keaslian tetap dijalankan. Boarding pass yang jelas (judul "boarding
pass" + ≥2 sinyal gate/boarding time/seat/sequence/zone, tanpa penanda
e-ticket/transaksi) dideteksi **tanpa LLM**. Kasus yang ragu tetap
diklasifikasi oleh LLM. Dokumen gabungan yang memuat bukti pembayaran tetap
`invoice`/`receipt`.

**`trip_type`** (tiket transportasi flight/train/ship/bus/others):
`"one_way"` (sekali jalan) / `"round_trip"` (pulang-pergi/PP) / `"multi_city"` /
`"unknown"` (bukan tiket transportasi atau tidak bisa ditentukan). Untuk
`round_trip`, `route_from`/`route_to`/`flight_date` berisi segmen pergi dan
`return_date` berisi tanggal pulang. Boarding pass selalu `"unknown"` (tidak
diekstraksi).

Field `data.doc_type` di response **selalu** `invoice` / `receipt` / `boarding_pass` / `unknown` —
tidak pernah `hotel` / `flight`. Subtype dilaporkan di `data.document_subtype`.

---

## Klien Contoh

### curl

```bash
# Upload
curl -X POST http://localhost:8080/api/pinter/upload \
  -H "X-API-Key: your_key" \
  -F "file=@invoice.pdf"

# Poll
curl "http://localhost:8080/api/pinter/extract?trx_id=550e8400-..." \
  -H "X-API-Key: your_key"
```

### Python (httpx)

```python
import httpx, time

API = "http://localhost:8080"
HEADERS = {"X-API-Key": "your_key"}

with httpx.Client() as c:
    with open("invoice.pdf", "rb") as f:
        r = c.post(f"{API}/api/pinter/upload", files={"file": f}, headers=HEADERS)
    r.raise_for_status()
    trx_id = r.json()["trx_id"]

    while True:
        r = c.get(f"{API}/api/pinter/extract", params={"trx_id": trx_id}, headers=HEADERS)
        data = r.json()
        if data["status"] != "progress":
            break
        time.sleep(1.5)

    if data["status"] == "success":
        result = data["data"]
        print(f"doc_type: {result['doc_type']}")
        print(f"summary: {result['summary']}")
```

### JavaScript (browser)

```javascript
// Optional: set API key once via DevTools console
localStorage.setItem('pinterApiKey', '<key>');

const headers = () => {
  const k = localStorage.getItem('pinterApiKey');
  return k ? { 'X-API-Key': k } : {};
};

// Upload
const form = new FormData();
form.append('file', fileObj);
const up = await fetch('/api/pinter/upload', { method: 'POST', body: form, headers: headers() });
const { trx_id } = await up.json();

// Poll
while (true) {
  const r = await fetch(`/api/pinter/extract?trx_id=${trx_id}`, { headers: headers() });
  const body = await r.json();
  if (body.status !== 'progress') break;
  await new Promise(r => setTimeout(r, 1500));
}
```
