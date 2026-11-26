"""Forensik struktur PDF digital (tanpa LLM).

Mencari jejak pengeditan di dalam struktur file, bukan hanya metadata:

1. Riwayat revisi (incremental update): setiap simpan inkremental menambah
   `%%EOF`. Revisi lama dipulihkan dan teksnya dibandingkan dengan versi akhir —
   revisi yang hanya menambah tanda tangan/anotasi (teks halaman sama) wajar.
2. Kotak putih yang digambar SETELAH teks dan menutupinya (whiteout), atau
   "tambalan" putih pas di belakang angka (jejak alat redact: teks lama dihapus,
   angka baru ditulis di atas tambalan).
3. Teks lama yang tertimpa teks baru di posisi yang sama (angka berbeda).
4. Angka dengan keluarga font asing (bukan font dominan halaman) — editor PDF
   memakai font lain saat glyph angka baru tidak ada di font asli.

"Save As" menghapus jejak no.1, tapi jejak no.2–4 ada di isi halaman sehingga
tetap terbawa. Dokumen yang dibuat ulang dari nol (mis. inspect element lalu
print to PDF) tidak meninggalkan jejak struktur — tangkap lewat cek isi.
Halaman tanpa teks digital (scan/foto) dilewati.
"""

from __future__ import annotations

import collections
import difflib
import logging
import re
from typing import Any

import fitz

logger = logging.getLogger(__name__)

CONTENT_CHANGED = "content_changed_after_creation"
WHITEOUT = "text_covered_by_white_box"
WHITE_PATCH = "white_patch_behind_amount"
HIDDEN_TEXT = "hidden_text_under_overlay"
FOREIGN_FONT = "foreign_font_in_amount"
FORENSIC_FLAGS = (CONTENT_CHANGED, WHITEOUT, WHITE_PATCH, HIDDEN_TEXT, FOREIGN_FONT)

_NUMBER = re.compile(r"\d[\d.,]{3,}")
_EOF = re.compile(rb"%%EOF[\r\n\s]*")
_MAX_EVIDENCE = 3


def _font_family(name: str) -> str:
    """'ABCDEF+Roboto-BoldItalic' → 'roboto' (abaikan subset & varian bold/italic)."""
    name = name.split("+", 1)[-1]
    name = re.split(r"[-,]", name, maxsplit=1)[0]
    return re.sub(r"(bold|italic|oblique|regular|medium|light|mt|ps)+$", "", name.lower()) or name


def _text_spans(page: fitz.Page) -> list[dict[str, Any]]:
    return [
        span
        for block in page.get_text("dict")["blocks"]
        if block.get("type") == 0
        for line in block["lines"]
        for span in line["spans"]
        if span["text"].strip()
    ]


def _page_content_text(page: fitz.Page) -> str:
    """Teks isi halaman tanpa anotasi/tanda tangan (yang wajar ditambah belakangan)."""
    for annot in list(page.annots() or []):
        page.delete_annot(annot)
    for widget in list(page.widgets() or []):
        page.delete_widget(widget)
    return page.get_text()


def _revision_changes(raw: bytes) -> list[str]:
    """Token teks yang berubah antara revisi pertama dan versi akhir."""
    cuts = [m.end() for m in _EOF.finditer(raw)]
    if raw[:1024].find(b"/Linearized") >= 0 and cuts:
        cuts = cuts[1:]  # %%EOF pertama file linearized bukan revisi
    if len(cuts) < 2:
        return []
    try:
        with fitz.open("pdf", raw[: cuts[0]]) as first, fitz.open("pdf", raw) as last:
            if len(first) != len(last):
                return []
            before = " ".join(_page_content_text(p) for p in first).split()
            after = " ".join(_page_content_text(p) for p in last).split()
    except Exception as exc:  # noqa: BLE001 - revisi lama bisa rusak/tidak lengkap
        logger.debug("forensik: revisi pertama tidak bisa dibuka (%s)", exc)
        return []
    return [
        token
        for token in difflib.unified_diff(before, after, lineterm="", n=0)
        if token[:1] in "+-" and not token.startswith(("+++", "---"))
    ]


def _covered_ratio(char_bbox: Any, rect: fitz.Rect) -> float:
    box = fitz.Rect(char_bbox)
    inter = box & rect
    return 0.0 if inter.is_empty or not box.get_area() else inter.get_area() / box.get_area()


def _white_boxes(page: fitz.Page) -> tuple[list[str], list[str]]:
    """(teks yang ditutup kotak putih, angka di atas tambalan putih pas-ukuran)."""
    traces = page.get_texttrace()
    covered, patched = [], []
    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if not fill or not all(c > 0.97 for c in fill[:3]):
            continue
        rect = drawing["rect"]
        if rect.width >= 0.9 * page.rect.width or rect.is_empty:
            continue  # latar belakang halaman
        seqno = drawing.get("seqno", -1)
        for trace in traces:
            chars = [ch for ch in trace["chars"] if chr(ch[0]).strip()]
            if trace.get("seqno", 0) < seqno:
                # Teks lebih dulu, kotak putih sesudahnya → teks tertutup.
                hidden = "".join(chr(ch[0]) for ch in chars if _covered_ratio(ch[3], rect) > 0.5)
                if hidden:
                    covered.append(hidden)
            else:
                # Angka di atas tambalan putih yang ukurannya pas dengan angka itu.
                text = "".join(chr(ch[0]) for ch in chars)
                box = fitz.Rect(trace["bbox"])
                if (
                    _NUMBER.search(text)
                    and box.get_area()
                    and _covered_ratio(box, rect) > 0.8
                    and rect.get_area() <= 2.0 * box.get_area()
                ):
                    patched.append(text.strip())
    return covered, patched


