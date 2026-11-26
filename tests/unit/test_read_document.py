from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

# Un-stub fitz if it was mocked in conftest
sys.modules.pop("fitz", None)
fitz = importlib.import_module("fitz")

# Load real modules directly from file (baca_invoice.tools di-stub oleh conftest).
_TOOLS_DIR = Path(__file__).resolve().parents[2] / "invoice_verifier" / "baca_invoice" / "tools"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_regions = _load("baca_invoice.tools.regions", "regions.py")
_real_pdf_tool = _load("baca_invoice.tools.real_pdf", "pdf.py")
read_document = _real_pdf_tool.read_document
read_pdf = _real_pdf_tool.read_pdf
detect_text_regions = _regions.detect_text_regions


def test_read_pdf_digital(tmp_path):
    pdf_path = tmp_path / "digital.pdf"
    doc = fitz.open()
    page = doc.new_page(width=300, height=100)
    page.insert_text((50, 50), "DIGITAL INVOICE #001")
    doc.save(str(pdf_path))
    doc.close()

    result = read_pdf(str(pdf_path))
    assert result["success"] is True
    assert result["total_pages"] == 1
    assert "DIGITAL INVOICE #001" in result["full_text"]


def test_read_image_document(tmp_path):
    img_path = tmp_path / "invoice.png"
    doc = fitz.open()
    page = doc.new_page(width=300, height=100)
    page.insert_text((50, 50), "IMAGE INVOICE #999")
    pix = page.get_pixmap()
    pix.save(str(img_path))
    doc.close()

    result = read_document(str(img_path))
    assert result["success"] is True
    assert result["total_pages"] == 1
    assert result["metadata"]["success"] is True
    # PyMuPDF OCR extracts text from the rasterized image
    assert "INVOICE" in result["full_text"] or len(result["full_text"]) > 0


def test_read_nonexistent_file():
    result = read_document("/nonexistent/file.png")
    assert result["success"] is False
    assert "tidak ditemukan" in result["error"].lower()


def _has_tesseract() -> bool:
    try:
        return bool(fitz.get_tessdata())
    except Exception:
        return False


requires_ocr = pytest.mark.skipif(
    not _has_tesseract(), reason="Tesseract OCR tidak terpasang"
)


def _text_image(text: str) -> bytes:
    tmp = fitz.open()
    page = tmp.new_page(width=500, height=300)
    page.insert_text((40, 80), text, fontsize=22)
    png = page.get_pixmap(dpi=150).tobytes("png")
    tmp.close()
    return png


def test_read_pdf_reads_every_page(tmp_path):
    pdf_path = tmp_path / "many.pdf"
    doc = fitz.open()
    for i in range(1, 31):
        doc.new_page().insert_text((50, 72), f"ISI HALAMAN {i:02d}")
    doc.save(str(pdf_path))
    doc.close()

    result = read_pdf(str(pdf_path))
    assert result["total_pages"] == 30
    for i in range(1, 31):
        assert f"=== HALAMAN {i} ===" in result["full_text"]
        assert f"ISI HALAMAN {i:02d}" in result["full_text"]


@requires_ocr
def test_read_pdf_ocr_scanned_and_mixed_pages(tmp_path):
    pdf_path = tmp_path / "mixed.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((50, 72), "HALAMAN TEKS INV-001")
    scanned = doc.new_page()
    scanned.insert_image(fitz.Rect(50, 50, 550, 350), stream=_text_image("SCAN TOTAL 777000"))
    mixed = doc.new_page()
    mixed.insert_text((50, 40), "Header kecil", fontsize=8)
    mixed.insert_image(fitz.Rect(50, 60, 550, 360), stream=_text_image("CAMPURAN HOTEL SANTIKA"))
    doc.save(str(pdf_path))
    doc.close()

    pages = read_pdf(str(pdf_path))["pages"]
    assert "HALAMAN TEKS INV-001" in pages[0]["text"]
    assert "SCAN TOTAL 777000" in pages[1]["text"]
    assert "Header kecil" in pages[2]["text"]
    assert "CAMPURAN HOTEL SANTIKA" in pages[2]["text"]


