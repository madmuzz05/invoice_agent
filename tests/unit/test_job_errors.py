from web.services.job_errors import classify_error, http_status_for


def _litellm_like(name: str) -> Exception:
    """Buat exception dengan nama kelas sama seperti litellm (tanpa import litellm)."""
    return type(name, (Exception,), {})("boom")


def test_classify_litellm_errors_by_class_name():
    assert classify_error(_litellm_like("NotFoundError")) == "LLM_UNAVAILABLE"
    assert classify_error(_litellm_like("APIConnectionError")) == "LLM_UNAVAILABLE"
    assert classify_error(_litellm_like("AuthenticationError")) == "LLM_AUTH_FAILED"
    assert classify_error(_litellm_like("RateLimitError")) == "LLM_RATE_LIMITED"
    assert classify_error(_litellm_like("Timeout")) == "LLM_TIMEOUT"


def test_classify_timeout_and_invalid_output():
    assert classify_error(TimeoutError()) == "LLM_TIMEOUT"
    assert classify_error(
        ValueError("Agent did not return a valid JSON object matching the expected schema.")
    ) == "INVALID_LLM_OUTPUT"
    assert classify_error(
        ValueError("Agent finished without returning a JSON extraction result.")
    ) == "INVALID_LLM_OUTPUT"


def test_classify_unknown_error_defaults_to_extraction_failed():
    assert classify_error(RuntimeError("x")) == "EXTRACTION_FAILED"
    assert classify_error(ValueError("lain")) == "EXTRACTION_FAILED"


def test_http_status_mapping():
    assert http_status_for("EXTRACTION_FAILED") == 500
    assert http_status_for(None) == 500
    assert http_status_for("UNKNOWN_CODE") == 500
    assert http_status_for("LLM_UNAVAILABLE") == 502
    assert http_status_for("JOB_INTERRUPTED") == 503
    assert http_status_for("LLM_TIMEOUT") == 504
