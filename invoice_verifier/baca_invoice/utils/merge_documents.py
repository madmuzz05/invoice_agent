"""Gabungkan hasil ekstraksi per dokumen (satu file berisi beberapa dokumen).

Input: hasil `validate_agent_result` tiap dokumen, urut halaman. Output: satu
dict TravelDocumentResult (meta confidence/review dihitung ulang oleh caller).

- Dokumen non-transaksi (unknown, boarding_pass) diabaikan bila ada dokumen
  transaksi lain.
- ≥2 subtype konkret berbeda → document_subtype="mixed" (validator menghapus
  data & menghentikan ekstraksi).
- Subtype sama → dijumlahkan: satu baris item per dokumen, total = jumlah
  total semua dokumen, nomor dokumen digabung dengan ", ".
"""

from __future__ import annotations

from typing import Any

from .mixed_subtype import MIXED_SUBTYPE, combined_subtype, normalize_detected_subtypes
from .toll_check import review_marker as toll_review_marker

_NON_TRANSACTION = ("unknown", "boarding_pass")
_TRANSPORT = ("flight", "train", "ship", "bus", "others")
_SUMMED_AMOUNTS = ("total_payment", "subtotal", "tax", "discount", "service_fee")
_EMPTY = ("", "-")
_VEHICLE_DETAIL_FIELDS = (
    "toll_gate", "vehicle_class", "gas_station_id", "fuel_type", "vehicle_plate",
)


def _text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text in _EMPTY else text


def _join_unique(values: list[Any]) -> str:
    seen: list[str] = []
    for value in values:
        for part in _text(value).split(","):
            part = part.strip()
            if part and part not in seen:
                seen.append(part)
    return ", ".join(seen) or "-"


def _subtypes(result: dict) -> list[str]:
    found = normalize_detected_subtypes(result.get("detected_subtypes"))
    for extra in normalize_detected_subtypes([result.get("document_subtype")]):
        if extra not in found:
            found.append(extra)
    return found


def describe_document(result: dict) -> str:
    """Label singkat satu dokumen untuk baris item & summary."""
    subtype = result.get("document_subtype")
    if subtype == "hotel":
        parts = [result.get("hotel_name"), result.get("check_in_date")]
    elif subtype in _TRANSPORT:
        route = " → ".join(
            filter(None, (_text(result.get("route_from")), _text(result.get("route_to"))))
        )
        parts = [result.get("airline"), route, result.get("flight_date")]
    elif subtype == "toll":
        parts = ["Tol", result.get("toll_gate"), result.get("transaction_date")]
    elif subtype == "fuel":
        parts = [
            result.get("merchant_name"), result.get("fuel_type"), result.get("transaction_date")
        ]
    elif subtype == "vehicle_rental":
        parts = [
            "Sewa", result.get("vehicle_type"), result.get("vehicle_plate"),
            result.get("rental_start_date"),
        ]
    else:
        parts = []
    parts = [_text(part) for part in parts if _text(part)]
    if not parts:
        parts = [
            _text(result.get(key))
            for key in ("merchant_name", "vendor_name", "provider")
            if _text(result.get(key))
        ][:1]
    return " ".join(parts) or "Dokumen"


def _rows(result: dict) -> list[dict[str, Any]]:
    """Baris item untuk satu dokumen: pakai item per dokumen jika dokumen itu
    sendiri berisi beberapa dokumen, selain itu satu baris dengan totalnya."""
    if result.get("doc_type") == "invoice":
        items = [
            {"description": i.get("description"), "quantity": i.get("quantity"),
             "price": i.get("subtotal")}
            for i in result.get("line_items") or []
        ]
    else:
        items = list(result.get("items_purchased") or [])
    count = int(result.get("document_count") or 1)
    if count >= 2 and len(items) == count and all((i.get("price") or 0) > 0 for i in items):
        return items

    quantity = 1.0
    if result.get("document_subtype") == "fuel" and (result.get("fuel_volume_liters") or 0) > 0:
        quantity = float(result["fuel_volume_liters"])
    description = describe_document(result)
    marker = toll_review_marker(str(result.get("summary") or ""))
    if marker:
        description = f"{description} {marker}"
    return [{
        "description": description,
        "quantity": quantity,
        "price": float(result.get("total_payment") or 0.0),
    }]


def _trip_fields(transport: list[dict]) -> dict[str, Any]:
    if len(transport) < 2:
        return {}
    first, last = transport[0], transport[-1]
    origin, final = _text(first.get("route_from")), _text(last.get("route_to"))
    if origin and final and origin.lower() == final.lower():
        return {"trip_type": "round_trip", "return_date": last.get("flight_date") or "-"}
    return {"trip_type": "multi_city", "return_date": "-"}


def _rupiah(amount: float) -> str:
    return f"Rp {amount:,.0f}".replace(",", ".")


def _plate(value: Any) -> str:
    return "".join(_text(value).upper().split())


