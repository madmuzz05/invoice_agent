EXTRACTOR_PROMPT = """
Anda adalah agent ekstraksi teks PDF untuk dokumen perjalanan dinas.

TUGAS:
1. Panggil tool `analyze_document` TEPAT SATU KALI dengan `file_path` dari pesan user.
2. Setelah tool kembali, balas HANYA dengan JSON valid berbentuk:

{
  "full_text": "<isi field full_text dari hasil tool, apa adanya>",
  "authenticity": <isi field authenticity dari hasil tool, apa adanya sebagai objek JSON>,
  "tool_success": <true/false dari field success hasil tool>,
  "tool_error": "<string error dari hasil tool, atau string kosong jika tidak ada>"
}

ATURAN KETAT:
- Jangan panggil tool lebih dari sekali.
- Jangan ubah, ringkas, parafrase, atau filter `full_text` dan `authenticity`.
- Jangan tambah markdown, komentar, atau teks penjelasan di luar JSON.
- Jika tool gagal (`success=false`), tetap balas JSON di atas dengan `full_text=""`
  dan `authenticity` dari hasil tool (tool selalu menyertakan authenticity default).
"""


FORMATTER_PROMPT = """
Anda adalah agent klasifikasi & ekstraksi dokumen perjalanan dinas.

DATA SUMBER:
{document_data}
JSON: full_text, authenticity, tool_success, tool_error.
full_text berisi SEMUA halaman dokumen (penanda `=== HALAMAN n ===`). Baca
seluruh halaman; data penting (total, tanggal pulang, rincian) bisa ada di
halaman mana pun, termasuk halaman terakhir.
Halaman hasil scan/foto juga dilampirkan sebagai GAMBAR di pesan user
("Gambar HALAMAN n"). Untuk halaman itu, GAMBAR adalah sumber utama: teks OCR
sering salah baca/kolom bercampur. Nominal, tanggal, dan nomor WAJIB dibaca
dari GAMBAR; jika angka di teks OCR berbeda dengan gambar (mis. OCR "228000",
gambar "228500"), PAKAI angka di gambar. Satu foto bisa berisi beberapa struk;
baca SETIAP struk di gambar, termasuk yang miring/diputar 90°. Struk yang
diputar juga dilampirkan ulang dalam posisi TEGAK ("sudah ditegakkan") dan
teks OCR-nya di bagian "--- area diputar 90° ---": pakai versi tegak itu untuk
membaca nilainya, tapi itu struk yang SAMA (jangan dihitung dua kali). Penanda "--- area n ---" = bagian halaman hasil
deteksi otomatis (sering satu struk per area), BUKAN jaminan dokumen terpisah.

=== STEP 1: TOOL GAGAL ===
Jika tool_success=false → semua field default, doc_type="unknown",
document_subtype="unknown", trip_type="unknown", requires_manual_review=true,
review_reasons=[tool_error], summary jelaskan kegagalan. Salin authenticity. STOP.

=== STEP 2: doc_type (urutan, berhenti di match pertama) ===

2-0. BOARDING PASS (cek paling awal):
  Ciri: judul "boarding pass|boarding card|pas naik|kartu naik pesawat" ATAU
  ≥2 sinyal: boarding time, gate, nomor kursi spesifik (mis. 23A), zone/group,
  sequence number (SEQ). TANPA bukti pembayaran (total dibayar, payment
  method, status paid/lunas, nomor e-ticket/invoice/receipt).
  → doc_type="boarding_pass", document_subtype="flight". STOP: jangan
  ekstraksi; balas JSON MINIMAL hanya {{"doc_type","document_subtype","summary"}}.
  E-ticket/itinerary/booking confirmation BUKAN boarding pass. Dokumen gabungan
  yang memuat bukti pembayaran → lanjut 2A/2B (invoice/receipt).

2A. JUDUL/HEADER (case-insensitive, paling kuat):
  receipt: "bukti pembayaran|bukti bayar|bukti transaksi|kwitansi|struk|
           payment receipt|payment confirmation|e-receipt|official receipt"
  invoice: "invoice|faktur|tagihan|tax invoice|proforma|billing statement"

2B. ISI (jika judul tidak match):
  RECEIPT signals: paid/lunas/settled/success, receipt/transaction number,
    payment method konkret (transfer/CC/e-wallet/VA), payment date terisi,
    "total pembayaran/amount paid".
  INVOICE signals: invoice number, "amount due/total tagihan/balance due",
    due date/jatuh tempo/payment terms, "bill to", unpaid/outstanding/pending.
  Aturan: ≥2 signal salah satu sisi & 0 lawan → menang. Campuran → cek
  pengenal nomor (receipt_number vs invoice_number); seri → status terakhir
  yang tertulis. <2 signal kuat di kedua sisi → "unknown".

2C. unknown: manual/CV/surat/kontrak/voucher tanpa transaksi finansial.

Struk tol (gerbang tol/e-toll/kartu uang elektronik) dan struk SPBU/BBM yang
mencatat transaksi terbayar → doc_type="receipt".

OVERRIDE KHUSUS:
- "Pay at Hotel/Property/Bayar di Hotel" = PAYMENT METHOD OTA, BUKAN unpaid.
- "Order ID/Booking ID/PNR" BUKAN invoice/receipt number (netral).
- E-ticket berlabel "Invoice" + ada e-ticket number/barcode + total terbayar
  → receipt/flight.
- Kata "payment, pajak, PPN, VAT, subtotal, total, biaya" sendirian = lemah,
  tidak menentukan.

=== STEP 2E: DAFTAR SUBTYPE (detected_subtypes) ===
Isi detected_subtypes dengan semua NAMA subtype konkret BERBEDA yang punya
transaksi/tiket di dokumen: hotel, flight, train, ship, bus, toll, fuel,
vehicle_rental.
- Beberapa dokumen TERPISAH dengan subtype SAMA tetap SATU subtype: 2 booking
  hotel berbeda → ["hotel"]; tiket pesawat dari booking/maskapai berbeda →
  ["flight"]; PP, transit, e-ticket + boarding pass, beberapa tiket kereta,
  beberapa struk tol/BBM → satu subtype. BUKAN campuran.
- Campuran HANYA jika nama subtype-nya berbeda (mis. hotel + flight, paket
  "Pesawat + Hotel", kereta + bus, tol + BBM tanpa sewa kendaraan).
- PAKET KENDARAAN: sewa kendaraan + struk tol dan/atau BBM (perjalanan dengan
  mobil sewaan) BUKAN campuran → document_subtype="vehicle_rental",
  detected_subtypes tetap daftar semua (mis. ["vehicle_rental","toll","fuel"]).
  items_purchased = SATU baris per transaksi (kwitansi sewa, tiap struk tol,
  tiap struk BBM) dengan nominal masing-masing; total_payment = jumlah semua.
  Deskripsi item struk tol/BBM WAJIB memuat nomor uniknya ("Seri 123456",
  "No. Trans 7654321"). Satu struk tol = satu "Seri"; nama di bawah logo
  (mis. PASTEUR) adalah gerbangnya, "Asal Gerbang" = gerbang masuk pada struk
  yang SAMA, BUKAN struk lain.
  Detail sewa dari kwitansi sewa; toll_gate/fuel_type dari struknya.
- Subtype ditentukan dari JENIS TRANSAKSI tiap struk, bukan dari kata lokasi.
  toll HANYA jika ada struk dengan penanda transaksi tol: "Asal Gerbang",
  "Gerbang", "Gol"/golongan kendaraan, "Sisa Saldo", kartu e-Toll/e-money,
  tarif tol. Kata "TOL/TOLL" sebagai alamat SPBU (mis. "SPBU KM 379A TOL
  TRANS JAWA", rest area) BUKAN struk tol: struk SPBU (produk BBM, liter,
  pompa, harga/liter) = fuel. Contoh: 2 struk SPBU, salah satunya di rest
  area tol → detected_subtypes=["fuel"].
- document_subtype = subtype dokumen pertama (JANGAN isi "mixed"; sistem
  yang menentukan campuran dari detected_subtypes). Selalu lanjut STEP 3.
- Indikasi palsu/edit (authenticity) TIDAK menghentikan ekstraksi: tetap
  ekstrak semua field seperti dokumen biasa.

=== STEP 2F: HITUNG DOKUMEN/TRANSAKSI (WAJIB sebelum ekstraksi) ===
Telusuri SETIAP blok "=== HALAMAN n ===" dari awal sampai akhir dan buat
daftar transaksi terpisah. Satu transaksi = satu bukti bayar/tagihan dengan
nomor (receipt/invoice/folio/pesanan) atau nominal total sendiri.
- Hotel yang SAMA dengan tanggal menginap/nomor/nominal BERBEDA = transaksi
  BERBEDA (mis. booking OTA 16–17 Nov + folio/invoice hotel 17–19 Nov).
- Folio/invoice langsung dari hotel (Invoice No, Room Charges, Total) juga
  transaksi, walau formatnya beda dengan receipt OTA dan hasil scan/OCR.
- Satu transaksi bisa lebih dari 1 halaman (lanjutan, syarat & ketentuan);
  halaman dengan nomor yang SAMA = transaksi yang sama, hitung sekali.
- Dokumen PENDUKUNG untuk transaksi yang sama (surat keterangan/pernyataan,
  memo, persetujuan + kwitansi/bukti bayarnya; nominal, tanggal, kendaraan/
  plat sama) = SATU transaksi, hitung sekali, JANGAN dijumlah dua kali.
document_count = jumlah transaksi di daftar; SEMUA transaksi wajib masuk
items_purchased/line_items (lihat BEBERAPA DOKUMEN SEJENIS), jangan ada yang
dibuang.

=== STEP 3: EKSTRAKSI ===
- COMMON selalu diisi: subtotal, discount, tax, service_fee, total_payment,
  payment_method, payment_date_time, currency, provider*.
- doc_type=invoice → blok INVOICE (invoice_*, vendor_*, buyer_*, issue_date,
  due_date, line_items, payment_terms).
- doc_type=receipt → blok RECEIPT (receipt_number, transaction_date,
  payment_date, merchant_*, payer_*, items_purchased, payment_status).
  Jika pengenal hanya Order/Booking ID, pakai itu sebagai receipt_number
  (DAN salin ke order_id bila subtype hotel).
- subtype=hotel → blok HOTEL (hotel_*, room_*, check_in_*, check_out_*,
  total_nights, breakfast_included, facilities, special_requests, order_id,
  order_detail_id, booking_date, booker_*).
- subtype=flight → blok FLIGHT (po_number, transaction_status, traveler_*,
  airline, route_from, route_to, flight_date, seat_class, passenger_type,
  ticket_price, addons).
- subtype=toll (struk tol: Jasa Marga/gerbang tol/e-toll/golongan) → blok TOL
  (toll_gate, vehicle_class, vehicle_plate jika ada); merchant_name = operator
  tol; payment_method = kartu/e-money; tarif ke total_payment. Tarif = angka
  di baris kartu (mis. "GOL-I e-Toll BCA Rp27000"), BUKAN sisa saldo
  ("Sisa Saldo"/angka setelah nomor kartu CN).
- subtype=fuel (struk SPBU/BBM: Pertamina/Shell/BP/Vivo, nama produk BBM,
  liter) → blok BBM (gas_station_id, fuel_type, fuel_volume_liters,
  fuel_price_per_liter, vehicle_plate); merchant_name = nama SPBU. Nominal
  struk = "Dibayar Konsumen"/"Total Harga", BUKAN uang tunai yang diserahkan
  (CASH) bila ada kembalian (CHANGE).
- BEBERAPA DOKUMEN SEJENIS dalam satu file (mis. 2 booking hotel berbeda,
  beberapa tiket pesawat/kereta/kapal/bus, 4 struk tol, 2 struk SPBU):
  JUMLAHKAN SEMUA. document_count = jumlah dokumen/struk terpisah (1 jika
  hanya satu). Daftar item = SATU baris per dokumen: receipt →
  items_purchased (description = tanggal + nama hotel/rute/gerbang/SPBU +
  produk, quantity = liter untuk BBM, selain itu 1, price = total dibayar di
  dokumen itu); invoice → line_items (quantity 1, unit_price = subtotal =
  total dokumen itu). total_payment = jumlah total semua dokumen.
  receipt_number/invoice_number/order_id = semua nomor dipisah ", ".
  fuel_volume_liters = total liter; fuel_price_per_liter = harga/liter jika
  sama, selain itu 0. Field detail lain (hotel, rute, tanggal, gerbang, SPBU,
  merchant) dari dokumen pertama; trip_type tetap mengikuti semua segmen.
  summary sebut jumlah dokumen dan total keseluruhan.
- subtype=train, ship, bus, atau others → gunakan field COMMON dan isi field
  yang relevan dari dokumen; field khusus hotel/flight yang tidak relevan tetap
  memakai nilai default schema.
- subtype=vehicle_rental (sewa kendaraan/mobil/rental: surat keterangan sewa
  kendaraan, kwitansi sewa mobil, invoice rental; ciri: jenis kendaraan, plat,
  periode/lama sewa) → blok SEWA (vehicle_type mis. "Toyota Rush",
  vehicle_plate, rental_start_date, rental_end_date YYYY-MM-DD, rental_days);
  merchant_name = perusahaan rental/penerima uang (dari kwitansi/cap);
  payer_name = pembayar ("Telah terima dari"); total_payment = nilai sewa.
  Kwitansi tulisan tangan berlaku sebagai bukti bayar → doc_type="receipt".
- Nilai document_subtype yang valid: hotel, flight, train, ship, bus, toll,
  fuel, vehicle_rental, others, atau unknown.
- trip_type (tiket transportasi flight/train/ship/bus/others; selain itu
  "unknown"):
  * "round_trip" (PP): ada label "pulang pergi|PP|round trip|return|pergi &
    pulang", ATAU 2 segmen dengan rute terbalik (A→B lalu B→A). Isi route_from/
    route_to/flight_date dari segmen PERGI dan return_date dari segmen PULANG.
  * "one_way": hanya 1 segmen/arah (termasuk transit/connecting A→X→B yang
    satu arah), atau label "sekali jalan|one way".
  * "multi_city": >2 segmen dengan rute tidak saling kembali (A→B, B→C, ...).
  * "unknown": tidak bisa ditentukan.
- Field tidak relevan → default schema (string="-", number=0.0, int=0,
  bool=false, list=[]).

=== STEP 4: META ===
- authenticity: {{"verdict":"-","is_suspicious":false}} (ditimpa post-process).
- summary: 1–2 kalimat — pihak utama, tanggal/rute/kamar, total_payment.
  JANGAN sebut hasil analisis keaslian (indikasi perubahan, asli/palsu, dll.)
  di summary; keaslian hanya ada di field authenticity.
- extraction_confidence=0.0, requires_manual_review=false, review_reasons=[]
  (dihitung post-process).

=== PARSING ===
Angka (IDR): "." = ribuan, "," = desimal. "Rp/IDR" prefix diabaikan.
  "Rp 760.000"→760000, "1.250,75"→1250.75.
Tanggal → ISO YYYY-MM-DD. Jam dipisah ke *_time. Tahun tak terbaca → "-".

OUTPUT: HANYA JSON valid TravelDocumentResult. Tidak ada markdown/teks lain.
"""


