"""Post-processing deterministik untuk TravelDocumentResult.

Modul ini berisi logika perhitungan `extraction_confidence`,
`requires_manual_review`, dan `review_reasons` yang sebelumnya dititipkan ke
LLM. Dipindahkan ke kode supaya hasilnya stabil dan testable terpisah dari
agent. Modul ini tidak bergantung pada ADK dan bisa dipakai ulang di tempat
lain (mis. unit test atau pipeline batch).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from google.adk.models import LlmResponse
from google.genai import types

from ..models.authenticity import DocumentAuthenticity
from ..models.travel_document import TravelDocumentResult
from ..utils.llm_text import answer_text, extract_json_object

logger = logging.getLogger(__name__)

LARGE_PAYMENT_THRESHOLD = 10_000_000
DEFAULT_CURRENCY = "IDR"
EXTRACTOR_STATE_KEY = "document_data"
RAW_AUTHENTICITY_STATE_KEY = "raw_authenticity"
PRECHECK_RESULT_STATE_KEY = "precheck_result"
REVIEW_REASON_BOARDING = "doc_type boarding_pass: bukan bukti transaksi"

# doc_type tanpa data transaksi → tidak ada field yang diekstrak/dinilai.
_NON_TRANSACTION_DOC_TYPES = ("unknown", "boarding_pass")


def capture_tool_authenticity(
    tool: Any,  # noqa: ARG001
    args: dict[str, Any],  # noqa: ARG001
    tool_context: Any,
    tool_response: dict[str, Any],
) -> dict[str, Any] | None:
    """ADK after_tool_callback: simpan raw authenticity dict ke state.

    Dipasang di extractor_agent supaya postprocess punya akses ke output tool
    asli (bukan via re-serialisasi LLM yang bisa drop field). Tidak mengubah
    response tool (return None).
    """
    auth = tool_response.get("authenticity") if isinstance(tool_response, dict) else None
    if isinstance(auth, dict):
        try:
            tool_context.state[RAW_AUTHENTICITY_STATE_KEY] = auth
        except Exception as exc:  # noqa: BLE001
            logger.warning("capture_tool_authenticity: gagal tulis state (%s).", exc)
    return None

_COMMON_FIELDS: tuple[str, ...] = (
    "total_payment",
    "payment_method",
    "payment_date_time",
    "currency",
)

_INVOICE_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "issue_date",
    "vendor_name",
    "buyer_name",
    "payment_terms",
    "line_items",
)

_RECEIPT_FIELDS: tuple[str, ...] = (
    "receipt_number",
    "transaction_date",
    "payment_date",
    "merchant_name",
    "payer_name",
    "payment_status",
    "items_purchased",
)

_HOTEL_FIELDS: tuple[str, ...] = (
    "hotel_name",
    "hotel_city",
    "room_type",
    "check_in_date",
    "check_out_date",
    "total_nights",
)

_FLIGHT_FIELDS: tuple[str, ...] = (
    "airline",
    "route_from",
    "route_to",
    "flight_date",
    "traveler_name",
    "ticket_price",
)

_CRITICAL_FIELDS: tuple[str, ...] = ("total_payment", "payment_date_time")


def _is_filled(value: Any) -> bool:
    """True jika field punya nilai bermakna (bukan default kosong)."""
    if value is None or value == "-" or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value == 0:
        return False
    if isinstance(value, list) and len(value) == 0:
        return False
    return True


def _relevant_fields(result: TravelDocumentResult) -> tuple[str, ...]:
    fields: list[str] = list(_COMMON_FIELDS)
    if result.doc_type == "invoice":
        fields.extend(_INVOICE_FIELDS)
    elif result.doc_type == "receipt":
        fields.extend(_RECEIPT_FIELDS)
    if result.document_subtype == "hotel":
        fields.extend(_HOTEL_FIELDS)
    elif result.document_subtype == "flight":
        fields.extend(_FLIGHT_FIELDS)
    return tuple(dict.fromkeys(fields))


def compute_confidence(result: TravelDocumentResult) -> float:
    """Hitung confidence dari rasio field relevan yang terisi."""
    if result.doc_type in _NON_TRANSACTION_DOC_TYPES:
        return 0.0
    fields = _relevant_fields(result)
    if not fields:
        return 0.0
    filled = sum(1 for f in fields if _is_filled(getattr(result, f, None)))
    ratio = filled / len(fields)
    if ratio > 0.8:
        return 0.85
    if ratio > 0.5:
        return 0.65
    if ratio > 0.2:
        return 0.4
    return 0.2


def compute_review_flags(result: TravelDocumentResult) -> tuple[bool, list[str]]:
    """Tentukan apakah perlu review manual dan alasan-alasannya."""
    reasons: list[str] = []
    if result.doc_type == "unknown":
        reasons.append("doc_type unknown")
    if result.doc_type == "boarding_pass":
        reasons.append(REVIEW_REASON_BOARDING)
    if getattr(result.authenticity, "is_suspicious", False):
        reasons.append("authenticity suspicious")
    if result.total_payment > LARGE_PAYMENT_THRESHOLD:
        reasons.append(f"total_payment > {LARGE_PAYMENT_THRESHOLD:,}")
    if result.doc_type not in _NON_TRANSACTION_DOC_TYPES:
        missing = [f for f in _CRITICAL_FIELDS if not _is_filled(getattr(result, f, None))]
        if missing:
            reasons.append(f"field penting tidak terbaca: {', '.join(missing)}")
    return (bool(reasons), reasons)


def _normalize_currency(value: str | None) -> str:
    """Fallback ke IDR jika kosong/`-`."""
    if not value or value.strip() in ("", "-"):
        return DEFAULT_CURRENCY
    return value


def _extract_authenticity_from_state(state_payload: Any) -> DocumentAuthenticity | None:
    """Ambil DocumentAuthenticity dari output extractor (state['document_data']).

    Extractor menyimpan teks JSON `{"full_text":..., "authenticity": {...}, ...}`.
    Kalau parsing gagal, kembalikan None — caller tetap pakai authenticity dari
    LLM (fallback).
    """
    if state_payload is None:
        return None
    if isinstance(state_payload, str):
        try:
            state_payload = json.loads(state_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(state_payload, dict):
        return None
    auth_raw = state_payload.get("authenticity")
    if not isinstance(auth_raw, dict):
        return None
    try:
        return DocumentAuthenticity.model_validate(auth_raw)
    except ValueError as exc:
        logger.warning("postprocess: gagal validasi authenticity dari state (%s).", exc)
        return None


def apply_postprocessing(
    result: TravelDocumentResult,
    authenticity_override: DocumentAuthenticity | None = None,
) -> TravelDocumentResult:
    """Pure function: terima TravelDocumentResult, kembalikan versi terisi meta.

    Args:
        result: hasil parse JSON output formatter.
        authenticity_override: jika diberikan, timpa `result.authenticity`
            dengan nilai ini (untuk bypass LLM dan pakai output tool langsung).
    """
    updates: dict[str, Any] = {
        "currency": _normalize_currency(result.currency),
    }
    if authenticity_override is not None:
        updates["authenticity"] = authenticity_override
    intermediate = result.model_copy(update=updates)

    confidence = compute_confidence(intermediate)
    requires_review, reasons = compute_review_flags(intermediate)
    return intermediate.model_copy(
        update={
            "extraction_confidence": confidence,
            "requires_manual_review": requires_review,
            "review_reasons": reasons,
        }
    )


def postprocess_llm_response(
    callback_context: Any,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """ADK after_model_callback: terapkan post-processing ke JSON output formatter.

    Mengembalikan LlmResponse baru dengan text JSON yang sudah diisi field meta
    deterministik dan authenticity di-overwrite dari hasil tool (state extractor).
    Jika parsing gagal, kembalikan None agar response asli lewat apa adanya
    (output_schema akan tetap memvalidasi).
    """
    content = llm_response.content
    if content is None or not content.parts:
        return None

    # Abaikan part reasoning (thought) dari model seperti GLM; ambil JSON jawaban.
    payload = extract_json_object(answer_text(content.parts))
    if payload is None:
        logger.warning("postprocess: output formatter tidak berisi JSON; skip.")
        return None
    try:
        result = TravelDocumentResult.model_validate(payload)
    except ValueError as exc:
        logger.warning("postprocess: gagal validasi output formatter (%s); skip.", exc)
        return None

    auth_override: DocumentAuthenticity | None = None
    state = getattr(callback_context, "state", None)
    if state is not None:
        try:
            raw_auth = state.get(RAW_AUTHENTICITY_STATE_KEY)
            if isinstance(raw_auth, dict):
                auth_override = DocumentAuthenticity.model_validate(raw_auth)
            else:
                auth_override = _extract_authenticity_from_state(state.get(EXTRACTOR_STATE_KEY))
        except Exception as exc:  # noqa: BLE001
            logger.warning("postprocess: gagal akses state (%s).", exc)

    processed = apply_postprocessing(result, authenticity_override=auth_override)
    new_text = processed.model_dump_json()
    return LlmResponse(
        content=types.Content(role=content.role, parts=[types.Part(text=new_text)])
    )


def build_boarding_pass_result(authenticity: dict[str, Any]) -> TravelDocumentResult:
    """Hasil final untuk boarding pass yang terdeteksi pre-check (tanpa LLM).

    Authenticity tetap memakai hasil analisis rule-based dari extractor.
    """
    base = TravelDocumentResult(
        doc_type="boarding_pass",
        document_subtype="flight",
        summary=(
            "Dokumen terdeteksi sebagai boarding pass (pre-check otomatis, tanpa LLM). "
            "Boarding pass bukan bukti transaksi; data tidak diekstraksi."
        ),
    )
    try:
        auth = DocumentAuthenticity.model_validate(authenticity or {})
    except ValueError as exc:
        logger.warning("build_boarding_pass_result: authenticity tidak valid (%s).", exc)
        auth = None
    return apply_postprocessing(base, authenticity_override=auth)


def skip_llm_if_prechecked(callback_context: Any) -> types.Content | None:
    """ADK before_agent_callback formatter: lewati LLM jika pre-check sudah
    menghasilkan hasil final (mis. boarding pass). Content yang dikembalikan
    menjadi final response dan mengakhiri invocation."""
    state = getattr(callback_context, "state", None)
    precheck = state.get(PRECHECK_RESULT_STATE_KEY) if state is not None else None
    if not precheck:
        return None
    return types.Content(role="model", parts=[types.Part(text=precheck)])
