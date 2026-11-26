"""Satu file berisi beberapa dokumen sejenis (mis. 2 booking hotel berbeda,
beberapa tiket pesawat, 4 struk tol, 2 struk SPBU).

LLM mencatat tiap dokumen sebagai satu baris item (receipt → items_purchased
dengan price = total dokumen; invoice → line_items dengan subtotal = total
dokumen). Total dihitung ulang di kode supaya konsisten: total_payment =
jumlah semua dokumen, dan untuk BBM fuel_volume_liters = jumlah liter.
"""

from __future__ import annotations

import re
from typing import Any

MULTI_DOCUMENT_SUBTYPES = (
    "hotel", "flight", "train", "ship", "bus", "toll", "fuel", "vehicle_rental",
)
# Struk tol/BBM (dan paket sewa kendaraan + tol/BBM): satu struk = satu baris
# item, jadi jumlah baris = jumlah struk meski document_count dari LLM salah.
_RECEIPT_ONLY_SUBTYPES = ("toll", "fuel")
# Nomor unik struk di deskripsi item ("Seri 123456", "No. Trans 7654321").
_RECEIPT_SERIAL = re.compile(r"\b(?:no\.?\s*)?(?:seri|trans)\w*\W{0,3}(\d{4,})", re.IGNORECASE)


def _one_item_per_receipt(result: Any) -> bool:
    if result.document_subtype in _RECEIPT_ONLY_SUBTYPES:
        return True
    # Paket kendaraan: kwitansi sewa + struk tol/BBM, satu baris per transaksi.
    return result.document_subtype == "vehicle_rental" and len(result.detected_subtypes) > 1


def _dedupe_by_serial(items: list[Any]) -> list[Any]:
    """Buang item dengan nomor struk yang sama (struk terbaca dua kali, mis.
    dari gambar halaman dan versi tegaknya)."""
    seen: set[str] = set()
    unique = []
    for item in items:
        match = _RECEIPT_SERIAL.search(item.description or "")
        if match:
            if match.group(1) in seen:
                continue
            seen.add(match.group(1))
        unique.append(item)
    return unique


def _document_amounts(items: list[Any], invoice: bool) -> tuple[list[float], list[float]]:
    if invoice:
        return [item.subtotal for item in items], [item.quantity for item in items]
    return [item.price for item in items], [item.quantity for item in items]


def sum_multi_receipts(result: Any) -> dict[str, Any]:
    """Kembalikan update field untuk TravelDocumentResult (dict kosong jika
    tidak berlaku).

    - hotel/flight/train/ship/bus/sewa tunggal: hanya jika document_count ≥2
      dan jumlah baris item sama dengan document_count (satu baris per
      dokumen), supaya rincian item satu dokumen (kamar + sarapan, pajak)
      tidak ikut dijumlah.
    - toll/fuel & paket kendaraan: ≥2 baris item; item dengan nomor struk sama
      dibuang, document_count = jumlah baris.
    Semua baris harus punya nominal > 0.
    """
    subtype = result.document_subtype
    if subtype not in MULTI_DOCUMENT_SUBTYPES:
        return {}
    invoice = result.doc_type == "invoice"
    items = list(result.line_items if invoice else result.items_purchased)
    per_receipt = _one_item_per_receipt(result)
    updates: dict[str, Any] = {}
    if per_receipt and not invoice:
        unique = _dedupe_by_serial(items)
        if len(unique) != len(items):
            items = unique
            updates["items_purchased"] = items
    amounts, quantities = _document_amounts(items, invoice)
    if len(amounts) < 2 or any(amount <= 0 for amount in amounts):
        return updates
    if not per_receipt and result.document_count != len(amounts):
        return updates

    total = round(sum(amounts), 2)
    updates["total_payment"] = total
    if per_receipt:
        updates["document_count"] = len(amounts)
    if result.subtotal > 0:
        updates["subtotal"] = total
    if subtype == "fuel" and all(quantity > 0 for quantity in quantities):
        updates["fuel_volume_liters"] = round(sum(quantities), 2)
    return updates
