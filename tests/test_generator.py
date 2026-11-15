"""Run with:  python -m pytest -q   (or python tests/test_generator.py)"""
import glob
import os
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
