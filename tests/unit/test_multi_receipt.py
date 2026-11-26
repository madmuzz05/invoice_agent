from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.utils.multi_receipt import sum_multi_receipts


def _result(subtype, items, **extra):
    return TravelDocumentResult(
        doc_type="receipt", document_subtype=subtype, items_purchased=items, **extra
    )


def test_toll_receipts_are_summed():
    items = [
        {"description": "21/04 Pejagan", "quantity": 1, "price": 263000},
        {"description": "21/04 Batang", "quantity": 1, "price": 78500},
        {"description": "21/04 Kalikangkung", "quantity": 1, "price": 156000},
        {"description": "23/04 Tegal", "quantity": 1, "price": 228000},
    ]
    updates = sum_multi_receipts(_result("toll", items, total_payment=263000))
    assert updates == {"total_payment": 725500, "document_count": 4}


def test_fuel_receipts_sum_total_and_liters():
    items = [
        {"description": "SPBU 34.xxx PERTALITE", "quantity": 30, "price": 300000},
        {"description": "SPBU 45.5522 PERTALITE", "quantity": 32, "price": 320000},
    ]
    updates = sum_multi_receipts(
        _result("fuel", items, total_payment=300000, subtotal=300000, fuel_volume_liters=30)
    )
    assert updates == {
        "total_payment": 620000, "document_count": 2, "subtotal": 620000, "fuel_volume_liters": 62,
    }


def test_single_receipt_or_other_subtype_unchanged():
    one = [{"description": "PERTALITE", "quantity": 30, "price": 300000}]
    assert sum_multi_receipts(_result("fuel", one, total_payment=300000)) == {}

    hotel_items = [
        {"description": "Deluxe", "quantity": 1, "price": 1000000},
        {"description": "Breakfast", "quantity": 1, "price": 150000},
    ]
    assert sum_multi_receipts(_result("hotel", hotel_items, total_payment=1265000)) == {}


def test_items_without_amount_are_not_summed():
    items = [
        {"description": "Gerbang A", "quantity": 1, "price": 263000},
        {"description": "Gerbang B (nominal tidak terbaca)", "quantity": 1, "price": 0},
    ]
    assert sum_multi_receipts(_result("toll", items, total_payment=263000)) == {}


def test_multiple_hotel_bookings_are_summed():
    items = [
        {"description": "Aston Gresik 2 malam", "quantity": 1, "price": 1500000},
        {"description": "INNSIDE by Melia Yogyakarta 1 malam", "quantity": 1, "price": 1100000},
    ]
    result = _result("hotel", items, document_count=2, total_payment=1500000)
    assert sum_multi_receipts(result) == {"total_payment": 2600000}


def test_multiple_flight_invoices_use_line_items():
    result = TravelDocumentResult(
        doc_type="invoice",
        document_subtype="flight",
        document_count=2,
        line_items=[
            {"description": "CGK-SUB Garuda", "quantity": 1, "subtotal": 1200000},
            {"description": "SUB-CGK Citilink", "quantity": 1, "subtotal": 900000},
        ],
        total_payment=1200000,
    )
    assert sum_multi_receipts(result) == {"total_payment": 2100000}


def test_hotel_items_of_single_booking_are_not_summed():
    items = [
        {"description": "Deluxe", "quantity": 1, "price": 1000000},
        {"description": "Breakfast", "quantity": 1, "price": 150000},
    ]
    assert sum_multi_receipts(_result("hotel", items, total_payment=1265000)) == {}
    # document_count tidak cocok dengan jumlah baris → tidak dijumlah
    result = _result("hotel", items, document_count=3, total_payment=1265000)
    assert sum_multi_receipts(result) == {}


def test_document_count_coercion():
    assert TravelDocumentResult(document_count="2").document_count == 2
    assert TravelDocumentResult(document_count=0).document_count == 1
    assert TravelDocumentResult(document_count="dua").document_count == 1
