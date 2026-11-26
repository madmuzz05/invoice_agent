from baca_invoice.models.travel_document import TravelDocumentResult
from baca_invoice.utils.merge_documents import merge_document_results
from baca_invoice.utils.toll_check import (
    REVIEW_MARKER,
    check_toll_amounts,
    needs_toll_review,
    toll_amounts,
)

# Teks OCR asli struk e-toll (salah baca OCR dibiarkan apa adanya).
BATANG = (
    "BATANG\n21/04/2026\n15:42:04\nSeri:123459\nAsal Gerbang\n:\n08\n(TEGAL]\n"
    "ae TOLL WANDIRI 78500\nCN: 6632000000001234\nSisa\nSald\no\nRp.222303"
)
PASTEUR = "Gol-1 e-Tol] BCA 48500\nCN:0145000000005678 Rp.140900"


def _toll(total, **fields):
    return TravelDocumentResult(
        doc_type="receipt", document_subtype="toll", detected_subtypes=["toll"],
        total_payment=total, **fields,
    )


def test_reads_tariff_and_balance_from_ocr():
    assert toll_amounts(BATANG) == ([78500], [222303])
    assert toll_amounts(PASTEUR) == ([48500], [140900])


def test_single_receipt_balance_is_corrected_to_tariff():
    result = _toll(222303, items_purchased=[{"description": "Tol Batang", "price": 222303}])
    updates = check_toll_amounts(result, BATANG)
    assert updates["total_payment"] == 78500
    assert updates["items_purchased"][0].price == 78500


def test_correct_tariff_is_left_alone():
    assert check_toll_amounts(_toll(78500), BATANG) == {}


def test_package_item_with_balance_is_flagged_for_review():
    result = TravelDocumentResult(
        doc_type="receipt", document_subtype="vehicle_rental",
        detected_subtypes=["vehicle_rental", "toll"],
        items_purchased=[
            {"description": "Sewa", "price": 1100000},
            {"description": "Tol Pasteur", "price": 140900},
        ],
    )
    updates = check_toll_amounts(result, PASTEUR + "\nGol-1 e-Toll BCA 55500")
    flagged = result.model_copy(update=updates)
    assert REVIEW_MARKER in flagged.items_purchased[1].description
    assert needs_toll_review(flagged)


def test_review_marker_survives_merge():
    flagged = _toll(222303, summary=f"Tol {REVIEW_MARKER}").model_dump()
    other = _toll(156000, toll_gate="Kalikangkung").model_dump()
    merged = TravelDocumentResult.model_validate(merge_document_results([flagged, other]))
    assert needs_toll_review(merged)


def test_single_receipt_differing_from_ocr_tariff_is_flagged():
    updates = check_toll_amounts(_toll(228500, summary="Tol Tegal"), "e-Toll MANDIRI 228000")
    flagged = _toll(228500).model_copy(update=updates)
    assert "228000" in flagged.summary
    assert needs_toll_review(flagged)
    merged = TravelDocumentResult.model_validate(
        merge_document_results([flagged.model_dump(), _toll(78500).model_dump()])
    )
    assert needs_toll_review(merged)
