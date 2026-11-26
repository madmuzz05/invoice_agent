import pytest
from baca_invoice.models.travel_document import TravelDocumentResult
from web.services.result_validator import validate_agent_result


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("round_trip", "round_trip"),
        ("PP", "round_trip"),
        ("Pulang Pergi", "round_trip"),
        ("round-trip", "round_trip"),
        ("one_way", "one_way"),
        ("Sekali Jalan", "one_way"),
        ("multi-city", "multi_city"),
        ("-", "unknown"),
        (None, "unknown"),
        ("aneh", "unknown"),
    ],
)
def test_trip_type_normalized(raw, expected):
    assert TravelDocumentResult.model_validate({"trip_type": raw}).trip_type == expected


def test_trip_type_defaults():
    result = TravelDocumentResult()
    assert result.trip_type == "unknown"
    assert result.return_date == "-"


def test_round_trip_flight_kept():
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "flight",
            "trip_type": "round_trip",
            "flight_date": "2026-11-20",
            "return_date": "2026-11-23",
            "receipt_number": "1",
        }
    )
    assert result["trip_type"] == "round_trip"
    assert result["return_date"] == "2026-11-23"


def test_trip_type_cleared_for_non_transport():
    result = validate_agent_result(
        {
            "doc_type": "receipt",
            "document_subtype": "hotel",
            "trip_type": "round_trip",
            "return_date": "2026-11-23",
            "receipt_number": "1",
        }
    )
    assert result["trip_type"] == "unknown"
    assert result["return_date"] == "-"
