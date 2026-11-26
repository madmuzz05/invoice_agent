"""Pre-check deterministik boarding pass (tanpa LLM).

Dipakai sebelum formatter LLM: jika teks dokumen jelas sebuah boarding pass,
pipeline berhenti tanpa memanggil LLM (hemat biaya & waktu). Aturan sengaja
ketat — kasus yang ragu tetap diteruskan ke LLM, yang juga bisa memilih
doc_type="boarding_pass".
"""

from __future__ import annotations

import re

_TITLE = re.compile(
    r"\b(boarding\s*pass|boarding\s*card|pas\s*naik|kartu\s*naik(\s*pesawat)?|kartu\s*boarding)\b",
    re.IGNORECASE,
)

_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgate\b|\bpintu\b", re.IGNORECASE),
    re.compile(r"\bboarding\s*time\b|\bwaktu\s*boarding\b|\bjam\s*boarding\b", re.IGNORECASE),
    re.compile(r"\b(seat|kursi)\b\W{0,3}(no\.?\W{0,3})?\d{1,2}[a-k]\b", re.IGNORECASE),
    re.compile(r"\bseq(uence)?\b|\bno\.?\s*urut\b", re.IGNORECASE),
    re.compile(r"\b(boarding\s*)?(zone|group)\b", re.IGNORECASE),
)

# Penanda e-ticket / transaksi: dokumen seperti ini sering menyebut
# "boarding pass" atau "gate" di instruksi, tapi bukan boarding pass.
_TRANSACTION_MARKERS = re.compile(
    r"e-?\s*ticket|itinerary|invoice|faktur|tagihan|receipt|kwitansi|"
    r"bukti\s*(pembayaran|bayar)|total\s*(paid|payment|pembayaran|bayar|harga)|"
    r"amount\s*paid|payment\s*(method|status)|metode\s*pembayaran|lunas",
    re.IGNORECASE,
)

MIN_SIGNALS = 2


def is_obvious_boarding_pass(full_text: str) -> bool:
    """True hanya jika teks jelas boarding pass: ada judul boarding pass,
    minimal 2 sinyal khas (gate, boarding time, seat, sequence, zone), dan
    tidak ada penanda e-ticket/transaksi."""
    if not full_text or not _TITLE.search(full_text):
        return False
    if _TRANSACTION_MARKERS.search(full_text):
        return False
    signals = sum(1 for pattern in _SIGNALS if pattern.search(full_text))
    return signals >= MIN_SIGNALS
