"""Kontrak tipe response API.

Mengunci tipe setiap field response (mis. string tidak boleh berubah jadi
array). Jika field sengaja ditambah/diubah, perbarui
`tests/unit/data/response_field_types.json` secara sadar.
"""

import json
from pathlib import Path

import pytest
from baca_invoice.models.travel_document import TravelDocumentResult
from web.models import v1_upload
from web.services.result_validator import validate_agent_result

_SNAPSHOT = json.loads((Path(__file__).parent / "data" / "response_field_types.json").read_text())
_PY_TYPES = {"string": str, "number": float, "integer": int, "boolean": bool}


def _resolve(prop: dict, defs: dict) -> dict:
    if "$ref" in prop:
        return defs[prop["$ref"].split("/")[-1]]
    if "allOf" in prop:
        return defs[prop["allOf"][0]["$ref"].split("/")[-1]]
    return prop


def _field_types(schema: dict, defs: dict) -> dict:
    types = {}
    for name, prop in schema["properties"].items():
        prop = _resolve(prop, defs)
        kind = prop.get("type") or [option.get("type") for option in prop.get("anyOf", [])]
        if kind == "array":
            kind = f"array<{_resolve(prop['items'], defs).get('type')}>"
        types[name] = kind
    return types


def _current_types() -> dict:
    schema = TravelDocumentResult.model_json_schema()
    defs = schema.get("$defs", {})
    types = {
        "TravelDocumentResult": _field_types(schema, defs),
        "DocumentAuthenticity": _field_types(defs["DocumentAuthenticity"], defs),
    }
    for name in ("UploadResponse", "ExtractResponse", "V1ErrorResponse"):
        model_schema = getattr(v1_upload, name).model_json_schema()
        types[name] = _field_types(model_schema, model_schema.get("$defs", {}))
    return types


def test_response_field_types_match_contract():
    current = _current_types()
    for model, expected in _SNAPSHOT.items():
        changed = {
            field: (kind, current[model].get(field, "<dihapus>"))
            for field, kind in expected.items()
            if current[model].get(field, "<dihapus>") != kind
        }
        added = sorted(set(current[model]) - set(expected))
        assert not changed, f"{model}: tipe field berubah/dihapus {changed}"
        assert not added, f"{model}: field baru {added} — perbarui snapshot kontrak"


@pytest.mark.parametrize(
    "llm_output",
    [
        {field: None for field in _SNAPSHOT["TravelDocumentResult"]},
        {"doc_type": "receipt", "document_subtype": "flight", "airline": ["Garuda", "Citilink"]},
        {"doc_type": "receipt", "document_subtype": "fuel", "total_payment": "388500"},
        {"doc_type": "invoice", "line_items": None, "addons": None, "detected_subtypes": None},
        {"doc_type": "receipt", "document_subtype": "mixed", "detected_subtypes": "toll, fuel"},
        {"doc_type": "boarding_pass"},
        {"doc_type": "unknown"},
    ],
)
def test_runtime_values_follow_contract_types(llm_output):
    """Apa pun bentuk output LLM, nilai di response mengikuti tipe kontrak."""
    result = validate_agent_result(dict(llm_output))
    for field, kind in _SNAPSHOT["TravelDocumentResult"].items():
        value = result[field]
        if kind.startswith("array"):
            assert isinstance(value, list), (field, value)
        elif kind == "object" or kind not in _PY_TYPES:
            continue
        else:
            assert isinstance(value, _PY_TYPES[kind]), (field, value)
            if kind in ("number", "integer"):
                assert not isinstance(value, bool), (field, value)
