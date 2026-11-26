from baca_invoice.models.travel_document import TravelDocumentResult
from web.services.result_validator import validate_agent_result


def test_mixed_document_stops_extraction():
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "mixed",
            "detected_subtypes": ["Hotel", "flight", "hotel", "spaceship"],
            # LLM sempat mengisi data → harus dikosongkan.
            "hotel_name": "Hotel Santika",
            "airline": "Garuda",
            "total_payment": 5000000,
            "trip_type": "round_trip",
            "summary": "Paket pesawat + hotel.",
            "authenticity": {"verdict": "AUTENTIK"},
        }
    )
    assert result["doc_type"] == "receipt"
    assert result["document_subtype"] == "mixed"
    assert result["detected_subtypes"] == ["hotel", "flight"]
    assert result["hotel_name"] == "-"
    assert result["airline"] == "-"
    assert result["total_payment"] == 0.0
    assert result["trip_type"] == "unknown"
    assert result["extraction_confidence"] == 0.0
    assert result["requires_manual_review"] is True
    assert "hotel, flight" in result["review_reasons"][0]
    assert result["summary"] == "Paket pesawat + hotel."
    assert result["authenticity"]["verdict"] == "AUTENTIK"


def test_detected_subtypes_accepts_comma_string():
    parsed = TravelDocumentResult.model_validate({"detected_subtypes": "toll, fuel"})
    assert parsed.detected_subtypes == ["toll", "fuel"]


def test_single_subtype_document_not_affected():
    result = validate_agent_result(
        {"doc_type": "receipt", "document_subtype": "hotel", "hotel_name": "Hotel Santika"}
    )
    assert result["document_subtype"] == "hotel"
    assert result["hotel_name"] == "Hotel Santika"


def test_same_subtype_documents_marked_mixed_are_extracted():
    """2 booking hotel berbeda = satu subtype → bukan mixed, data tetap diekstrak."""
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "mixed",
            "detected_subtypes": ["hotel", "hotel"],
            "document_count": 2,
            "hotel_name": "Aston Gresik Hotel & Conference Center",
            "total_payment": 2600000,
        }
    )
    assert result["document_subtype"] == "hotel"
    assert result["detected_subtypes"] == ["hotel"]
    assert result["document_count"] == 2
    assert result["hotel_name"] == "Aston Gresik Hotel & Conference Center"
    assert result["total_payment"] == 2600000


def test_different_subtypes_become_mixed_even_if_llm_picks_one():
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "hotel",
            "detected_subtypes": ["hotel", "flight"],
            "hotel_name": "Hotel Santika",
            "total_payment": 5000000,
        }
    )
    assert result["document_subtype"] == "mixed"
    assert result["hotel_name"] == "-"
    assert result["total_payment"] == 0.0


def test_resolve_subtype_rules():
    from baca_invoice.utils.mixed_subtype import resolve_subtype

    assert resolve_subtype("mixed", []) == "unknown"
    assert resolve_subtype("flight", ["flight"]) == "flight"
    assert resolve_subtype("toll", ["fuel", "toll"]) == "mixed"
