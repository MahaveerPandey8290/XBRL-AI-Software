"""MCA's official C&I business rules (schema/business_rules_AS.json). Run with: python -m pytest -q"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xbrl_aoc4.business_rules import load_rules  # noqa: E402
from xbrl_aoc4.validator import validate  # noqa: E402
from test_generator import make_filing  # noqa: E402


def br(res, code=None):
    return [i for i in res.items if i.get("source") == "MCA_BR" and (code is None or i["code"] == code)]


def by_name(name):
    return next(e for e in load_rules("AS")["element_rules"] if e["name"] == name)


def test_workbook_converted():
    d = load_rules("AS")
    assert d["source"].startswith("Final_Business_Rule_CI_Taxonomy_2016")
    assert len(d["element_rules"]) > 500 and len(d["mandatory_line_items"]) == 44 and len(d["generic"]) == 17
    assert {"k": "nonneg"} in by_name("LongTermBorrowings")["checks"]
    assert {"k": "mandatory"} in by_name("LongTermBorrowings")["checks"]
    assert {"k": "mandatory_if_start_after", "date": "2018-04-01"} in by_name("NICCodeOfProductOrService")["checks"]
    dep = by_name("AmountRaisedFromPublicOfferingDuringYear")["checks"]
    assert {"k": "if_value", "target": "in-ca_WhetherMoneyRaisedFromPublicOfferingDuringYear", "value": "true"} in dep
    t = by_name("ClassificationOfBorrowingsTable")["checks"]
    assert t and t[0]["k"] == "table_if_amount" and "in-gaap_LongTermBorrowings" in t[0]["targets"]
    assert "INDIA" in d["countries"] and "AFGHANISTAN" in d["countries"]


def test_negative_amount_and_dependency():
    f = make_filing("AS")
    f.set_value("in-gaap_LongTermBorrowings", "CY", "-5")
    f.set_value("in-ca_WhetherMoneyRaisedFromPublicOfferingDuringYear", "CY", "true")
    res = validate(f)
    assert any(i["code"] == "NONNEG" and i["element"] == "in-gaap_LongTermBorrowings" for i in res.errors)
    assert any(i["element"] == "in-ca_AmountRaisedFromPublicOfferingDuringYear" and i["code"] == "DEPENDENCY" for i in res.errors)


def test_table_rules():
    f = make_filing("AS")
    T = "in-gaap_DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable"
    # members 1 and 3 without 2; a country MCA doesn't know
    for n in (1, 3):
        f.table_row(T, {"in-gaap_ClassesOfShareCapitalAxis": "in-gaap_EquityShares1Member",
                        "in-gaap_NameOfShareholderAxis": f"in-gaap_Shareholder{n}Member"})["values"].update({
            "in-gaap_NameOfShareholder": {"CY": f"Holder {n}"}, "in-gaap_NumberOfSharesHeldInCompany": {"CY": "10"},
            "in-ca_CountryOfIncorporationOrResidenceOfShareholder": {"CY": "Atlantis"}})
    res = validate(f)
    assert br(res, "MCA_SEQUENCE"), res.errors
    assert any(i["element"] == "in-ca_CountryOfIncorporationOrResidenceOfShareholder" for i in br(res, "COUNTRY"))


def test_generic_rules():
    f = make_filing("AS")
    f.set_value("in-gaap_TradePayables", "CY", "80000.125")                 # rule 8: 2 decimals
    f.set_value("in-ca_DateOfBoardMeetingWhenFinalAccountsWereApproved", "PY", "2025-05-01")  # rule 12: no PY in 400100
    f.set_value("in-gaap_OtherCurrentLiabilities", "CY", None)              # rule 6: PY without CY
    f.values["in-gaap_OtherCurrentLiabilities"] = {"PY": "10"}
    res = validate(f)
    codes = {i["code"] for i in br(res)}
    assert {"MCA_DECIMALS", "MCA_SCOPE", "MCA_PY"} <= codes, codes
    # a consolidated document cannot carry the directors' report
    f = make_filing("AS")
    f.nature = "Consolidated"
    f.set_value("in-ca_NumberOfMeetingsOfBoard", "CY", "4")
    assert any(i["element"] == "in-ca_NumberOfMeetingsOfBoard" for i in br(validate(f), "MCA_SCOPE"))


def test_not_reported_twice():
    f = make_filing("AS")
    f.values.pop("in-gaap_ReservesAndSurplus")
    res = validate(f)
    hits = [i for i in res.errors if i["element"] == "in-gaap_ReservesAndSurplus" and i["code"] == "MANDATORY" and i["period"] == "CY"]
    assert len(hits) == 1, hits
