from __future__ import annotations

import importlib
import importlib.util
import shutil
import sys
from pathlib import Path

sys.modules.pop("fitz", None)
fitz = importlib.import_module("fitz")

_TOOLS = Path(__file__).resolve().parents[2] / "invoice_verifier" / "baca_invoice" / "tools"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


forensics = _load("baca_invoice.tools.real_pdf_forensics", "pdf_forensics.py")
_load("baca_invoice.tools.constants", "constants.py")
authenticity = _load("baca_invoice.tools.real_authenticity", "authenticity.py")

LINES = [
    "RECEIPT Hotel Santika Jakarta - Traveloka",
    "No: 1880000000000000001",
    "Check-in 2026-11-16, 1 malam, Deluxe Room",
    "Deluxe Room                 1.062.145",
    "TOTAL                       1.062.145",
    "Status: PAID via Virtual Account",
]
META = {"creator": "Chromium", "producer": "Skia/PDF m120"}


def _original(path: Path) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    for i, text in enumerate(LINES):
        page.insert_text((60, 90 + 22 * i), text, fontname="cour", fontsize=11)
    doc.set_metadata(META)
    doc.save(str(path), garbage=4, deflate=True)
    return path


def _save_as(doc, path: Path) -> None:
    doc.set_metadata(META)  # pemalsu mengembalikan metadata asli
    tmp = path.with_suffix(".tmp")
    doc.save(str(tmp), garbage=4, deflate=True, clean=True)
    doc.close()
    shutil.move(tmp, path)


def _flags(path: Path) -> list[str]:
    return forensics.analyze_pdf_structure(str(path))["flags"]


def test_original_has_no_flags(tmp_path):
    assert _flags(_original(tmp_path / "asli.pdf")) == []


def test_incremental_edit_recovers_original_amount(tmp_path):
    path = tmp_path / "edit.pdf"
    shutil.copy(_original(tmp_path / "asli.pdf"), path)
    doc = fitz.open(str(path))
    page = doc[0]
    for rect in page.search_for("1.062.145"):
        page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    page.insert_text((288, 156), "3.062.145", fontname="helv", fontsize=11)
    doc.saveIncr()
    result = forensics.analyze_pdf_structure(str(path))
    assert forensics.CONTENT_CHANGED in result["flags"]
    assert "1.062.145" in result["evidence"][0] and "3.062.145" in result["evidence"][0]


def test_save_as_still_leaves_whiteout_and_foreign_font(tmp_path):
    path = tmp_path / "saveas.pdf"
    shutil.copy(_original(tmp_path / "asli.pdf"), path)
    doc = fitz.open(str(path))
    page = doc[0]
    rects = page.search_for("1.062.145")
    for rect in rects:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    for rect in rects:  # angka baru ditulis di posisi angka lama
        page.insert_text((rect.x0, rect.y1 - 2.2), "3.062.145", fontname="helv", fontsize=11)
    _save_as(doc, path)
    flags = _flags(path)
    assert forensics.CONTENT_CHANGED not in flags  # riwayat revisi hilang karena Save As
    assert forensics.WHITE_PATCH in flags  # redact: teks lama hilang, tambalan putih tersisa
    assert forensics.FOREIGN_FONT in flags


def test_whiteout_with_same_font_leaves_hidden_old_amount(tmp_path):
    path = tmp_path / "whiteout.pdf"
    shutil.copy(_original(tmp_path / "asli.pdf"), path)
    doc = fitz.open(str(path))
    page = doc[0]
    for rect in page.search_for("1.062.145"):
        page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
        page.insert_text((rect.x0, rect.y1 - 2.2), "3.062.145", fontname="cour", fontsize=11)
    _save_as(doc, path)
    flags = _flags(path)
    assert forensics.WHITEOUT in flags
    assert forensics.HIDDEN_TEXT in flags


def test_signature_added_later_is_not_flagged(tmp_path):
    path = tmp_path / "signed.pdf"
    shutil.copy(_original(tmp_path / "asli.pdf"), path)
    doc = fitz.open(str(path))
    doc[0].add_freetext_annot(fitz.Rect(300, 400, 500, 440), "Disetujui")
    doc.saveIncr()
    result = forensics.analyze_pdf_structure(str(path))
    assert result["revisions"] == 2
    assert result["flags"] == []