def test_read_pdf_small_logo_does_not_trigger_ocr(tmp_path, monkeypatch):
    pdf_path = tmp_path / "logo.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 200), "E-TICKET GARUDA")
    page.insert_image(fitz.Rect(50, 20, 110, 60), stream=_text_image("LOGO"))
    doc.save(str(pdf_path))
    doc.close()

    ocr_calls: list[int] = []
    original = fitz.Page.get_textpage_ocr

    def _spy(page, *args, **kwargs):
        ocr_calls.append(page.number)
        return original(page, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_textpage_ocr", _spy)
    assert "E-TICKET GARUDA" in read_pdf(str(pdf_path))["full_text"]
    assert ocr_calls == []


def test_read_pdf_password_protected(tmp_path):
    pdf_path = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((50, 72), "RAHASIA")
    doc.save(
        str(pdf_path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        user_pw="user123",
        owner_pw="owner123",
    )
    doc.close()

    result = read_pdf(str(pdf_path))
    assert result["success"] is False
    assert "password" in result["error"].lower()


def _receipt_lines(page, x, y, title, lines=12):
    page.insert_text((x, y), title, fontsize=11)
    for i in range(lines):
        page.insert_text((x, y + 16 * (i + 1)), f"Item {i:02d} : {1000 * (i + 1):,}", fontsize=10)


def _page_gray(page):
    import numpy as np

    pix = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def test_detect_regions_finds_each_receipt_in_grid(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    _receipt_lines(page, 40, 60, "STRUK SATU")
    _receipt_lines(page, 330, 90, "STRUK DUA")
    _receipt_lines(page, 60, 440, "STRUK TIGA")
    _receipt_lines(page, 340, 470, "STRUK EMPAT")
    regions = detect_text_regions(_page_gray(page))
    assert len(regions) == 4
    page_regions = [fitz.Rect(*(v / (150 / 72) for v in r)) for r in regions]
    assert _real_pdf_tool._has_side_by_side(page_regions)


def test_label_value_columns_of_one_receipt_are_not_side_by_side():
    page = fitz.open().new_page(width=600, height=800)
    for i in range(10):
        page.insert_text((60, 300 + 16 * i), f"Label {i}", fontsize=10)
        page.insert_text((420, 300 + 16 * i), f"{1000 * i:,}", fontsize=10)
    scale = 150 / 72
    regions = [fitz.Rect(*(v / scale for v in r)) for r in detect_text_regions(_page_gray(page))]
    assert not _real_pdf_tool._has_side_by_side(regions)


def _scan_pdf_with_sideways_receipt(tmp_path):
    """Halaman scan: struk tegak + struk ditempel menyamping (diputar 90°)."""
    src = fitz.open()
    page = src.new_page(width=600, height=800)
    _receipt_lines(page, 40, 60, "STRUK TEGAK 01/02/2026")
    for i in range(10):
        page.insert_text(
            (420 + 18 * i, 760), f"TOL {i:02d} 12/03/2026 Rp 55500", fontsize=11, rotate=90
        )
    pix = page.get_pixmap(dpi=200)
    scan = fitz.open()
    scan_page = scan.new_page(width=600, height=800)
    scan_page.insert_image(scan_page.rect, pixmap=pix)
    path = tmp_path / "scan.pdf"
    scan.save(str(path))
    return path


@requires_ocr
def test_sideways_receipt_is_read_upright(tmp_path):
    path = _scan_pdf_with_sideways_receipt(tmp_path)
    text = read_pdf(str(path))["full_text"]
    assert "--- area diputar 90°" in text
    assert "55500" in text.split("--- area diputar 90°")[1]
    labels = [label for label, _ in _real_pdf_tool.scanned_page_images(str(path))]
    assert len(labels) == 2 and "ditegakkan" in labels[1]


@requires_ocr
def test_upright_scan_has_no_sideways_area(tmp_path):
    src = fitz.open()
    page = src.new_page(width=600, height=800)
    _receipt_lines(page, 40, 60, "STRUK SATU 01/02/2026", lines=30)
    scan = fitz.open()
    scan.new_page(width=600, height=800).insert_image(
        fitz.Rect(0, 0, 600, 800), pixmap=page.get_pixmap(dpi=200)
    )
    path = tmp_path / "upright.pdf"
    scan.save(str(path))
    assert "--- area diputar" not in read_pdf(str(path))["full_text"]
