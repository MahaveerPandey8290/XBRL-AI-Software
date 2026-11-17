"""CRA-4 (Costing Taxonomy 2019). Run with:  python -m pytest -q tests/test_cost.py"""
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xbrl_aoc4.cost import (AUDITOR_AXIS, AUDITOR_TEXT, PRODUCT_AXIS, T_AUDITOR_REPORT, T_AUDITORS, T_COST_PRODUCT,  # noqa: E402
                            T_PRODUCTS, T_PROFITABILITY, T_QTY_PRODUCT, T_TAXES, fill_header, make_workbook, read_workbook)
from xbrl_aoc4.generator import generate  # noqa: E402
from xbrl_aoc4.model import Filing  # noqa: E402
from xbrl_aoc4.schema_loader import load_schema  # noqa: E402
from xbrl_aoc4.validator import validate  # noqa: E402

P = "in-cost_"
MAT = P + "DetailsOfMaterialConsumedOfManufacturedProductTable"
MAT_AXIS = P + "DetailsOfMaterialConsumedOfManufacturedProductAxis"


def demo() -> Filing:
    f = Filing(taxonomy="COST", cin="L24223MH1946PLC005434", company="Demo Chemicals Limited",
               fy_start="2025-04-01", fy_end="2026-03-31", nature="")
    f.ensure_periods()
    put = lambda n, cy, py=None: (f.set_value(P + n, "CY", str(cy)), py is not None and f.set_value(P + n, "PY", str(py)))  # noqa: E731
    put("AddressOfRegisteredOfficeOrOfPrincipalPlaceOfBusinessInIndiaOfCompany", "1 Main Road, Mumbai 400001")
    put("AddressOfCorporateOfficeOfCompany", "1 Main Road, Mumbai 400001")
    put("EmailAddressOfCompany", "cs@demo.example")
    put("LevelOfRoundingUsedInCostStatements", "Actual")
    put("WhetherIndianAccountingStandardsAreApplicableToTheCompany", "false")
    put("DateOfBoardOfDirectorsMeetingInWhichAnnexureToCostAuditReportWasApproved", "2026-08-10")
    put("WhetherCostAuditorsReportHasBeenQualifiedOrHasAnyReservationsOrContainsAdverseRemarks", "false")
    put("ConsolidatedObservationsOrSuggestionsOfAllCostAuditorsExplanatory", "<p>None.</p>")
    put("WhetherCompanyHasRelatedPartyTransactionsForSaleOrPurchaseOfGoodsOrServices", "false")
    # products: 1 manufactured (covered), 2 other (not covered)
    for m, name, cov, rev in (("1", "Caustic soda", "true", (600, 500)), ("2", "Scrap", "false", (100, 80))):
        row = f.table_row(T_PRODUCTS, {PRODUCT_AXIS: m})
        row["values"] = {P + "SelectSectorForClassifyingManufacturedProductOrService": {"CY": "Manufactured Product"},
                         P + "WhetherPreviousYearFiguresAreReported": {"CY": "true"},
                         P + "NameOfManufacturedProductOrService": {"CY": name},
                         P + "CTACodeOfManufacturedProduct": {"CY": "2815"}, P + "SubheadingOfCTACode": {"CY": "1110"},
                         P + "UnitOfMeasurementForManufacturedProductOrService": {"CY": "MT"},
                         P + "NetOperationalRevenueOfManufacturedProductOrService": {"CY": str(rev[0]), "PY": str(rev[1])},
                         P + "WhetherManufacturedProductOrServiceCoveredUnderCostAudit": {"CY": cov}}
    put("OtherOperatingIncomesOfCompany", 50, 40)
    put("OtherIncomesOfCompany", 10, 5)
    put("TurnoverAsPerExciseOrServiceTaxOrGSTRecords", 760, 625)
    q = f.table_row(T_QTY_PRODUCT, {PRODUCT_AXIS: "1"})
    q["values"] = {P + "NameOfManufacturedProduct": {"CY": "Caustic soda"}, P + "CTACodeOfManufacturedProduct": {"CY": "2815"},
                   P + "SubheadingOfCTACode": {"CY": "1110"}, P + "UnitOfMeasurementForManufacturedProduct": {"CY": "MT"},
                   P + "ActualProductionQuantity": {"CY": "100", "PY": "90"},
                   P + "AvailableQuantityForSaleOfProduct": {"CY": "100", "PY": "90"},
                   P + "QuantitySoldOfManufacturedAndTradedProduct": {"CY": "100", "PY": "90"}}
    c = f.table_row(T_COST_PRODUCT, {PRODUCT_AXIS: "1"})
    c["values"] = {P + "NameOfManufacturedProduct": {"CY": "Caustic soda"}, P + "CTACodeOfManufacturedProduct": {"CY": "2815"},
                   P + "SubheadingOfCTACode": {"CY": "1110"}, P + "UnitOfMeasurementForManufacturedProduct": {"CY": "MT"},
                   P + "ActualProductionQuantity": {"CY": "100", "PY": "90"},
                   P + "QuantitySoldOfManufacturedAndTradedProduct": {"CY": "100", "PY": "90"},
                   P + "CostOfMaterialsConsumed": {"CY": "300", "PY": "250"}}
    for i, (cy, py) in enumerate(((200, 150), (100, 100)), start=1):
        mrow = f.table_row(MAT, {PRODUCT_AXIS: "1", MAT_AXIS: f"{P}MaterialConsumed{i}Member"})
        mrow["values"] = {P + "NameOfManufacturedProduct": {"CY": "Caustic soda"}, P + "DescriptionOfMaterial": {"CY": f"Salt {i}"},
                          P + "NatureOfMaterialConsumed": {"CY": "Indigenous"}, P + "UnitOfMaterialConsumed": {"CY": "MT"},
                          P + "QuantityOfMaterialConsumed": {"CY": "10"}, P + "RateOfMaterialConsumed": {"CY": "5"},
                          P + "CostOfMaterialsConsumed": {"CY": str(cy), "PY": str(py)}}
    pr = f.table_row(T_PROFITABILITY, {PRODUCT_AXIS: "1"})
    pr["values"] = {P + "NameOfManufacturedProductOrService": {"CY": "Caustic soda"}, P + "Sales": {"CY": "600", "PY": "500"},
                    P + "CostOfSales": {"CY": "450", "PY": "400"}, P + "Margin": {"CY": "150", "PY": "100"}}
    put("ProfitLossForAuditedProductOrServices", 150, 100)
    put("ProfitLossAsPerFinancialAccounts", 160, 105)
    put("GrossRevenueFromOperationsOfCompany", 800, 700)
    put("TaxesAndOtherDutiesOfCompany", 50, 80)
    put("CostOfMaterialsConsumedOfCompany", 300, 250)
    put("ToEmployeesAsSalariesWagesRetirementBenefitsAndOthers", 300, 270)
    put("FundsRetainedByCompany", 160, 105)
    for n, cy, py in (("ShareCapital", 100, 100), ("ReservesAndSurplus", 400, 300), ("LongTermBorrowings", 0, 0),
                      ("GrossFixedAssets", 500, 450), ("NetFixedAssets", 300, 280), ("CurrentAssets", 400, 300),
                      ("CurrentLiabilities", 200, 180), ("CapitalEmployed", 500, 400), ("NetWorth", 500, 400),
                      ("NetRevenueFromOperationsOfCompany", 750, 620), ("ProfitBeforeTax", 160, 105)):
        put(n, cy, py)
    a = f.table_row(T_AUDITORS, {AUDITOR_AXIS: "1"})
    a["values"] = {P + k: {"CY": v} for k, v in {
        "WhetherCostAuditorIsLeadAuditor": "true", "CategoryOfCostAuditor": "Cost auditors firm",
        "FirmsRegistrationNumber": "000123", "NameOfCostAuditorOrCostAuditorsFirm": "ABC & Co",
        "PermanentAccountNumberOfCostAuditorOrCostAuditorsFirm": "ABCDE1234F", "AddressOfCostAuditorOrCostAuditorsFirm": "Pune",
        "EmailIDOfCostAuditorOrCostAuditorsFirm": "abc@example.com", "MembershipNumberOfMemberSigningReport": "12345",
        "NameOfMemberSigningReport": "A B C", "NameOfProductOrIndustry": "Caustic soda (2815)", "SRNNumberOfFormCRA2": "R12345678",
        "DateOfSigningCostAuditReportAndAnnexureByCostAuditor": "2026-08-12",
        "PlaceOfSigningCostAuditReportAndAnnexureByCostAuditor": "Pune"}.items()}
    rep = f.table_row(T_AUDITOR_REPORT, {AUDITOR_AXIS: "1"})
    rep["values"] = {P + n: {"CY": "<p>As reported.</p>"} for n in AUDITOR_TEXT}
    t = f.table_row(T_TAXES, {P + "TypesOfIndirectTaxesOfCompanyAxis": P + "CentralGoodsAndServiceTaxMember"})
    t["values"] = {P + "DutiesTaxesPayableByCompany": {"CY": "40"}, P + "DutiesTaxesPaidByCompany": {"CY": "40"},
                   P + "DifferenceBetweenDutiesTaxesPaidAndPayable": {"CY": "0"}, P + "InterestPenaltyFinesPaidByCompany": {"CY": "0"}}
    fill_header(f)
    return f


