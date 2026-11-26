from baca_invoice.models.authenticity import DocumentAuthenticity
from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.utils.receipt_authenticity import adjust_authenticity_for_subtype
from web.services.result_validator import validate_agent_result


def _auth(flags, verdict="TIDAK ADA INDIKASI"):
    return DocumentAuthenticity(
        verdict=verdict, confidence_score=0.75, warning_flags=flags, detected_provider="pertamina"
    )


def test_toll_and_fuel_subtypes_and_fields():
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "fuel",
            "fuel_type": "PERTAMAX",
            "fuel_volume_liters": "30.0",
            "vehicle_plate": "B 1234 XYZ",
            "total_payment": 388500,
        }
    )
    assert result["document_subtype"] == "fuel"
    assert result["fuel_volume_liters"] == 30.0
    assert result["vehicle_plate"] == "B 1234 XYZ"
    assert result["trip_type"] == "unknown"

    toll = validate_agent_result(
        {"doc_type": "receipt", "document_subtype": "toll", "toll_gate": "GT Cikampek"}
    )
    assert toll["document_subtype"] == "toll"
    assert TravelDocumentResult().toll_gate == "-"


def test_receipt_photo_flags_become_unverified():
    adjusted = adjust_authenticity_for_subtype(
        _auth(["unknown_provider", "missing_metadata"]), "fuel"
    )
    assert adjusted.verdict == "TIDAK DAPAT DIANALISIS"
    assert adjusted.is_suspicious is False
    assert adjusted.warning_flags == []
    assert adjusted.detected_provider == "pertamina"


def test_editing_evidence_is_kept_for_toll():
    original = _auth(["editing_software_detected", "missing_metadata"], verdict="INDIKASI KUAT")
    assert adjust_authenticity_for_subtype(original, "toll") is original


def test_other_subtypes_unchanged():
    original = _auth(["unknown_provider"])
    assert adjust_authenticity_for_subtype(original, "flight") is original
