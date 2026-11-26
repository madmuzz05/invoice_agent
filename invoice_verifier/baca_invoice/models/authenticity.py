from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

# Hasil analisis keaslian berupa TINGKAT INDIKASI, bukan vonis asli/palsu:
# sistem hanya melihat jejak di metadata & struktur file, tidak bisa memastikan
# kebenaran isi dokumen.
VERDICT_NO_INDICATION = "TIDAK ADA INDIKASI"
VERDICT_WEAK_INDICATION = "INDIKASI LEMAH"
VERDICT_STRONG_INDICATION = "INDIKASI KUAT"
VERDICT_NOT_ANALYZABLE = "TIDAK DAPAT DIANALISIS"

VERDICT_DESCRIPTIONS: dict[str, str] = {
    VERDICT_NO_INDICATION: (
        "Tidak ditemukan indikasi perubahan pada metadata maupun struktur dokumen. "
        "Ini bukan jaminan keaslian: dokumen yang dibuat ulang dari awal tidak "
        "meninggalkan jejak struktur."
    ),
    VERDICT_WEAK_INDICATION: (
        "Ditemukan tanda yang bisa berarti dokumen pernah diubah, tetapi juga bisa "
        "muncul pada dokumen yang wajar (mis. metadata tidak sesuai provider, tambalan "
        "putih di belakang angka, font angka berbeda). Perlu dicek manual."
    ),
    VERDICT_STRONG_INDICATION: (
        "Ditemukan jejak langsung perubahan isi: software pengedit di metadata, isi "
        "berbeda antara revisi file, atau angka lama tertimpa angka baru. Cocokkan "
        "dengan dokumen sumber sebelum diproses."
    ),
    VERDICT_NOT_ANALYZABLE: (
        "Keaslian tidak bisa dianalisis dari file ini, misalnya hasil scan/foto struk "
        "kertas (metadata milik scanner, bukan penerbit), file gagal dibaca, atau "
        "dokumen tidak dikenali. Periksa dokumen secara manual bila diperlukan."
    ),
}


class DocumentAuthenticity(BaseModel):
    verdict: str = Field(
        default="-",
        description=(
            "Tingkat indikasi perubahan: TIDAK ADA INDIKASI | INDIKASI LEMAH | "
            "INDIKASI KUAT | TIDAK DAPAT DIANALISIS ('-' bila belum dianalisis)."
        ),
    )
    is_suspicious: bool = False
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    detected_provider: str = "-"
    pdf_creator: str = "-"
    pdf_producer: str = "-"
    creation_date: str = "-"
    modification_date: str = "-"
    was_modified: bool = False
    warning_flags: list[str] = Field(default_factory=list)
    fake_evidence: list[str] = Field(
        default_factory=list,
        description="Temuan konkret yang mendasari tingkat indikasi (bukan bukti pemalsuan)",
    )
    analysis_notes: str = "-"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict_description(self) -> str:
        """Penjelasan arti `verdict` — selalu konsisten dengan nilainya."""
        return VERDICT_DESCRIPTIONS.get(self.verdict, "-")
