import importlib.util
from pathlib import Path

import pytest
from web.services.result_validator import validate_agent_result

# baca_invoice.tools di-stub oleh conftest; muat modul pre-check langsung dari file.
_MODULE_PATH = (
    Path(__file__).parents[2] / "invoice_verifier" / "baca_invoice" / "tools" / "boarding_pass.py"
)
_spec = importlib.util.spec_from_file_location("boarding_pass_precheck", _MODULE_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
is_obvious_boarding_pass = _module.is_obvious_boarding_pass

_BP_TEXT = (
    "BOARDING PASS\nGaruda Indonesia\nName: SANTOSO/BUDI MR\nFlight GA 402 Date 20NOV26\n"
    "From JAKARTA (CGK) To DENPASAR (DPS)\nBoarding Time 07:10 Gate 12 Seat 23A Zone 3\nSEQ 045"
)
_ETICKET = (
    "E-TICKET / ITINERARY RECEIPT\nGaruda Indonesia\nE-ticket number 126-2345678901\n"
    "GA 402 CGK -> DPS\nPlease show this e-ticket to get your boarding pass. Gate closes 20 min.\n"
    "Total Paid: IDR 1.850.000"
)


@pytest.mark.parametrize(
    "text",
    [
        _BP_TEXT,
        "KARTU NAIK PESAWAT / BOARDING PASS\nPintu 5\nWaktu boarding 06:30\nKursi 12C",
    ],
)
def test_obvious_boarding_pass_detected(text):
    assert is_obvious_boarding_pass(text)


@pytest.mark.parametrize(
    "text",
    [
        _ETICKET,
        "BOARDING PASS\nGaruda Indonesia\nGA 402",  # judul tanpa sinyal → ragu, serahkan LLM
        "Gate 12 Boarding Time 07:10 Seat 23A",  # sinyal tanpa judul → serahkan LLM
        "",
    ],
)
def test_non_obvious_documents_go_to_llm(text):
    assert not is_obvious_boarding_pass(text)


def test_validator_accepts_boarding_pass_and_forces_flight_subtype():
    result = validate_agent_result(
        {"doc_type": "boarding_pass", "document_subtype": "unknown", "summary": "BP"}
    )
    assert result["doc_type"] == "boarding_pass"
    assert result["document_subtype"] == "flight"


def test_validator_keeps_receipt_for_combined_document():
    result = validate_agent_result(
        {"doc_type": "receipt", "document_subtype": "flight", "receipt_number": "1"}
    )
    assert result["doc_type"] == "receipt"
