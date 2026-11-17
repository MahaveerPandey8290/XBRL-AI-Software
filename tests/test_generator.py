"""Run with:  python -m pytest -q   (or python tests/test_generator.py)"""
import glob
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxml import etree  # noqa: E402

from tools.compare_instances import compare  # noqa: E402
from xbrl_aoc4.generator import generate  # noqa: E402
from xbrl_aoc4.importers.instance_reader import read_instance  # noqa: E402
from xbrl_aoc4.model import Filing  # noqa: E402
from xbrl_aoc4.validator import validate  # noqa: E402


def sample_files():
    """Real TaxPrint/InstantXBRL instances: ../Output sample/*.xml or $XBRL_SAMPLES."""
    pats = [os.environ.get("XBRL_SAMPLES", ""), str(ROOT.parent / "Output sample" / "*.xml")]
    out = []
    for p in pats:
        if p:
            out += glob.glob(p)
    return out


def make_filing(tax="AS"):
    f = Filing(taxonomy=tax, cin="U74899DL1993PTC051877", company="Demo Private Limited",
               fy_start="2025-04-01", fy_end="2026-03-31", nature="Standalone")
    f.ensure_periods()
    p = "in-gaap" if tax == "AS" else "ind-as"
    if tax == "AS":
        bs = {"ShareCapital": (100000, 100000), "ReservesAndSurplus": (500000, 400000),
              "LongTermProvisions": (20000, 10000), "TradePayables": (80000, 90000),
              "ShortTermProvisions": (0, 0), "ShareApplicationMoneyPendingAllotment": (0, 0),
              "TangibleAssets": (300000, 250000), "TradeReceivables": (250000, 200000),
              "CashAndBankBalances": (150000, 150000), "Inventories": (0, 0),
              "ShortTermLoansAndAdvances": (0, 0), "LongTermLoansAndAdvances": (0, 0),
              "OtherCurrentLiabilities": (0, 0)}
    else:
        bs = {"EquityShareCapital": (100000, 100000), "OtherEquity": (500000, 400000),
              "PropertyPlantAndEquipment": (600000, 500000), "TradePayablesCurrent": (100000, 100000),
              "TradeReceivablesCurrent": (100000, 100000)}
    for k, (cy, py) in bs.items():
        f.set_value(f"{p}_{k}", "CY", str(cy))
        f.set_value(f"{p}_{k}", "PY", str(py))
    f.set_value("in-ca_NameOfCompany", "CY", f.company)
    f.set_value("in-ca_CorporateIdentityNumber", "CY", f.cin)
    return f


def test_sample_roundtrip():
    files = sample_files()
    if not files:
        print("no sample instances found; skipped")
        return
    for fp in files:
        f, _ = read_instance(fp)
        xml, _ = generate(f)
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8", newline="") as t:
            t.write(xml)
        assert compare(fp, t.name), f"regenerated instance differs from {fp}"
        res = validate(f)
        assert not res.errors, res.errors[:5]


