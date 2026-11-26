"""Helper untuk membaca jawaban teks LLM secara robust.

Model reasoning (mis. GLM via endpoint OpenAI-compatible) mengembalikan
beberapa part: part "thought" berisi penalaran, lalu part jawaban berisi JSON.
Penalaran juga kadang bocor ke teks jawaban sebelum JSON. Helper di sini
mengabaikan part thought dan mengambil objek JSON terakhir yang valid.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_decoder = json.JSONDecoder()


def answer_text(parts: Iterable[Any] | None) -> str:
    """Gabungkan teks dari part jawaban, abaikan part reasoning (`thought=True`)."""
    texts = [
        part.text
        for part in parts or []
        if getattr(part, "text", None) and not getattr(part, "thought", False)
    ]
    return "\n".join(texts)


def extract_json_object(text: str) -> dict | None:
    """Ambil objek JSON dari teks LLM.

    Urutan: seluruh teks → blok ```json``` terakhir → objek JSON top-level
    terakhir di dalam teks (jawaban final biasanya muncul setelah penalaran).
    Kembalikan None jika tidak ada objek JSON yang valid.
    """
    text = (text or "").strip()
    if not text:
        return None

    candidates = [text, *reversed(_FENCED_JSON.findall(text))]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value

    last: dict | None = None
    pos = text.find("{")
    while pos != -1:
        try:
            value, end = _decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            pos = text.find("{", pos + 1)
            continue
        if isinstance(value, dict):
            last = value
        pos = text.find("{", end)
    return last