def test_schema_loads():
    s = load_schema("COST")
    assert len(s.sections) == 23
    assert T_PRODUCTS in s.tables and s.tables[T_PRODUCTS]["axes"][0]["typedDomain"]
    assert s.element(P + "CostOfMaterialsConsumed")["type"] == "monetaryItemType"
    assert s.element(P + "ActualProductionQuantity")["type"] == "decimalItemType"


def test_demo_passes_and_generates():
    f = demo()
    res = validate(f)
    assert res.errors == [], [e["message"] for e in res.errors]
    xml, warns = generate(f)
    from lxml import etree
    root = etree.fromstring(xml.encode())
    facts = [e for e in root if isinstance(e.tag, str) and e.get("contextRef")]
    assert len(facts) > 80
    used = {e.get("contextRef") for e in facts}
    ctxs = {c.get("id") for c in root.iter("{http://www.xbrl.org/2003/instance}context")}
    assert used == ctxs  # every context is used by a fact
    assert b"typedMember" in xml.encode() and "I_Opening" not in xml


def test_rules_catch_mistakes():
    f = demo()
    f.table_row(MAT, {PRODUCT_AXIS: "1", MAT_AXIS: P + "MaterialConsumed1Member"})["values"][P + "CostOfMaterialsConsumed"]["CY"] = "210"
    f.set_value(P + "ProfitBeforeTax", "CY", "999")
    f.table_row(T_QTY_PRODUCT, {PRODUCT_AXIS: "1"})["values"][P + "QuantitySoldOfManufacturedAndTradedProduct"]["CY"] = "95"
    codes = [e["message"] for e in validate(f).errors]
    assert any("add up to 310" in m for m in codes)
    assert any("Profit before tax" in m for m in codes)
    assert any("must equal 'Total available quantity" in m for m in codes)


def test_workbook_round_trip():
    f = demo()
    data = make_workbook(f)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cra4.xlsx"
        p.write_bytes(data)
        g = Filing(taxonomy="COST", cin=f.cin, company=f.company, fy_start=f.fy_start, fy_end=f.fy_end, nature="")
        g.ensure_periods()
        read_workbook(p, g)
    fill_header(g)
    assert validate(g).errors == []
    assert g.tables[MAT][1]["values"][P + "CostOfMaterialsConsumed"] == {"CY": "100", "PY": "100"}


def test_browser_process_cost():
    from xbrl_aoc4.browser import process
    f = demo()
    r = json.loads(process([("cra4.xlsx", make_workbook(f))], json.dumps(
        {"taxonomy": "COST", "cin": f.cin, "company": f.company, "fy_start": f.fy_start, "fy_end": f.fy_end})))
    assert r["taxonomy"] == "COST" and r["errors"] == [], r["errors"][:5]
    assert r["workbook"] and r["filename"].endswith(".xml") and "CRA-4" in r["filename"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
