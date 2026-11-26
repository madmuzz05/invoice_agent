from types import SimpleNamespace

from baca_invoice.utils.llm_text import answer_text, extract_json_object
from web.services.agent_io import _parse_json_result

_ANSWER = '{"doc_type": "receipt", "document_subtype": "hotel", "total_payment": 1250000}'


def test_answer_text_skips_thought_parts():
    parts = [
        SimpleNamespace(text="Let me analyze {the} document...", thought=True),
        SimpleNamespace(text=_ANSWER, thought=None),
    ]
    assert answer_text(parts) == _ANSWER


def test_extract_json_object_plain_and_fenced():
    assert extract_json_object(_ANSWER)["doc_type"] == "receipt"
    assert extract_json_object(f"Hasil:\n```json\n{_ANSWER}\n```")["doc_type"] == "receipt"


def test_extract_json_object_takes_last_object_after_reasoning():
    text = 'Reasoning: authenticity {"verdict":"-"} dan {tidak valid}.\nFinal:\n' + _ANSWER
    assert extract_json_object(text)["doc_type"] == "receipt"


def test_extract_json_object_returns_none_without_json():
    assert extract_json_object("tidak ada json di sini") is None
    assert extract_json_object("") is None


def test_parse_json_result_falls_back_to_raw():
    assert _parse_json_result("bukan json") == {"raw": "bukan json"}
    assert _parse_json_result("{'a': True}") == {"raw": "{'a': True}"}
    assert _parse_json_result('{"a": True}') == {"a": True}

