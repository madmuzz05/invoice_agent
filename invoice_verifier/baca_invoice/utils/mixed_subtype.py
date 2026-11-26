"""Aturan dokumen campuran: satu file berisi >1 subtype berbeda.

Dokumen seperti ini tidak diekstraksi (document_subtype="mixed"); client
diminta mengunggah tiap dokumen secara terpisah. Beberapa dokumen terpisah
dengan subtype SAMA (mis. 2 booking hotel) bukan campuran.
"""

from __future__ import annotations

from typing import Any

MIXED_SUBTYPE = "mixed"
CONCRETE_SUBTYPES = (
    "hotel", "flight", "train", "ship", "bus", "toll", "fuel", "vehicle_rental",
)


def normalize_detected_subtypes(value: Any) -> list[str]:
    """Ambil subtype konkret yang valid, unik, urutan dipertahankan."""
    if isinstance(value, str):
        value = value.replace(";", ",").split(",")
    if not isinstance(value, (list, tuple)):
        return []
    seen: list[str] = []
    for item in value:
        name = str(item or "").strip().lower()
        if name in CONCRETE_SUBTYPES and name not in seen:
            seen.append(name)
    return seen


def mixed_review_reason(subtypes: list[str]) -> str:
    found = ", ".join(subtypes) if subtypes else "lebih dari satu jenis"
    return (
        f"dokumen berisi lebih dari 1 subtype ({found}): ekstraksi dihentikan, "
        "unggah tiap dokumen secara terpisah"
    )


# Paket biaya kendaraan satu perjalanan: sewa kendaraan + tol + BBM bukan
# dokumen campuran (selama ada sewa kendaraannya) → subtype "vehicle_rental".
VEHICLE_RENTAL = "vehicle_rental"
VEHICLE_GROUP = (VEHICLE_RENTAL, "toll", "fuel")


def combined_subtype(subtypes: list[str]) -> str | None:
    """Subtype gabungan untuk daftar subtype konkret: satu subtype → itu;
    paket kendaraan (sewa + tol/BBM) → "vehicle_rental"; selain itu None
    (campuran)."""
    unique = list(dict.fromkeys(subtypes))
    if len(unique) == 1:
        return unique[0]
    if VEHICLE_RENTAL in unique and set(unique) <= set(VEHICLE_GROUP):
        return VEHICLE_RENTAL
    return None


def resolve_subtype(document_subtype: str, detected_subtypes: Any) -> str:
    """Keputusan "mixed" diambil di kode, bukan oleh LLM.

    - Paket kendaraan (sewa + tol/BBM) → "vehicle_rental".
    - ≥2 subtype konkret BERBEDA lainnya di detected_subtypes → "mixed".
    - LLM menandai "mixed" padahal hanya ada 1 subtype (mis. 2 booking hotel
      berbeda) → pakai subtype tersebut ("unknown" jika daftar kosong).
    - Selain itu document_subtype dipertahankan.
    """
    subtypes = normalize_detected_subtypes(detected_subtypes)
    if len(subtypes) >= 2:
        return combined_subtype(subtypes) or MIXED_SUBTYPE
    if document_subtype == MIXED_SUBTYPE:
        return subtypes[0] if subtypes else "unknown"
    return document_subtype