def _hidden_texts(spans: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Dua angka berbeda di posisi yang sama (yang lama tertimpa)."""
    numbers = [s for s in spans if _NUMBER.search(s["text"])]
    pairs = []
    for i, a in enumerate(numbers):
        for b in numbers[i + 1:]:
            ra, rb = fitz.Rect(a["bbox"]), fitz.Rect(b["bbox"])
            inter = ra & rb
            if (
                a["text"].strip() != b["text"].strip()
                and not inter.is_empty
                and inter.get_area() > 0.6 * min(ra.get_area(), rb.get_area())
            ):
                pairs.append((a["text"].strip(), b["text"].strip()))
    return pairs


def _foreign_fonts(spans: list[dict[str, Any]]) -> list[str]:
    """Angka dengan keluarga font yang jarang dipakai di halaman itu."""
    chars = collections.Counter()
    for span in spans:
        chars[_font_family(span["font"])] += len(span["text"].strip())
    total = sum(chars.values())
    if total < 40:
        return []  # halaman terlalu sedikit teks untuk menilai font dominan
    dominant = chars.most_common(1)[0][0]
    found = []
    for span in spans:
        family = _font_family(span["font"])
        if _NUMBER.search(span["text"]) and family != dominant and chars[family] < 0.2 * total:
            found.append(f"'{span['text'].strip()}' ({span['font']}, font dominan {dominant})")
    return found


def analyze_pdf_structure(file_path: str) -> dict[str, Any]:
    """Hasil forensik: {"flags": [...], "evidence": [...], "revisions": n}.
    Kosong (tanpa flag) untuk file non-PDF atau halaman tanpa teks digital."""
    result: dict[str, Any] = {"flags": [], "evidence": [], "revisions": 0}
    try:
        raw = open(file_path, "rb").read()
        if not raw.startswith(b"%PDF"):
            return result
        result["revisions"] = max(len(_EOF.findall(raw)), 1)
        changes = _revision_changes(raw)
        if changes:
            removed = [t[1:] for t in changes if t.startswith("-")][:_MAX_EVIDENCE]
            added = [t[1:] for t in changes if t.startswith("+")][:_MAX_EVIDENCE]
            result["flags"].append(CONTENT_CHANGED)
            result["evidence"].append(
                "[TEMUAN - ISI DIUBAH SETELAH DIBUAT] Revisi awal file berisi "
                f"{removed or '-'}, versi akhir berisi {added or '-'}."
            )

        whiteouts, patches, hidden, foreign = [], [], [], []
        with fitz.open("pdf", raw) as doc:
            for page in doc:
                spans = _text_spans(page)
                if not spans:
                    continue  # halaman scan/foto
                covered, patched = _white_boxes(page)
                whiteouts += [f"hal {page.number + 1}: '{t}'" for t in covered]
                patches += [f"hal {page.number + 1}: '{t}'" for t in patched]
                hidden += [f"hal {page.number + 1}: {a} / {b}" for a, b in _hidden_texts(spans)]
                foreign += [f"hal {page.number + 1}: {t}" for t in _foreign_fonts(spans)]
        if whiteouts:
            result["flags"].append(WHITEOUT)
            result["evidence"].append(
                "[TEMUAN - TEKS DITUTUP KOTAK PUTIH] Kotak putih digambar di atas teks: "
                + "; ".join(whiteouts[:_MAX_EVIDENCE])
            )
        if patches:
            result["flags"].append(WHITE_PATCH)
            result["evidence"].append(
                "[TEMUAN - TAMBALAN PUTIH DI BELAKANG ANGKA] Angka ditulis di atas kotak "
                "putih seukuran angka (jejak alat redact): " + "; ".join(patches[:_MAX_EVIDENCE])
            )
        if hidden:
            result["flags"].append(HIDDEN_TEXT)
            result["evidence"].append(
                "[TEMUAN - TEKS LAMA TERTIMPA] Dua angka berbeda di posisi yang sama: "
                + "; ".join(hidden[:_MAX_EVIDENCE])
            )
        if foreign:
            result["flags"].append(FOREIGN_FONT)
            result["evidence"].append(
                "[TEMUAN - FONT ANGKA BERBEDA] Angka memakai font yang tidak dipakai "
                "teks lain: " + "; ".join(foreign[:_MAX_EVIDENCE])
            )
    except Exception as exc:  # noqa: BLE001 - forensik tidak boleh menggagalkan job
        logger.warning("forensik struktur PDF gagal: %s", exc)
    return result
