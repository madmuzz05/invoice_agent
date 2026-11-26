"""Penyesuaian analisis keaslian untuk struk tol, BBM & kwitansi sewa kendaraan.

Analisis keaslian berbasis metadata PDF (creator/producer, provider OTA)
dirancang untuk e-ticket/invoice digital. Struk tol & SPBU umumnya foto struk
kertas thermal, sehingga flag metadata/provider tidak bermakna. Untuk subtype
ini flag tersebut diabaikan; jika tidak ada bukti pengeditan yang tersisa,
verdict menjadi "TIDAK DAPAT DIANALISIS".
"""

from __future__ import annotations

from ..models.authenticity import VERDICT_NOT_ANALYZABLE, DocumentAuthenticity

RECEIPT_ONLY_SUBTYPES = ("toll", "fuel", "vehicle_rental")
_LABELS = {"toll": "tol", "fuel": "BBM", "vehicle_rental": "sewa kendaraan"}
UNVERIFIED_VERDICT = VERDICT_NOT_ANALYZABLE

# Flag yang tidak relevan untuk foto/scan struk kertas.
_IRRELEVANT_FLAGS = {"unknown_provider", "missing_metadata", "metadata_mismatch"}


def adjust_authenticity_for_subtype(
    authenticity: DocumentAuthenticity, document_subtype: str
) -> DocumentAuthenticity:
    """Kembalikan authenticity yang disesuaikan untuk struk tol/BBM.

    - Subtype lain: dikembalikan apa adanya.
    - Masih ada bukti kuat (software pengedit / modifikasi pasca-cetak):
      dikembalikan apa adanya (tetap INDIKASI LEMAH/KUAT).
    - Selain itu: verdict TIDAK DAPAT DIANALISIS.
    """
    if document_subtype not in RECEIPT_ONLY_SUBTYPES:
        return authenticity

    relevant_flags = [f for f in authenticity.warning_flags if f not in _IRRELEVANT_FLAGS]
    if relevant_flags:
        return authenticity

    label = _LABELS[document_subtype]
    return authenticity.model_copy(
        update={
            "verdict": UNVERIFIED_VERDICT,
            "is_suspicious": False,
            "confidence_score": 0.0,
            "warning_flags": [],
            "fake_evidence": [],
            "analysis_notes": (
                f"Struk/kwitansi {label}: keaslian tidak bisa diverifikasi dari metadata "
                "dokumen (umumnya foto/scan kertas). Tidak ditemukan jejak pengeditan; "
                "periksa struk secara manual bila diperlukan."
            ),
        }
    )