def test_generate_as_with_totals():
    f = make_filing("AS")
    xml, warns = generate(f)
    root = etree.fromstring(xml.encode("utf-8"))
    ns = {"in-gaap": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-gaap"}
    assets = root.findall("in-gaap:Assets", ns)
    assert assets and {a.text for a in assets} == {"700000", "600000"}, [a.text for a in assets]
    sf = root.find("in-gaap:ShareholdersFunds", ns)
    assert sf is not None and sf.get("contextRef").startswith("I_")
    res = validate(f)
    codes = {i["code"] for i in res.errors}
    assert "BALANCE" not in codes and "TOTAL" not in codes, res.errors


def test_validation_catches_errors():
    f = make_filing("AS")
    f.cin = "BADCIN"
    f.set_value("in-gaap_TradePayables", "CY", "12x")
    f.set_value("in-gaap_Assets", "CY", "999")  # wrong total
    f.values.pop("in-gaap_ReservesAndSurplus")
    res = validate(f)
    codes = {i["code"] for i in res.errors}
    assert {"CIN", "TYPE", "TOTAL", "MANDATORY"} <= codes, codes


def test_amount_scaling():
    f = make_filing("AS")
    f.amount_unit = "Lakhs"
    f.values = {"in-gaap_ShareCapital": {"CY": "67.4625"}, "in-gaap_BasicEarningPerEquityShare": {"CY": "12.5"}}
    xml, _ = generate(f)
    assert ">6746250</in-gaap:ShareCapital>" in xml
    assert ">12.5</in-gaap:BasicEarningPerEquityShare>" in xml


def test_indas_generates():
    f = make_filing("INDAS")
    xml, warns = generate(f)
    etree.fromstring(xml.encode("utf-8"))
    assert "ind-as:Assets" in xml and "ind-as:EquityAndLiabilities" in xml


def test_documents_word_and_pdf():
    import io, zipfile, tempfile
    from xbrl_aoc4.importers.docs_reader import classify_document, text_block_for
    from xbrl_aoc4.importers.financials_reader import read_financials
    from xbrl_aoc4.schema_loader import load_schema
    assert classify_document("Directors Report_2025-26.docx") == "directors"
    assert classify_document("Auditors Report.pdf") == "auditors"
    assert classify_document("Annexure - I.docx", "Annual Report on Corporate Social Responsibility") == "csr"
    assert classify_document("Annexure - II.docx", "FORM NO. AOC -2") == "directors"
    assert text_block_for(load_schema("AS"), "directors") == "in-ca_DisclosureInBoardOfDirectorsReportExplanatoryTextBlock"
    assert text_block_for(load_schema("INDAS"), "auditors") == "in-ca_DisclosureInAuditorsReportExplanatory"
    # a balance sheet in a Word table (built by hand: no python-docx needed)
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    def p(t): return f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>"
    def row(*cs): return "<w:tr>" + "".join(f"<w:tc>{p(c)}</w:tc>" for c in cs) + "</w:tr>"
    body = (p("Balance Sheet as at 31st March, 2026") + "<w:tbl>" + row("Particulars", "Note", "31.03.2026", "31.03.2025")
            + row("Share capital", "3", "10,00,000", "10,00,000") + row("Trade payables", "5", "-", "(45,000)") + "</w:tbl>")
    path = Path(tempfile.mkdtemp()) / "bs.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
    rows = {r["caption"]: r for r in read_financials(path, "AS")["rows"]}
    assert rows["Share capital"]["cy"] == 1000000 and rows["Share capital"]["note"] == "3"
    assert rows["Trade payables"]["cy"] == 0 and rows["Trade payables"]["py"] == -45000
    assert rows["Share capital"]["best"] == "in-gaap_ShareCapital"


def test_nested_totals_and_autofill():
    from xbrl_aoc4.autofill import board_report_sections
    from xbrl_aoc4.calc import fill_totals
    from xbrl_aoc4.schema_loader import load_schema
    f = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31")
    f.ensure_periods()
    f.set_value("in-gaap_CurrentTaxPertainingToCurrentYear", "CY", "210")
    f.set_value("in-gaap_DeferredTax", "CY", "-58")
    vals = fill_totals(f, load_schema("AS"))
    # tax expense must include current tax, which is itself a computed sub-total
    assert vals["in-gaap_TaxExpense"]["CY"] == "152"
    secs = board_report_sections("<p>DIRECTOR'S REPORT</p><p>1. FINANCIAL HIGHLIGHTS</p><p>Sales grew.</p>"
                                 "<p>18. SIGNIFICANT AND MATERIAL ORDERS PASSED BY THE REGULATORS OR</p>"
                                 "<p>COURTS OR TRIBUNALS</p><p>None.</p>")
    heads = [h for h, _ in secs]
    assert "FINANCIAL HIGHLIGHTS" in heads
    assert any(h.startswith("SIGNIFICANT AND MATERIAL ORDERS") and h.endswith("TRIBUNALS") for h in heads)


def test_infer_from_document_wording():
    from xbrl_aoc4.infer import Docs, Inferrer
    from xbrl_aoc4.schema_loader import load_schema
    f = Filing(taxonomy="AS", cin="U74899DL1993PTC051877", fy_start="2025-04-01", fy_end="2026-03-31")
    f.ensure_periods()
    schema = load_schema("AS")
    d = Docs()
    d.add("directors", "The Company does not have any Subsidiary, Joint venture or Associate Company.\n"
                       "Place: New Delhi\nDated: 03-09-2026")
    d.add("auditors", "As required by the Companies (Auditor's Report) Order, 2020 we give in the Annexure ... "
                      "the provisions of clause 3(xii) of the Order are not applicable to the Company.")
    src = {}
    Inferrer(f, schema, d, src, address="35-A, Siddharth Chambers, Kalu Sarai, Hauz Khas, New Delhi - 110016").run()
    v = lambda local: f.get_value(next(e for e in schema.elements if e.endswith("_" + local)), "CY")
    assert v("WhetherCompanyHasSubsidiaryCompanies") == "false"
    assert v("WhetherCompaniesAuditorsReportOrderIsApplicableOnCompany") == "true"  # one clause n/a is not the whole Order
    assert v("WhetherMoneyRaisedFromPublicOfferingDuringYear") == "false"
    assert v("DateOfBoardMeetingWhenFinalAccountsWereApproved") == "2026-09-03"
    assert v("PinCodeOfPlaceOfMaintenanceOfComputerServersStoringAccountingData") == "110016"
    assert v("NameOfStateUnionTerritoryOfPlaceOfMaintenanceOfComputerServersStoringAccountingData") == "Delhi"
    assert src, "each inferred value records its evidence"


def test_note_tables_from_workbook():
    """Fixed asset schedule, share capital, reserves and receivables notes become MCA's note tables."""
    from xbrl_aoc4.infer import Docs
    from xbrl_aoc4.schema_loader import load_schema
    from xbrl_aoc4.tables import Tables
    from xbrl_aoc4.validator import validate
    f = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31", cin="U74899DL1993PTC051877")
    f.ensure_periods()
    for local, cy, py in (("TangibleAssets", 1500, 1300), ("ShareCapital", 100000, 100000), ("TradeReceivables", 5000, 4000),
                          ("ReservesAndSurplus", 9000, 8000)):
        f.set_value("in-gaap_" + local, "CY", str(cy))
        f.set_value("in-gaap_" + local, "PY", str(py))
    d = Docs()
    d.sheets = [
        ("wb.xlsx", "Note 10", True, [
            ["NOTE 10 : PROPERTY, PLANT & EQUIPMENT"],
            ["Description", "Gross Block", None, None, None, "Depreciation", None, None, None, "Net Block", None],
            ["", "As at", "Additions", "Deductions", "As at", "As at", "For the year", "Deductions", "As at", "As at", "As at"],
            ["Tangible Assets"],
            ["Motor Cars", 1000, 0, 0, 1000, 200, 100, 0, 300, 700, 800],
            ["Computers", 900, 400, 0, 1300, 400, 100, 0, 500, 800, 500],
            ["Total", 1900, 400, 0, 2300, 600, 200, 0, 800, 1500, 1300],
        ]),
        ("wb.xlsx", "Note 3", True, [
            ["NOTE 3 : SHARE CAPITAL"],
            ["Authorised"],
            ["20,000 (20,000) Equity Shares of Rs.10 each", None, 200000, 200000],
            ["(c) Details of Shareholders holding more than 5% shares"],
            ["Equity Shares"],
            ["Mr. Ravi Kumar", None, 9000, 0.9, 9000, 0.9],
            ["(d) Details of Promoter's shareholding"],
        ]),
        ("wb.xlsx", "Note 4-16", True, [
            ["NOTE 4 : RESERVES AND SURPLUS"],
            ["Surplus in the Statement of Profit and Loss"],
            ["As per Last Balance Sheet", None, 8000, 7000],
            ["Add : Profit for the year", None, 1000, 1000],
            ["", None, 9000, 8000],
        ]),
    ]
    schema = load_schema("AS")
    t = Tables(f, schema, d)
    t.run()
    names = {k.split("_", 1)[1] for k, v in f.tables.items() if v}
    for need in ("DisclosureOfTangibleAssetsTable", "DisclosureOfAdditionalInformationTangibleAssetsTable",
                 "DisclosureOfClassesOfShareCapitalTable", "DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable",
                 "StatementOfChangesInReservesTable", "SubclassificationOfTradeReceivablesTable"):
        assert need in names, need
    cls = {r["dims"].get("in-gaap_ClassesOfTangibleAssetsAxis") for r in f.tables["in-gaap_DisclosureOfTangibleAssetsTable"]}
    assert {"in-gaap_VehiclesMember", "in-gaap_ComputerEquipmentsMember"} <= cls
    share = f.tables["in-gaap_DisclosureOfClassesOfShareCapitalTable"][0]["values"]
    assert share["in-gaap_NumberOfSharesIssued"]["CY"] == "10000" and share["in-gaap_NumberOfSharesAuthorised"]["CY"] == "20000"
    errs = [e for e in validate(f).errors if e["code"].startswith("MCA_SUM")]
    assert not errs, errs

    # MCA's row checks: the share movement and the 'due by directors' lines are filled
    assert share["in-gaap_IncreaseDecreaseInNumberOfSharesOutstanding"]["CY"] == "0"
    assert share["in-gaap_NumberOfSharesOutstanding"]["CY"] == "10000"
    rec = f.tables["in-gaap_SubclassificationOfTradeReceivablesTable"][0]["values"]
    assert rec["in-gaap_TradeReceivablesDueByFirmsOrCompaniesInWhichAnyDirectorIsPartnerOrDirector"] == {"CY": "0", "PY": "0"}
    # with no previous-year schedule, MCA's previous-year movement is reported as missing ...
    codes = [e["message"] for e in validate(f).errors if e["code"] == "MCA_PY"]
    assert any("last year's movement" in m for m in codes), codes
    # ... and last year's filing supplies it
    prior = Filing(taxonomy="AS", fy_start="2024-04-01", fy_end="2025-03-31")
    prior.ensure_periods()
    for r in f.tables["in-gaap_DisclosureOfTangibleAssetsTable"]:
        vals = {}
        for eid, per in r["values"].items():
            if eid.endswith("_TangibleAssets"):
                vals[eid] = {"CY": per["PY"], "PY": str(int(per["PY"]) - 100)}
            elif eid.endswith("_ChangesInTangibleAssets"):
                vals[eid] = {"CY": "100"}
        prior.table_row("in-gaap_DisclosureOfTangibleAssetsTable", dict(r["dims"]))["values"].update(vals)
    f2 = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31", cin="U74899DL1993PTC051877")
    f2.ensure_periods()
    f2.values = {k: dict(v) for k, v in f.values.items()}
    Tables(f2, schema, d, prior=prior).run()
    assert not [e for e in validate(f2).errors if e["code"] == "MCA_PY" and "movement" in e["message"]]
    row = next(r for r in f2.tables["in-gaap_DisclosureOfTangibleAssetsTable"] if len(r["dims"]) == 3)
    st, ch = row["values"]["in-gaap_TangibleAssets"], row["values"]["in-gaap_ChangesInTangibleAssets"]
    assert int(st["OPEN"]) + int(ch["PY"]) == int(st["PY"])


def test_round_to_statements_level():
    """Amounts are rounded to whole lakhs like TaxPrint; totals are re-added and the balance sheet still balances."""
    from xbrl_aoc4.calc import round_to_unit
    from xbrl_aoc4.schema_loader import load_schema
    from xbrl_aoc4.validator import validate
    schema = load_schema("AS")
    f = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31", cin="U74899DL1993PTC051877")
    f.ensure_periods()
    vals = {"ShareCapital": 6746250, "ReservesAndSurplus": 48134567, "ShareholdersFunds": 54880817,
            "TradePayables": 12345678, "OtherCurrentLiabilitiesOthers": 2345678, "OtherCurrentLiabilities": 2345678,
            "CurrentLiabilities": 14691356, "EquityAndLiabilities": 69572173,
            "TradeReceivables": 40123456, "CashAndBankBalances": 29448717, "CurrentAssets": 69572173, "Assets": 69572173}
    for k, v in vals.items():
        f.set_value("in-gaap_" + k, "CY", str(v))
    round_to_unit(f, schema, 100000)
    g = lambda k: int(f.get_value("in-gaap_" + k, "CY"))  # noqa: E731
    assert g("TradeReceivables") == 40100000 and g("ReservesAndSurplus") == 48100000
    assert g("ShareCapital") == 6746250, "share capital stays to the rupee"
    assert g("Assets") == g("EquityAndLiabilities") == 69500000
    assert g("OtherCurrentLiabilitiesOthers") % 100000 != 0, "the share capital difference goes to other liabilities"
    errs = [e for e in validate(f).errors if e["code"] in ("TOTAL", "BALANCE")]
    assert not errs, errs


def test_taxprint_layout_and_footnotes():
    """Plain contexts in TaxPrint's order, table facts in their own LI_ block, footnotes kept."""
    from xbrl_aoc4.importers.instance_reader import read_instance
    f = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31", cin="U74899DL1993PTC051877")
    f.set_value("in-gaap_OtherCurrentLiabilitiesOthers", "CY", "54853750")
    f.set_value("in-gaap_PrescribedCSRExpenditure", "CY", "1736260")
    f.set_value("in-gaap_DisclosureOfAccountingPoliciesExplanatoryTextBlock", "CY", "<p>Policies</p>")
    f.footnotes = {"in-gaap_OtherCurrentLiabilitiesOthers": {"CY": "Cheques issued but not cleared"}}
    f.table_row("in-gaap_DisclosureOfClassesOfShareCapitalTable", {"in-gaap_ClassesOfShareCapitalAxis": "in-gaap_EquitySharesMember"},
                )["values"]["in-gaap_NumberOfSharesIssued"] = {"CY": "674625"}
    xml, _ = generate(f)
    ids = re.findall(r'<context id="([^"]+)"', xml)
    assert ids[:5] == ["I_01042025_31032026", "D_01042025_31032026", "I_01042024_31032025", "D_01042024_31032025", "I_Opening"]
    assert 'id="LI_1"' in xml and xml.index("<!--Tuple End -->") < xml.index('id="LI_1"')
    assert 'decimals="2"   unitRef="INR">1736260<' in xml
    assert "&lt;span /&gt;&lt;p&gt;Policies" in xml
    assert "Cheques issued but not cleared</link:footnote>" in xml
    g, _ = read_instance(xml.encode("utf-8"))
    assert g.footnotes == f.footnotes


def test_zero_fill_like_taxprint():
    """Blank amounts TaxPrint writes as 0 get 0 in both years, but never a head count or a total its lines make non-zero."""
    from xbrl_aoc4.autofill import zero_fill_like_taxprint
    from xbrl_aoc4.schema_loader import load_schema
    schema = load_schema("AS")
    f = Filing(taxonomy="AS", fy_start="2025-04-01", fy_end="2026-03-31", cin="U74899DL1993PTC051877")
    f.ensure_periods()
    f.set_value("in-gaap_TradeReceivables", "CY", "5000")
    f.table_row("in-gaap_StatementOfChangesInReservesTable", {"in-gaap_ComponentsOfReservesAxis": "in-gaap_CapitalRedemptionReservesMember"})
    assert zero_fill_like_taxprint(f, schema)
    assert f.get_value("in-gaap_CashOnHand", "CY") == "0" and f.get_value("in-gaap_CashOnHand", "PY") == "0"
    assert f.get_value("in-gaap_TradeReceivables", "CY") == "5000"
    assert not f.get_value("in-ca_NumberOfEmployeesInTheCompanyAtTheEndOfTheFinancialYear", "CY")
    row = f.tables["in-gaap_StatementOfChangesInReservesTable"][0]["values"]
    assert "in-gaap_DividendAppropriation" not in row, "only items TaxPrint puts on that reserve"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
