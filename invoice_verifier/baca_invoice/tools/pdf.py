from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

import fitz
import numpy as np

from .regions import detect_sideways_text, detect_text_regions

logger = logging.getLogger(__name__)


def _parse_pdf_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        cleaned = raw.replace("D:", "").strip()[:14]
        return datetime.strptime(cleaned, "%Y%m%d%H%M%S").isoformat()
    except Exception:
        return raw or None


def _compute_modification_info(
    creation_date: str | None, mod_date: str | None
) -> tuple[bool, int | None]:
    if not (creation_date and mod_date and creation_date != mod_date):
        return False, None
    try:
        gap_seconds = (
            datetime.fromisoformat(mod_date) - datetime.fromisoformat(creation_date)
        ).total_seconds()
    except Exception:
        return False, None
    if gap_seconds <= 300:
        return False, None
    return True, int(gap_seconds // 86400)


# OCR halaman yang sebagian besar berupa gambar meski sudah ada sedikit teks
# (mis. header teks + isi hasil scan). Rasio = total area gambar / area halaman.
_OCR_IMAGE_AREA_RATIO = 0.2
# 300 dpi ≈ resolusi asli scan HP (CamScanner dll.); 150 dpi terlalu kabur
# untuk struk thermal. Waktu OCR ~0.3 detik/halaman.
_OCR_DPI = 300
_OCR_LANGUAGE = "eng"


def _image_area_ratio(page: fitz.Page) -> float:
    page_area = page.rect.get_area()
    if not page_area:
        return 0.0
    covered = 0.0
    for info in page.get_image_info():
        bbox = fitz.Rect(info.get("bbox") or (0, 0, 0, 0)) & page.rect
        if not bbox.is_empty:
            covered += bbox.get_area()
    return min(covered / page_area, 1.0)


def _needs_ocr(page: fitz.Page, text: str) -> bool:
    """Halaman tanpa teks (scan/foto) atau sebagian besar isinya gambar."""
    return not text.strip() or _image_area_ratio(page) >= _OCR_IMAGE_AREA_RATIO


# Resolusi gambar untuk deteksi area teks (sisi terpanjang, px).
_REGION_DETECT_SIZE = 1600


def _gray(pix: fitz.Pixmap) -> np.ndarray:
    if pix.colorspace is None or pix.colorspace.n != 1:
        pix = fitz.Pixmap(fitz.csGRAY, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width)


def _scan_image(page: fitz.Page) -> tuple[np.ndarray, fitz.Rect] | None:
    """Gambar asli halaman scan/foto (satu gambar dominan) sebagai grayscale
    yang sudah diputar sesuai rotasi halaman, beserta posisinya (koordinat
    halaman). Deteksi pada gambar asli lebih stabil daripada halaman yang
    di-render ulang (resampling membuat tepi kertas antar-struk samar)."""
    infos = [i for i in page.get_image_info(xrefs=True) if i.get("xref")]
    unrotated_area = page.mediabox.get_area()
    if len(infos) != 1:
        biggest = max(infos, key=lambda i: fitz.Rect(i["bbox"]).get_area(), default=None)
        if biggest is None or fitz.Rect(biggest["bbox"]).get_area() < 0.5 * unrotated_area:
            return None
        infos = [biggest]
    info = infos[0]
    a, b, c, d = (info.get("transform") or (1, 0, 0, 1, 0, 0))[:4]
    if abs(b) > 1e-6 or abs(c) > 1e-6 or a <= 0 or d <= 0:
        return None  # gambar miring/terbalik di dalam PDF → pakai render halaman
    try:
        gray = _gray(fitz.Pixmap(page.parent, info["xref"]))
    except Exception:
        return None
    # bbox gambar dalam koordinat halaman TANPA rotasi; tampilan halaman diputar
    # `page.rotation` derajat searah jarum jam.
    turns = (page.rotation // 90) % 4
    if turns:
        gray = np.rot90(gray, k=-turns)
    rect = fitz.Rect(info["bbox"]) * page.rotation_matrix
    rect.normalize()
    return np.ascontiguousarray(gray), rect


def _page_regions(page: fitz.Page) -> list[fitz.Rect]:
    """Area teks halaman (mis. tiap struk dalam satu foto), koordinat halaman."""
    scan = _scan_image(page)
    if scan is not None:
        gray, origin = scan
        scale_x, scale_y = origin.width / gray.shape[1], origin.height / gray.shape[0]
    else:
        longest = max(page.rect.width, page.rect.height) or 1
        zoom = _REGION_DETECT_SIZE / longest
        gray = _gray(page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False))
        scale_x = scale_y = 1 / zoom
        origin = page.rect
    return [
        fitz.Rect(
            origin.x0 + x0 * scale_x, origin.y0 + y0 * scale_y,
            origin.x0 + x1 * scale_x, origin.y0 + y1 * scale_y,
        )
        for x0, y0, x1, y1 in detect_text_regions(gray)
    ]


_PLAUSIBLE_WORD = re.compile(r"\b[A-Za-z]{3,12}\b")


def _is_plausible_word(word: str) -> bool:
    lower = word.lower()
    vowels = sum(ch in "aeiou" for ch in lower)
    return 0 < vowels < len(lower) and not re.search(r"(.)\1\1", lower)


def _ocr_score(text: str) -> int:
    """Skor keterbacaan hasil OCR: tanggal, angka/nominal, dan kata yang masuk
    akal (punya vokal & konsonan, tanpa huruf berulang) — OCR teks yang salah
    orientasi menghasilkan sampah yang tidak lolos pola ini."""
    return (
        5 * len(re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", text))
        + len(re.findall(r"\b\d{1,3}(?:[.,]\d{3})+\b|\b\d{4,}\b", text))
        + sum(_is_plausible_word(w) for w in _PLAUSIBLE_WORD.findall(text))
    )


def _upright(pix: fitz.Pixmap, degrees: int) -> fitz.Pixmap:
    doc = fitz.open()
    page = doc.new_page(width=pix.height, height=pix.width)
    page.insert_image(page.rect, pixmap=pix, rotate=degrees)
    return page.get_pixmap(dpi=72)


def _ocr_pixmap(pix: fitz.Pixmap) -> str:
    with fitz.open("pdf", pix.pdfocr_tobytes(language=_OCR_LANGUAGE)) as ocr_doc:
        return ocr_doc[0].get_text()


def _sideways_region(page: fitz.Page) -> tuple[bytes, str] | None:
    """Area struk yang ditempel menyamping (teks diputar 90°) → (JPEG tegak,
    teks OCR tegak). Tanpa model: arah teks dari profil tinta, arah putar
    (90°/270°) dipilih dari skor OCR yang lebih terbaca."""
    scan = _scan_image(page)
    if scan is None:
        return None
    gray, origin = scan
    box = detect_sideways_text(gray)
    if box is None:
        return None
    sx, sy = origin.width / gray.shape[1], origin.height / gray.shape[0]
    clip = fitz.Rect(
        origin.x0 + box[0] * sx, origin.y0 + box[1] * sy,
        origin.x0 + box[2] * sx, origin.y0 + box[3] * sy,
    ) & page.rect
    pix = page.get_pixmap(clip=clip, dpi=_OCR_DPI, alpha=False)
    # Pembanding: tanpa diputar. Area hanya dianggap menyamping bila versi yang
    # diputar jelas lebih terbaca (cegah false positive pada struk tegak).
    unrotated_score = _ocr_score(_ocr_pixmap(pix))
    candidates = []
    for degrees in (90, 270):
        upright = _upright(pix, degrees)
        text = _ocr_pixmap(upright)
        candidates.append((_ocr_score(text), upright, text))
    score, upright, text = max(candidates, key=lambda c: c[0])
    if score < 1.5 * unrotated_score + 3:
        return None
    longest = max(upright.width, upright.height)
    if longest > 2000:
        upright = fitz.Pixmap(upright, int(upright.width * 2000 / longest),
                              int(upright.height * 2000 / longest), None)
    return upright.tobytes(output="jpg", jpg_quality=85), text.strip()


def _has_side_by_side(regions: list[fitz.Rect]) -> bool:
    """Ada dokumen terpisah yang berdampingan (mis. 2 kolom struk dalam satu
    foto)? Hanya pada kondisi ini OCR satu halaman mencampur baris antar-struk.

    Pasangan area dihitung bila: kolomnya terpisah, tingginya saling bertumpuk,
    keduanya cukup tinggi (≥25% tinggi seluruh area teks), dan TIDAK sejajar
    rapi atas-bawah. Kolom label/nilai di dalam satu struk biasanya pendek atau
    sejajar persis, sehingga tidak dipisah.
    """
    if len(regions) < 2:
        return False
    total = fitz.Rect(regions[0])
    for region in regions[1:]:
        total |= region
    min_height, tolerance = 0.25 * total.height, 0.03 * total.height
    for i, a in enumerate(regions):
        for b in regions[i + 1:]:
            if a.height < min_height or b.height < min_height:
                continue
            overlap_y = min(a.y1, b.y1) - max(a.y0, b.y0)
            apart_x = a.x1 <= b.x0 + 0.1 * b.width or b.x1 <= a.x0 + 0.1 * a.width
            aligned = abs(a.y0 - b.y0) <= tolerance and abs(a.y1 - b.y1) <= tolerance
            if apart_x and overlap_y > 0.3 * min(a.height, b.height) and not aligned:
                return True
    return False


def _ocr_regions(page: fitz.Page, regions: list[fitz.Rect]) -> str:
    """OCR tiap area terpisah supaya teks struk yang berdampingan tidak
    tercampur per baris."""
    blocks = []
    margin = 0.01 * max(page.rect.width, page.rect.height)
    for index, rect in enumerate(regions, start=1):
        clip = (rect + (-margin, -margin, margin, margin)) & page.rect
        pix = page.get_pixmap(clip=clip, dpi=_OCR_DPI, alpha=False)
        with fitz.open("pdf", pix.pdfocr_tobytes(language=_OCR_LANGUAGE)) as ocr_doc:
            text = ocr_doc[0].get_text().strip()
        if text:
            blocks.append(f"--- area {index} ---\n{text}")
    return "\n\n".join(blocks)


def _page_text(page: fitz.Page) -> str:
    """Teks satu halaman. OCR dipakai bila halaman tanpa teks (scan/foto) atau
    sebagian besar isinya gambar (teks di dalam gambar tidak ikut get_text).
    Halaman scan/foto dengan beberapa area teks (mis. 4 struk dalam satu foto)
    di-OCR per area."""
    text = page.get_text()
    has_text = bool(text.strip())
    if not _needs_ocr(page, text):
        return text
    sideways = ""
    if not has_text:
        try:
            region = _sideways_region(page)
            if region and region[1]:
                sideways = f"\n\n--- area diputar 90° (sudah ditegakkan) ---\n{region[1]}"
        except Exception as exc:
            logger.warning("OCR teks menyamping gagal di halaman %d: %s", page.number + 1, exc)
        try:
            regions = _page_regions(page)
            if len(regions) >= 2 and _has_side_by_side(regions):
                region_text = _ocr_regions(page, regions)
                if region_text.strip():
                    return region_text + sideways
        except Exception as exc:
            logger.warning("OCR per area gagal di halaman %d: %s", page.number + 1, exc)
    try:
        # full=False: OCR hanya area gambar, teks asli halaman tetap dipakai.
        tp = page.get_textpage_ocr(language=_OCR_LANGUAGE, dpi=_OCR_DPI, full=not has_text)
        ocr_text = page.get_text(textpage=tp)
    except Exception as exc:
        logger.warning("OCR gagal di halaman %d: %s", page.number + 1, exc)
        return text
    return (ocr_text if ocr_text.strip() else text) + sideways


def _read_pages(doc: fitz.Document, wanted: set[int] | None) -> list[dict[str, Any]]:
    return [
        {"page": page.number + 1, "text": _page_text(page)}
        for page in doc
        if wanted is None or page.number + 1 in wanted
    ]


def _open_as_pdf(file_path: str) -> fitz.Document:
    doc = fitz.open(file_path)
    if doc.is_pdf:
        return doc
    try:
        return fitz.open("pdf", doc.convert_to_pdf())
    finally:
        doc.close()


def scanned_page_images(
    file_path: str,
    page_numbers: list[int] | None = None,
    max_side_px: int = 2400,
    limit: int = 8,
) -> list[tuple[str, bytes]]:
    """Render halaman hasil scan/foto (yang butuh OCR) menjadi JPEG untuk
    dikirim ke LLM vision. OCR struk foto sering kacau (kolom bercampur,
    angka salah baca); gambar asli membantu LLM membaca dengan benar. Area
    struk yang ditempel menyamping ikut dikirim sebagai gambar tambahan yang
    sudah ditegakkan.

    Returns: list (label, bytes JPEG), maksimal `limit` halaman.
    """
    wanted = set(page_numbers) if page_numbers else None
    images: list[tuple[str, bytes]] = []
    try:
        with _open_as_pdf(file_path) as doc:
            if doc.needs_pass:
                return []
            for page in doc:
                number = page.number + 1
                if wanted is not None and number not in wanted:
                    continue
                if not _needs_ocr(page, page.get_text()):
                    continue
                longest = max(page.rect.width, page.rect.height) or 1
                zoom = min(max_side_px / longest, 3.0)
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                jpeg = pix.tobytes(output="jpg", jpg_quality=80)
                images.append((f"Gambar HALAMAN {number}:", jpeg))
                sideways = _sideways_region(page)
                if sideways is not None:
                    images.append((
                        f"Gambar HALAMAN {number} — area struk yang diputar 90°, sudah "
                        "ditegakkan (struk yang sama dengan di gambar halaman; JANGAN "
                        "dihitung dua kali):",
                        sideways[0],
                    ))
                if len(images) >= limit:
                    break
    except Exception as exc:
        logger.warning("scanned_page_images gagal: %s", exc)
        return []
    return images


def count_pages(file_path: str) -> int:
    """Jumlah halaman tanpa membaca teks/OCR (0 jika gagal dibuka)."""
    try:
        with fitz.open(file_path) as doc:
            return 0 if doc.needs_pass else len(doc)
    except Exception:
        return 0


def read_pdf(file_path: str, page_numbers: list[int] | None = None) -> dict[str, Any]:
    """Baca seluruh teks (semua halaman) dan metadata dokumen (PDF atau gambar)
    dalam satu kali open file.

    Args:
        page_numbers: nomor halaman (mulai 1) yang dibaca; None = semua halaman.
            Penanda "=== HALAMAN n ===" tetap memakai nomor halaman asli.

    Returns:
        dict dengan key:
          success, file_path, total_pages, full_text, pages, metadata, error.
        `metadata` berisi: title, author, creator, producer, creation_date,
        modification_date, was_modified, modification_gap_days.
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": f"File tidak ditemukan: {file_path}"}
    try:
        doc = fitz.open(file_path)
        try:
            if doc.needs_pass:
                return {
                    "success": False,
                    "error": "PDF dilindungi password, isi dokumen tidak bisa dibaca.",
                    "file_path": file_path,
                }
            raw_meta = doc.metadata or {}
            page_count = len(doc)
            wanted = set(page_numbers) if page_numbers else None
            if doc.is_pdf:
                pages = _read_pages(doc, wanted)
            else:
                pdf_doc = fitz.open("pdf", doc.convert_to_pdf())
                try:
                    pages = _read_pages(pdf_doc, wanted)
                finally:
                    pdf_doc.close()
        finally:
            doc.close()
    except Exception as exc:
        return {"success": False, "error": str(exc), "file_path": file_path}

    empty_pages = [p["page"] for p in pages if not p["text"].strip()]
    if empty_pages:
        logger.warning(
            "read_pdf: %d dari %d halaman tanpa teks terbaca: %s",
            len(empty_pages), len(pages), empty_pages,
        )

    full_text = "\n\n".join(f"=== HALAMAN {p['page']} ===\n{p['text']}" for p in pages)
    creation_date = _parse_pdf_date(raw_meta.get("creationDate"))
    mod_date = _parse_pdf_date(raw_meta.get("modDate"))
    was_modified, modification_gap_days = _compute_modification_info(creation_date, mod_date)

    metadata = {
        "success": True,
        "title": raw_meta.get("title", ""),
        "author": raw_meta.get("author", ""),
        "creator": raw_meta.get("creator", ""),
        "producer": raw_meta.get("producer", ""),
        "creation_date": creation_date,
        "modification_date": mod_date,
        "was_modified": was_modified,
        "modification_gap_days": modification_gap_days,
    }
    return {
        "success": True,
        "file_path": file_path,
        "total_pages": len(pages),
        "page_count": page_count,
        "full_text": full_text,
        "pages": pages,
        "metadata": metadata,
    }


read_document = read_pdf
