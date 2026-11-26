"""Cek deterministik nominal struk tol vs sisa saldo kartu (tanpa LLM).

Struk e-toll memuat dua angka besar: tarif ("Gol-1 e-Toll MANDIRI 78500") dan
sisa saldo kartu ("Sisa Saldo Rp.222353" / "CN:6032... Rp.140900"). LLM kadang
mengambil sisa saldo sebagai nominal. Modul ini membaca kedua angka dari teks
OCR lalu:
- satu struk dengan tepat satu tarif terbaca → nominal yang mirip saldo
  dikoreksi ke tarif; nominal yang beda dengan tarif OCR (mis. 228500 vs
  228000 — OCR/LLM bisa salah baca 5↔0) ditandai untuk review manual;
- item paket yang nominalnya mirip saldo ditandai untuk review manual.
"""

from __future__ import annotations

import re
from typing import Any

REVIEW_MARKER = "[PERIKSA: nominal mirip sisa saldo kartu]"
_MISMATCH_MARKER = "[PERIKSA: nominal beda dengan OCR {ocr}]"
_MARKER_PREFIX = "[PERIKSA: nominal"
REVIEW_REASON = (
    "nominal struk tol perlu dicek manual (mirip sisa saldo kartu atau beda "
    "antara teks OCR dan gambar)"
)

_AMOUNT = r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d{4,7})(?!\d)"
# "e-Toll BCA 48500", OCR: "TOLL WANDIRI 78500", "e-Tol] BCA 48500"
_TARIFF = re.compile(
    r"t[o0][l1i\]|]{1,2}\w?\W{0,2}\s+[a-z]{3,}\w*\s+(?:rp\W{0,2})?" + _AMOUNT, re.IGNORECASE
)
# "Sisa Saldo Rp.222353" (OCR bisa memecah kata), "CN:6032000000001234 Rp.140900"
_BALANCE = (
    re.compile(r"sisa\s*sal\s*d\s*o\W{0,4}(?:rp\W{0,3})?" + _AMOUNT, re.IGNORECASE),
    re.compile(r"cn\W{0,3}\d{10,}(?!\d)\s*rp\W{0,3}" + _AMOUNT, re.IGNORECASE),
)


def _to_int(raw: str) -> int:
    return int(re.sub(r"[.,]", "", raw))


def toll_amounts(text: str) -> tuple[list[int], list[int]]:
    """(tarif, sisa saldo) yang terbaca dari teks OCR."""
    tariffs = [_to_int(m.group(1)) for m in _TARIFF.finditer(text or "")]
    balances = [_to_int(m.group(1)) for p in _BALANCE for m in p.finditer(text or "")]
    return tariffs, balances


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(100.0, 0.01 * b)


def _looks_like_balance(amount: float, tariffs: list[int], balances: list[int]) -> bool:
    return (
        amount > 0
        and any(_close(amount, b) for b in balances)
        and not any(_close(amount, t) for t in tariffs)
    )


def check_toll_amounts(result: Any, full_text: str) -> dict[str, Any]:
    """Update field TravelDocumentResult (dict kosong jika tidak ada masalah)."""
    subtypes = set(result.detected_subtypes) | {result.document_subtype}
    if "toll" not in subtypes:
        return {}
    tariffs, balances = toll_amounts(full_text)
    if not balances and not tariffs:
        return {}
    unique_tariffs = sorted(set(tariffs))
    items = list(result.items_purchased)

    if result.document_subtype == "toll" and len(items) <= 1:
        if _looks_like_balance(result.total_payment, unique_tariffs, balances):
            if len(unique_tariffs) == 1:
                tariff = float(unique_tariffs[0])
                updates: dict[str, Any] = {"total_payment": tariff}
                if result.subtotal > 0:
                    updates["subtotal"] = tariff
                if items:
                    updates["items_purchased"] = [items[0].model_copy(update={"price": tariff})]
                return updates
            return {"summary": f"{result.summary} {REVIEW_MARKER}"}
        if (
            len(unique_tariffs) == 1
            and result.total_payment > 0
            and result.total_payment != unique_tariffs[0]
            and _MARKER_PREFIX not in (result.summary or "")
        ):
            marker = _MISMATCH_MARKER.format(ocr=unique_tariffs[0])
            return {"summary": f"{result.summary} {marker}"}
        return {}

    flagged = False
    checked = []
    for item in items:
        if _looks_like_balance(item.price, unique_tariffs, balances) and REVIEW_MARKER not in (
            item.description or ""
        ):
            item = item.model_copy(update={"description": f"{item.description} {REVIEW_MARKER}"})
            flagged = True
        checked.append(item)
    return {"items_purchased": checked} if flagged else {}


def needs_toll_review(result: Any) -> bool:
    return _MARKER_PREFIX in (result.summary or "") or any(
        _MARKER_PREFIX in (item.description or "") for item in result.items_purchased
    )


def review_marker(text: str) -> str:
    """Penanda review di teks (untuk diteruskan saat hasil digabung)."""
    start = (text or "").find(_MARKER_PREFIX)
    if start < 0:
        return ""
    end = text.find("]", start)
    return text[start:end + 1] if end > start else ""
