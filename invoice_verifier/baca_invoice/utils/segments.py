"""Validasi hasil pemetaan halaman → dokumen dari segmenter LLM."""

from __future__ import annotations

from typing import Any

# Batas teks per halaman yang dikirim ke segmenter (hemat token; nomor & total
# dokumen umumnya ada di awal/akhir halaman).
SEGMENT_PAGE_CHAR_LIMIT = 3000


def format_pages_for_segmenter(pages: list[dict[str, Any]]) -> str:
    blocks = []
    for page in pages:
        text = page.get("text") or ""
        if len(text) > SEGMENT_PAGE_CHAR_LIMIT:
            half = SEGMENT_PAGE_CHAR_LIMIT // 2
            text = f"{text[:half]}\n[...]\n{text[-half:]}"
        blocks.append(f"=== HALAMAN {page['page']} ===\n{text.strip()}")
    return "\n\n".join(blocks)


def parse_segments(data: Any, page_numbers: list[int]) -> list[list[int]] | None:
    """Kembalikan daftar grup halaman (urut halaman pertama), atau None jika
    tidak valid: tiap halaman harus muncul tepat sekali dan semua tercakup."""
    documents = data.get("documents") if isinstance(data, dict) else None
    if not isinstance(documents, list) or not documents:
        return None

    expected = set(page_numbers)
    seen: set[int] = set()
    groups: list[list[int]] = []
    for document in documents:
        pages = document.get("pages") if isinstance(document, dict) else None
        if not isinstance(pages, list) or not pages:
            return None
        group: list[int] = []
        for page in pages:
            try:
                number = int(page)
            except (TypeError, ValueError):
                return None
            if number not in expected or number in seen:
                return None
            seen.add(number)
            group.append(number)
        groups.append(sorted(group))

    if seen != expected:
        return None
    return sorted(groups, key=lambda group: group[0])