def _identity(result: dict) -> tuple[str, ...] | None:
    """Kunci identitas transaksi; None jika data identitas tidak lengkap."""
    subtype = result.get("document_subtype")
    if subtype == "vehicle_rental":
        key = (_plate(result.get("vehicle_plate")), _text(result.get("rental_start_date")))
    elif subtype == "hotel":
        key = (_text(result.get("hotel_name")).lower(), _text(result.get("check_in_date")))
    elif subtype in _TRANSPORT:
        key = (
            _text(result.get("route_from")).lower(), _text(result.get("route_to")).lower(),
            _text(result.get("flight_date")),
        )
    else:
        number = _text(result.get("receipt_number")) or _text(result.get("invoice_number"))
        key = (number, _text(result.get("transaction_date")))
    return key if all(key) else None


def _is_empty(value: Any) -> bool:
    return value in (None, "", "-", 0, 0.0, [], {}) or _text(value) == ""


def _fill(base: dict, other: dict) -> dict:
    """Lengkapi field kosong `base` dengan nilai dari `other`."""
    merged = dict(base)
    for key, value in other.items():
        if _is_empty(merged.get(key)) and not _is_empty(value):
            merged[key] = value
    return merged


def _dedupe(transactions: list[dict]) -> list[dict]:
    """Dokumen pendukung untuk transaksi yang sama (mis. surat keterangan sewa
    + kwitansinya) tidak boleh dijumlah dua kali: subtype, nominal, dan kunci
    identitas sama → digabung jadi satu."""
    unique: list[dict] = []
    for result in transactions:
        identity, total = _identity(result), float(result.get("total_payment") or 0)
        match = next(
            (
                i for i, kept in enumerate(unique)
                if identity is not None
                and kept.get("document_subtype") == result.get("document_subtype")
                and float(kept.get("total_payment") or 0) == total > 0
                and _identity(kept) == identity
            ),
            None,
        )
        if match is None:
            unique.append(result)
        else:
            unique[match] = _fill(unique[match], result)
    return unique


def merge_document_results(results: list[dict]) -> dict:
    """Gabungkan hasil per dokumen menjadi satu hasil."""
    if len(results) == 1:
        return results[0]

    transactions = _dedupe([r for r in results if r.get("doc_type") not in _NON_TRANSACTION])
    if not transactions:
        return results[0]
    if len(transactions) == 1:
        return transactions[0]

    subtypes: list[str] = []
    for result in transactions:
        for subtype in _subtypes(result):
            if subtype not in subtypes:
                subtypes.append(subtype)
    combined = combined_subtype(subtypes) if subtypes else None
    # Detail utama dari dokumen bersubtype gabungan (mis. kwitansi sewa pada
    # paket sewa + tol + BBM), selain itu dokumen pertama.
    first = next(
        (r for r in transactions if r.get("document_subtype") == combined), transactions[0]
    )
    if combined is None or any(r.get("document_subtype") == MIXED_SUBTYPE for r in transactions):
        return {
            **first,
            "document_subtype": MIXED_SUBTYPE,
            "detected_subtypes": subtypes,
            "summary": "Dokumen berisi beberapa jenis transaksi: " + ", ".join(subtypes) + ".",
        }

    rows = [row for result in transactions for row in _rows(result)]
    doc_type = "receipt" if any(r.get("doc_type") == "receipt" for r in transactions) else "invoice"
    merged: dict[str, Any] = {
        **first,
        "document_subtype": combined,
        "doc_type": doc_type,
        "detected_subtypes": subtypes,
        "document_count": len(rows),
        "receipt_number": _join_unique(
            [r.get("receipt_number") if _text(r.get("receipt_number")) else r.get("invoice_number")
             for r in transactions]
        ),
        "invoice_number": _join_unique([r.get("invoice_number") for r in transactions]),
        "order_id": _join_unique([r.get("order_id") for r in transactions]),
    }
    for key in _SUMMED_AMOUNTS:
        merged[key] = round(sum(float(r.get(key) or 0.0) for r in transactions), 2)

    if doc_type == "invoice":
        merged["line_items"] = [
            {"description": row["description"], "quantity": row["quantity"],
             "unit_price": row["price"], "subtotal": row["price"]}
            for row in rows
        ]
        merged["items_purchased"] = []
    else:
        merged["items_purchased"] = rows
        merged["line_items"] = []

    fuel = [r for r in transactions if r.get("document_subtype") == "fuel"]
    if fuel:
        merged["fuel_volume_liters"] = round(
            sum(float(r.get("fuel_volume_liters") or 0.0) for r in fuel), 2
        )
        prices = {float(r.get("fuel_price_per_liter") or 0.0) for r in fuel}
        merged["fuel_price_per_liter"] = prices.pop() if len(prices) == 1 else 0.0
    # Paket kendaraan: detail tol/BBM/kendaraan dilengkapi dari struk terkait.
    for other in transactions:
        for key in _VEHICLE_DETAIL_FIELDS:
            if _is_empty(merged.get(key)) and not _is_empty(other.get(key)):
                merged[key] = other[key]

    transport = [r for r in transactions if r.get("document_subtype") in _TRANSPORT]
    merged.update(_trip_fields(transport))

    details = "; ".join(
        f"{row['description']} ({_rupiah(float(row['price'] or 0))})" for row in rows
    )
    merged["summary"] = (
        f"{len(rows)} dokumen {combined}: {details}. "
        f"Total {_rupiah(merged['total_payment'])}."
    )
    return merged
