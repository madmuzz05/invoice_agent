from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.utils.merge_documents import merge_document_results
from baca_invoice.utils.segments import format_pages_for_segmenter, parse_segments
from web.services.result_validator import validate_agent_result


def _doc(**fields):
    return TravelDocumentResult(**fields).model_dump()


# ── Segmentasi halaman ────────────────────────────────────────────────────────


def test_parse_segments_valid_plan_sorted_by_first_page():
    plan = {"documents": [{"pages": [3]}, {"pages": [2, 1]}]}
    assert parse_segments(plan, [1, 2, 3]) == [[1, 2], [3]]


def test_parse_segments_rejects_invalid_plans():
    pages = [1, 2, 3]
    assert parse_segments({"pages": [3]}, pages) is None  # objek dalam saja
    assert parse_segments({"documents": [{"pages": [1, 2]}]}, pages) is None  # hal. 3 hilang
    assert parse_segments({"documents": [{"pages": [1, 2]}, {"pages": [2, 3]}]}, pages) is None
    assert parse_segments({"documents": [{"pages": [1, 2, 3, 4]}]}, pages) is None
    assert parse_segments({"documents": [{"pages": ["x"]}]}, [1]) is None
    assert parse_segments({"raw": "bukan json"}, pages) is None


def test_format_pages_for_segmenter_truncates_long_pages():
    text = format_pages_for_segmenter([{"page": 1, "text": "A" * 5000 + "TOTAL"}])
    assert text.startswith("=== HALAMAN 1 ===")
    assert "[...]" in text and text.endswith("TOTAL")


# ── Penggabungan hasil per dokumen ────────────────────────────────────────────


def _hotel(number, name, check_in, total, doc_type="receipt"):
    fields = {"receipt_number": number} if doc_type == "receipt" else {"invoice_number": number}
    return _doc(
        doc_type=doc_type, document_subtype="hotel", detected_subtypes=["hotel"],
        hotel_name=name, check_in_date=check_in, total_payment=total, **fields,
    )


def test_merge_three_hotel_documents_sums_everything():
    merged = merge_document_results([
        _hotel("#1880000000000000001", "Aston Gresik", "2026-11-16", 1062145),
        _hotel("RSN00000001", "Aston Gresik", "2026-11-17", 1256000, doc_type="invoice"),
        _hotel("#1880000000000000002", "INNSIDE by Melia Yogyakarta", "2026-11-19", 1574944),
    ])
    result = validate_agent_result(merged)
    assert result["doc_type"] == "receipt"
    assert result["document_subtype"] == "hotel"
    assert result["document_count"] == 3
    assert result["total_payment"] == 3893089
    assert [item["price"] for item in result["items_purchased"]] == [1062145, 1256000, 1574944]
    assert result["receipt_number"] == (
        "#1880000000000000001, RSN00000001, #1880000000000000002"
    )
    assert result["hotel_name"] == "Aston Gresik"
    assert "3 dokumen hotel" in result["summary"]
    assert "Total Rp 3.893.089" in result["summary"]


def test_merge_ignores_non_transaction_pages():
    tnc = _doc(doc_type="unknown", document_subtype="unknown")
    hotel = _hotel("R1", "Aston", "2026-11-16", 1000000)
    assert merge_document_results([hotel, tnc]) == hotel


def test_merge_different_subtypes_becomes_mixed():
    flight = _doc(
        doc_type="receipt", document_subtype="flight", detected_subtypes=["flight"],
        airline="Garuda", total_payment=1250000,
    )
    merged = merge_document_results([_hotel("R1", "Aston", "2026-11-16", 1000000), flight])
    result = validate_agent_result(merged)
    assert result["document_subtype"] == "mixed"
    assert result["detected_subtypes"] == ["hotel", "flight"]
    assert result["total_payment"] == 0.0


def test_merge_fuel_receipts_sums_liters():
    def fuel(total, liters):
        return _doc(
            doc_type="receipt", document_subtype="fuel", detected_subtypes=["fuel"],
            merchant_name="SPBU", fuel_type="PERTALITE", total_payment=total,
            fuel_volume_liters=liters, fuel_price_per_liter=10000,
        )

    result = validate_agent_result(merge_document_results([fuel(300000, 30), fuel(320000, 32)]))
    assert result["total_payment"] == 620000
    assert result["fuel_volume_liters"] == 62
    assert result["fuel_price_per_liter"] == 10000
    assert [item["quantity"] for item in result["items_purchased"]] == [30, 32]


def test_merge_separate_flight_tickets_detects_round_trip():
    def ticket(origin, dest, date, total):
        return _doc(
            doc_type="receipt", document_subtype="flight", detected_subtypes=["flight"],
            airline="Garuda", route_from=origin, route_to=dest, flight_date=date,
            trip_type="one_way", total_payment=total,
        )

    result = validate_agent_result(merge_document_results([
        ticket("SUB", "CGK", "2026-11-16", 1200000),
        ticket("CGK", "SUB", "2026-11-19", 1100000),
    ]))
    assert result["trip_type"] == "round_trip"
    assert result["return_date"] == "2026-11-19"
    assert result["total_payment"] == 2300000


def test_merge_keeps_inner_documents_of_a_segment():
    toll = _doc(
        doc_type="receipt", document_subtype="toll", detected_subtypes=["toll"],
        document_count=2, total_payment=341500,
        items_purchased=[
            {"description": "Gerbang A", "quantity": 1, "price": 263000},
            {"description": "Gerbang B", "quantity": 1, "price": 78500},
        ],
    )
    single = _doc(
        doc_type="receipt", document_subtype="toll", detected_subtypes=["toll"],
        toll_gate="Tegal", total_payment=228000,
    )
    result = validate_agent_result(merge_document_results([toll, single]))
    assert result["document_count"] == 3
    assert result["total_payment"] == 569500
