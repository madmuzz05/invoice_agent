from baca_invoice.models.authenticity import DocumentAuthenticity
from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.utils.merge_documents import merge_document_results
from baca_invoice.utils.receipt_authenticity import adjust_authenticity_for_subtype
from web.services.result_validator import validate_agent_result


def _rental(**fields):
    base = {
        "doc_type": "receipt", "document_subtype": "vehicle_rental",
        "detected_subtypes": ["vehicle_rental"], "vehicle_type": "Toyota Rush",
        "vehicle_plate": "B 1234 XYZ", "rental_start_date": "2026-11-16",
        "rental_end_date": "2026-11-20", "rental_days": 5, "total_payment": 1500000,
    }
    return TravelDocumentResult(**{**base, **fields}).model_dump()


def test_vehicle_rental_fields_are_kept():
    result = validate_agent_result({
        "doc_type": "receipt", "document_subtype": "vehicle_rental",
        "vehicle_type": "Toyota Rush", "vehicle_plate": "B 1234 XYZ",
        "rental_start_date": "2026-11-16", "rental_end_date": "2026-11-20",
        "rental_days": "5 (lima) hari", "total_payment": "1500000",
        "trip_type": "round_trip",
    })
    assert result["document_subtype"] == "vehicle_rental"
    assert result["rental_days"] == 5
    assert result["total_payment"] == 1500000
    assert result["trip_type"] == "unknown"  # bukan tiket transportasi


def test_statement_and_receipt_of_same_rental_are_not_double_counted():
    statement = _rental(merchant_name="-", summary="Surat keterangan sewa")
    kwitansi = _rental(
        vehicle_plate="B1234XYZ", vehicle_type="-",
        merchant_name="Tour & Travel Padang", payer_name="PT Pupuk Indonesia",
    )
    result = validate_agent_result(merge_document_results([statement, kwitansi]))
    assert result["total_payment"] == 1500000
    assert result["document_count"] == 1
    assert result["vehicle_type"] == "Toyota Rush"
    assert result["merchant_name"] == "Tour & Travel Padang"


def test_different_rentals_are_summed():
    first = _rental()
    second = _rental(vehicle_plate="B 1 ABC", rental_start_date="2026-12-01", total_payment=900000)
    result = validate_agent_result(merge_document_results([first, second]))
    assert result["document_count"] == 2
    assert result["total_payment"] == 2400000


def test_rental_with_hotel_is_mixed():
    hotel = TravelDocumentResult(
        doc_type="receipt", document_subtype="hotel", detected_subtypes=["hotel"],
        hotel_name="Aston", check_in_date="2026-11-16", total_payment=1000000,
    ).model_dump()
    result = validate_agent_result(merge_document_results([_rental(), hotel]))
    assert result["document_subtype"] == "mixed"


def test_rental_receipt_authenticity_not_verified_without_edit_evidence():
    auth = DocumentAuthenticity(verdict="INDIKASI LEMAH", warning_flags=["unknown_provider"])
    adjusted = adjust_authenticity_for_subtype(auth, "vehicle_rental")
    assert adjusted.verdict == "TIDAK DAPAT DIANALISIS"
    assert "sewa kendaraan" in adjusted.analysis_notes


def _struk(subtype, total, **fields):
    return TravelDocumentResult(
        doc_type="receipt", document_subtype=subtype, detected_subtypes=[subtype],
        total_payment=total, **fields,
    ).model_dump()


def test_rental_toll_fuel_package_is_extracted_not_mixed():
    merged = merge_document_results([
        _struk("toll", 27000, toll_gate="Kalihurip 2", receipt_number="123458"),
        _rental(vehicle_type="Toyota Veloz", total_payment=1100000),
        _struk("fuel", 350000, fuel_type="PERTAMAX", fuel_volume_liters=28.46),
        _struk("toll", 48500, toll_gate="Pasteur", receipt_number="105519"),
    ])
    result = validate_agent_result(merged)
    assert result["document_subtype"] == "vehicle_rental"
    assert set(result["detected_subtypes"]) == {"vehicle_rental", "toll", "fuel"}
    assert result["document_count"] == 4
    assert result["total_payment"] == 1525500
    assert result["vehicle_type"] == "Toyota Veloz"
    assert result["fuel_type"] == "PERTAMAX"
    assert result["fuel_volume_liters"] == 28.46
    assert result["toll_gate"] == "Kalihurip 2"


def test_single_call_package_resolves_to_vehicle_rental():
    result = validate_agent_result({
        "doc_type": "receipt", "document_subtype": "mixed",
        "detected_subtypes": ["vehicle_rental", "toll", "fuel"],
        "vehicle_type": "Toyota Veloz", "total_payment": 1525500,
    })
    assert result["document_subtype"] == "vehicle_rental"
    assert result["vehicle_type"] == "Toyota Veloz"
    assert result["total_payment"] == 1525500


def test_toll_and_fuel_without_rental_stay_mixed():
    result = validate_agent_result({
        "doc_type": "receipt", "document_subtype": "toll",
        "detected_subtypes": ["toll", "fuel"], "total_payment": 100000,
    })
    assert result["document_subtype"] == "mixed"


def test_package_items_deduped_by_receipt_serial_and_counted():
    from baca_invoice.utils.multi_receipt import sum_multi_receipts

    result = TravelDocumentResult(
        doc_type="receipt", document_subtype="vehicle_rental",
        detected_subtypes=["vehicle_rental", "toll", "fuel"], document_count=7,
        total_payment=1656500,
        items_purchased=[
            {"description": "Sewa Toyota Veloz", "quantity": 1, "price": 1100000},
            {"description": "Tol Kalihurip 2 Seri 123458", "quantity": 1, "price": 27000},
            {"description": "BBM Pertamax No. Trans 7654321", "quantity": 28.46, "price": 350000},
            {"description": "Tol Pasteur Seri: 123456", "quantity": 1, "price": 48500},
            {"description": "Tol Kalihurip Utama 3 (Seri 123456)", "quantity": 1, "price": 48500},
            {"description": "Tol Kalihurip Utama 3 Seri 123457", "quantity": 1, "price": 55500},
        ],
    )
    updates = sum_multi_receipts(result)
    assert updates["document_count"] == 5
    assert updates["total_payment"] == 1581000
    assert len(updates["items_purchased"]) == 5
