"""Klasifikasi kegagalan job ekstraksi → error_code + HTTP status.

Dipakai saat job gagal (disimpan ke DB) dan saat GET /extract mengembalikan
status `fail` dengan HTTP status code error yang sesuai.
"""

from __future__ import annotations

EXTRACTION_FAILED = "EXTRACTION_FAILED"
INVALID_LLM_OUTPUT = "INVALID_LLM_OUTPUT"
LLM_TIMEOUT = "LLM_TIMEOUT"
LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
LLM_AUTH_FAILED = "LLM_AUTH_FAILED"
LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
JOB_INTERRUPTED = "JOB_INTERRUPTED"

HTTP_STATUS_BY_CODE: dict[str, int] = {
    EXTRACTION_FAILED: 500,
    INVALID_LLM_OUTPUT: 502,
    LLM_UNAVAILABLE: 502,
    LLM_AUTH_FAILED: 502,
    LLM_RATE_LIMITED: 503,
    JOB_INTERRUPTED: 503,
    LLM_TIMEOUT: 504,
}

# Nama kelas exception litellm (dicek lewat MRO agar tidak perlu import litellm).
_LITELLM_CODES: tuple[tuple[str, str], ...] = (
    ("Timeout", LLM_TIMEOUT),
    ("RateLimitError", LLM_RATE_LIMITED),
    ("AuthenticationError", LLM_AUTH_FAILED),
    ("PermissionDeniedError", LLM_AUTH_FAILED),
    ("NotFoundError", LLM_UNAVAILABLE),
    ("APIConnectionError", LLM_UNAVAILABLE),
    ("ServiceUnavailableError", LLM_UNAVAILABLE),
    ("InternalServerError", LLM_UNAVAILABLE),
    ("BadGatewayError", LLM_UNAVAILABLE),
    ("BadRequestError", LLM_UNAVAILABLE),
    ("APIError", LLM_UNAVAILABLE),
)

_INVALID_OUTPUT_MARKERS = (
    "did not return a valid json",
    "finished without returning a json",
)


def classify_error(exc: BaseException) -> str:
    """Petakan exception job ke error_code stabil."""
    if isinstance(exc, TimeoutError):
        return LLM_TIMEOUT

    class_names = {cls.__name__ for cls in type(exc).__mro__}
    for class_name, code in _LITELLM_CODES:
        if class_name in class_names:
            return code

    message = str(exc).lower()
    if isinstance(exc, ValueError) and any(m in message for m in _INVALID_OUTPUT_MARKERS):
        return INVALID_LLM_OUTPUT
    return EXTRACTION_FAILED


def http_status_for(error_code: str | None) -> int:
    return HTTP_STATUS_BY_CODE.get(error_code or EXTRACTION_FAILED, 500)