def test_bold_total_and_white_table_background_are_not_flagged(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(50, 70, 450, 220), color=None, fill=(1, 1, 1))  # latar tabel
    for i, text in enumerate(LINES[:-2]):
        page.insert_text((60, 90 + 22 * i), text, fontname="helv", fontsize=11)
    total = "TOTAL                       1.062.145"
    page.insert_text((60, 178), total, fontname="hebo", fontsize=11)
    path = tmp_path / "bold.pdf"
    doc.save(str(path))
    assert _flags(path) == []


def test_flags_drive_authenticity_verdict(tmp_path):
    meta = {"success": True, "creator": "Chromium", "producer": "Skia/PDF m120"}
    text = "Traveloka receipt"
    clean = authenticity.analyze_authenticity(meta, text, {"flags": [], "evidence": []})
    assert clean["verdict"] == "TIDAK ADA INDIKASI"
    whiteout = authenticity.analyze_authenticity(
        meta, text, {"flags": [forensics.WHITEOUT], "evidence": ["x"]}
    )
    assert whiteout["verdict"] == "INDIKASI LEMAH"
    assert "x" in whiteout["fake_evidence"]
    edited = authenticity.analyze_authenticity(
        meta, text, {"flags": [forensics.CONTENT_CHANGED], "evidence": ["y"]}
    )
    assert edited["verdict"] == "INDIKASI KUAT"
    patch_only = authenticity.analyze_authenticity(
        meta, text, {"flags": [forensics.WHITE_PATCH], "evidence": []}
    )
    assert patch_only["verdict"] == "INDIKASI LEMAH"  # temuan ringan tetap dilaporkan
    assert forensics.WHITE_PATCH in patch_only["warning_flags"]


def test_non_pdf_and_scans_are_skipped(tmp_path):
    png = tmp_path / "foto.png"
    page = fitz.open().new_page()
    page.insert_text((50, 80), "TOTAL 1.062.145")
    page.get_pixmap().save(str(png))
    assert forensics.analyze_pdf_structure(str(png))["flags"] == []


def test_every_verdict_has_a_description_in_the_response():
    from baca_invoice.models.authenticity import VERDICT_DESCRIPTIONS, DocumentAuthenticity

    assert set(VERDICT_DESCRIPTIONS) == {
        "TIDAK ADA INDIKASI", "INDIKASI LEMAH", "INDIKASI KUAT", "TIDAK DAPAT DIANALISIS",
    }
    for verdict, description in VERDICT_DESCRIPTIONS.items():
        dumped = DocumentAuthenticity(verdict=verdict).model_dump()
        assert dumped["verdict_description"] == description
    # Deskripsi mengikuti verdict walau diubah lewat model_copy (tidak bisa basi).
    changed = DocumentAuthenticity(verdict="TIDAK ADA INDIKASI").model_copy(
        update={"verdict": "INDIKASI KUAT"}
    )
    assert changed.model_dump()["verdict_description"] == VERDICT_DESCRIPTIONS["INDIKASI KUAT"]
    assert DocumentAuthenticity().model_dump()["verdict_description"] == "-"


def test_openpdf_is_a_valid_creator_for_known_providers():
    constants = sys.modules["baca_invoice.tools.constants"]
    with_itext = [n for n, c in constants.KNOWN_PROVIDERS.items() if "itext" in c["valid_creators"]]
    assert with_itext and all(
        "openpdf" in constants.KNOWN_PROVIDERS[n]["valid_creators"] for n in with_itext
    )
    assert not any("openpdf" in sw for sw in constants.SOFTWARE_LABELS)  # bukan editor

    meta = {"success": True, "creator": "", "producer": "OpenPDF 1.3.30"}
    result = authenticity.analyze_authenticity(meta, "E-ticket Garuda Indonesia GA-123", None)
    assert result["detected_provider"] == "garuda"
    assert "metadata_mismatch" not in result["warning_flags"]
    assert result["verdict"] == "TIDAK ADA INDIKASI"
