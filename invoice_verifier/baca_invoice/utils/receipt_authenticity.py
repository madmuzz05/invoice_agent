"""Penyesuaian analisis keaslian untuk struk tol & BBM.

Analisis keaslian berbasis metadata PDF (creator/producer, provider OTA)
dirancang untuk e-ticket/invoice digital. Struk tol & SPBU umumnya foto struk
kertas thermal, sehingga flag metadata/provider tidak bermakna. Untuk subtype
ini flag tersebut diabaikan; jika tidak ada bukti pengeditan yang tersisa,
verdict menjadi "TIDAK DIVERIFIKASI" (bukan AUTENTIK).
"""

from __future__ import annotations

from ..models.authenticity import DocumentAuthenticity

RECEIPT_ONLY_SUBTYPES = ("toll", "fuel")
UNVERIFIED_VERDICT = "TIDAK DIVERIFIKASI"

# Flag yang tidak relevan untuk foto/scan struk kertas.
_IRRELEVANT_FLAGS = {"unknown_provider", "missing_metadata", "metadata_mismatch"}


def adjust_authenticity_for_subtype(
    authenticity: DocumentAuthenticity, document_subtype: str
) -> DocumentAuthenticity:
    """Kembalikan authenticity yang disesuaikan untuk struk tol/BBM.

    - Subtype lain: dikembalikan apa adanya.
    - Masih ada bukti kuat (software pengedit / modifikasi pasca-cetak):
      dikembalikan apa adanya (tetap PALSU/MENCURIGAKAN).
    - Selain itu: verdict TIDAK DIVERIFIKASI, tidak mencurigakan.
    """
    if document_subtype not in RECEIPT_ONLY_SUBTYPES:
        return authenticity

    relevant_flags = [f for f in authenticity.warning_flags if f not in _IRRELEVANT_FLAGS]
    if relevant_flags:
        return authenticity

    label = "tol" if document_subtype == "toll" else "BBM"
    return authenticity.model_copy(
        update={
            "verdict": UNVERIFIED_VERDICT,
            "is_suspicious": False,
            "confidence_score": 0.0,
            "warning_flags": [],
            "fake_evidence": [],
            "analysis_notes": (
                f"Struk {label}: keaslian tidak bisa diverifikasi dari metadata dokumen "
                "(umumnya foto struk kertas). Tidak ditemukan bukti pengeditan; "
                "periksa struk secara manual bila diperlukan."
            ),
        }
    )
