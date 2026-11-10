from app import matching as mt

HDFC = """HDFC BANK Ltd.
Account No : 50100XXXXXXX
Statement From : 01/11/2026 To : 10/11/2026

Date,Narration,Chq./Ref.No.,Value Dt,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/11/26,NEFT CR-INSURER C-ICL/CLM/88123-HDFCR52026110512345678,HDFCR52026110512345678,05/11/26,,"77,850.00","25,77,850.00"
06/11/26,NEFT DR-PHARMA SUPPLIER,N123456,06/11/26,"84,000.00",,"24,93,850.00"
"""
ICICI = """Transaction Date,Transaction Remarks,Amount,Cr/Dr,Balance
07-Nov-2026,NEFT/ICICR42026110700011122/TPA TWO/BULK SETTLEMENT,"1,20,000.00",CR,"30,00,000.00"
07-Nov-2026,ATM WDL,"5,000.00",DR,"29,95,000.00"
"""
SBI = """Txn Date,Value Date,Description,Ref No./Cheque No.,Debit,Credit,Balance
08 Nov 2026,08 Nov 2026,BY TRANSFER-NEFT*CGHS PAO*SBIN126312000123,SBIN126312000123,,"43,200.00","12,43,200.00"
"""
PAYERS = [{"id": "c", "name": "Insurer C", "via_tpa": "TPA Two"}, {"id": "g", "name": "CGHS", "via_tpa": None},
          {"id": "a", "name": "Insurer A", "via_tpa": "TPA One"}]


def test_parse_hdfc_skips_header_block_and_debits():
    p = mt.parse_statement(HDFC)
    assert p.rows == 2 and p.skipped == 1 and len(p.credits) == 1
    c = p.credits[0]
    assert c["amount"] == 77850.0 and c["utr"] == "HDFCR52026110512345678" and str(c["value_date"]) == "2026-11-05"


def test_parse_icici_amount_with_crdr_column():
    p = mt.parse_statement(ICICI)
    assert [c["amount"] for c in p.credits] == [120000.0]
    assert p.credits[0]["utr"] == "ICICR42026110700011122"


def test_parse_sbi():
    p = mt.parse_statement(SBI)
    assert p.credits[0]["amount"] == 43200.0 and p.credits[0]["utr"] == "SBIN126312000123"


def test_unreadable():
    assert mt.parse_statement("foo,bar\n1,2").problems


def test_detect_payer_by_name_or_tpa():
    assert mt.detect_payer("NEFT CR-INSURER C-ICL/CLM", PAYERS) == "c"
    assert mt.detect_payer("NEFT/X/TPA TWO/BULK", PAYERS) == "c"
    assert mt.detect_payer("BY TRANSFER-NEFT*CGHS PAO", PAYERS) == "g"
    assert mt.detect_payer("UPI CR WALK-IN", PAYERS) is None


CASES = [
    {"id": "1", "case_number": "AX-1", "payer_id": "c", "payer_ref": "ICL/CLM/88123", "expected": 77850, "settled": 86500},
    {"id": "2", "case_number": "AX-2", "payer_id": "c", "payer_ref": None, "expected": 45000, "settled": 50000},
    {"id": "3", "case_number": "AX-3", "payer_id": "c", "payer_ref": None, "expected": 75000, "settled": 83333},
    {"id": "4", "case_number": "AX-4", "payer_id": "g", "payer_ref": None, "expected": 43200, "settled": 48000},
    {"id": "5", "case_number": "AX-5", "payer_id": "a", "payer_ref": "IA-55-0001", "expected": 30000, "settled": 33333},
]


def test_ladder():
    credits = [
        {"id": "k1", "amount": 77850, "narration": "NEFT CR-INSURER C-ICL/CLM/88123-HDFCR5", "payer_id": "c"},   # exact
        {"id": "k2", "amount": 43200, "narration": "BY TRANSFER-NEFT*CGHS PAO*SBIN1", "payer_id": "g"},          # likely
        {"id": "k3", "amount": 120000, "narration": "NEFT/ICICR4/TPA TWO/BULK SETTLEMENT", "payer_id": "c"},     # split 2+3
        {"id": "k4", "amount": 29100, "narration": "NEFT CR INSURER A IA-55-0001", "payer_id": "a"},              # exact, short
        {"id": "k5", "amount": 15000, "narration": "UPI CR WALK-IN", "payer_id": None},                            # no clue
    ]
    p = {x["credit_id"]: x for x in mt.propose(credits, CASES)}
    assert p["k1"]["confidence"] == "exact" and p["k1"]["lines"][0]["case_id"] == "1"
    assert p["k2"]["confidence"] == "likely" and p["k2"]["lines"][0]["case_id"] == "4"
    assert p["k3"]["confidence"] == "split" and sorted(l["case_id"] for l in p["k3"]["lines"]) == ["2", "3"]
    assert p["k4"]["confidence"] == "exact" and p["k4"]["shortfall"] == 900
    assert "k5" not in p


def test_ambiguous_amount_is_not_guessed():
    cases = [dict(CASES[1]), dict(CASES[1], id="9", case_number="AX-9")]
    assert mt.propose([{"id": "k", "amount": 45000, "narration": "NEFT INSURER C", "payer_id": "c"}], cases) == []


def test_split_only_when_one_combination_fits():
    cases = [{"id": str(i), "case_number": f"AX-{i}", "payer_id": "c", "payer_ref": None, "expected": v, "settled": v}
             for i, v in enumerate([10000, 20000, 30000, 40000])]
    # 50000 = 10k+40k = 20k+30k -> two combinations -> no proposal
    assert mt.propose([{"id": "k", "amount": 50000, "narration": "BULK", "payer_id": "c"}], cases) == []
    # 90000 = 20k+30k+40k only
    p = mt.propose([{"id": "k", "amount": 90000, "narration": "BULK", "payer_id": "c"}], cases)
    assert p and sorted(l["case_id"] for l in p[0]["lines"]) == ["1", "2", "3"]


def test_nhcx_utr_is_certain():
    cases = [dict(CASES[1], nhcx_utr="UTR000123456789")]
    p = mt.propose([{"id": "k", "amount": 45000, "narration": "NEFT CR INSURER C", "utr": "UTR000123456789", "payer_id": None}], cases)
    assert p[0]["confidence"] == "exact" and "NHCX payment notice" in p[0]["why"]


def test_no_tds_deducted_still_likely():
    p = mt.propose([{"id": "k", "amount": 48000, "narration": "CGHS", "payer_id": "g"}], CASES)
    assert p[0]["confidence"] == "likely" and "did not deduct TDS" in p[0]["why"]