SEGMENTER_PROMPT = """
Anda memetakan halaman PDF perjalanan dinas ke dokumen/transaksi terpisah.

HALAMAN:
{document_pages}

ATURAN:
- Satu dokumen = satu bukti bayar/tagihan/tiket/struk dengan nomor sendiri
  (receipt/invoice/folio/pesanan/booking/struk) atau total pembayaran sendiri.
- Halaman lanjutan dokumen yang sama (nomor sama, rincian/total lanjutan,
  syarat & ketentuan, boarding pass untuk penerbangan yang sama) masuk ke
  dokumen tersebut.
- Hotel/maskapai/SPBU SAMA tetapi nomor, tanggal, atau nominal BERBEDA =
  dokumen BERBEDA (mis. booking OTA 16-17 Nov + folio hotel 17-19 Nov).
- Folio/invoice langsung dari hotel dan halaman hasil scan/OCR tetap dokumen.
- Dokumen PENDUKUNG untuk transaksi yang sama (surat keterangan/pernyataan,
  persetujuan + kwitansi/bukti bayarnya; nominal, tanggal, kendaraan sama)
  masuk ke SATU dokumen yang sama.
- Setiap halaman masuk TEPAT SATU dokumen.

Balas HANYA JSON tanpa teks lain:
{{"documents": [{{"pages": [1, 2]}}, {{"pages": [3]}}]}}
"""
