"""Build xbrl_aoc4/schema/COST.json from MCA's "Business Rules based on Costing
Taxonomy 2019" workbook (Final_BusinessRules_based_on_CostingTaxonomy2019_V1.1.xlsx).

    python -m tools.build_cost_schema "E:/XBRL SOFTWARE/Taxonomy/Final_BusinessRules_based_on_CostingTaxonomy2019_V1.1.xlsx"

The workbook lists every element of the Cost Audit Report (CRA-4) under its
extended link (ELR), with its label, element name and MCA's rule. It does not
give data types or period types, so those are worked out from the label and
element name here (and replaced by the real ones once the taxonomy has been
downloaded, see taxonomy.py). Axes that have no members listed are typed
dimensions (a running number 1, 2, 3 ... per product, auditor, related party).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "xbrl_aoc4" / "schema" / "COST.json"
PERIODS_OUT = ROOT / "xbrl_aoc4" / "schema" / "periods_COST.json"
P = "in-cost"
ROLE = "http://www.icmai.in/xbrl/taxonomy/role/in-cost/"

# ELRs reported for the current year only (generic rule 14)
CY_ONLY = {"100100", "100200", "100300", "100310", "100410", "100420"}
# sections that hold quantities (not rupees) for their plain numbers
QTY_SECTIONS = {"100330", "100341"}

ENUMS = {
    "SelectSectorForClassifyingManufacturedProductOrService": ["Manufactured Product", "Service"],
    "CategoryOfCostAuditor": ["Individual", "Cost auditors firm"],
    "LevelOfRoundingUsedInCostStatements": ["Actual", "Thousands", "Lakhs", "Millions", "Crores"],
    "ReportingCurrencyOfEntity": ["INR"],
    "NatureOfMaterialConsumed": ["Imported", "Indigenous", "Self manufactured"],
    "BasisAdoptedToDetermineNormalPriceOfRelatedPartyTransaction": [
        "Comparable uncontrolled price method", "Resale price method", "Cost plus method",
        "Profit split method", "Transactional net margin method", "Any other method"],
}
STRING_NAMES = re.compile(
    r"^(NameOf|Address|Email|Description|DescriptionOf|UnitOf|Place|Category|Nature|Country|Basis|"
    r"FirmsRegistration|MembershipNumber|IdentificationNumber|ProductOrServiceCode|CTACode|SubheadingOf|ServiceCode|"
    r"SelectSector|LevelOfRounding|ReportingCurrency|SRNNumber|CorporateIdentity|PermanentAccount|CINOf)")
QTY_WORDS = re.compile(r"(Capacity|Quantity|Production|Produced|Services?Provided|ServiceRendered|ServicesForSale|"
                       r"OwnServices|ContractualArrangement|OutsourcedServices|CaptiveConsumption|Adjustments?|"
                       r"FinishedGoods|Sales?Of(Manufactured|Traded)|ExportSale|DomesticSales|Purchase|Imports|Stock)")


def item_type(name: str, label: str, sec: str) -> str:
    low = label.lower()
    if "[text block]" in low or name.endswith("Explanatory"):
        return "textBlockItemType"
    if name.startswith("Whether"):
        return "booleanItemType"
    if name.startswith("DateOf"):
        return "dateItemType"
    if name in ("CorporateIdentityNumberOrForeignCompanyRegistrationNumber", "CINOfRelatedParty"):
        return "CINNumber"
    if name.startswith("PermanentAccountNumber"):
        return "PANNumber"
    if name == "SRNNumberOfFormCRA2":
        return "SRNNumber"
    if STRING_NAMES.match(name):
        return "stringItemType"
    if name.startswith(("NumberOf", "DurationInMonths")):
        return "integerItemType"
    if "(%)" in label or name == "InHouseCapacityUtilization":
        return "percentItemType"
    if re.search(r"\bratio\b", low) or "(in months)" in low or name == "CurrentAssetsToCurrentLiabilities":
        return "decimalItemType"
    if name.startswith(("AggregateQuantity", "QuantityOf")):
        return "decimalItemType"
    if sec in QTY_SECTIONS and not name.startswith(("Cost", "Amount", "Net", "Revenue")) and QTY_WORDS.search(name):
        return "decimalItemType"
    if sec == "100342" and name in ("ActualServiceProvided", "ActualServiceRendered", "CaptiveConsumption",
                                    "OtherQuantitativeAdjustments"):
        return "decimalItemType"
    if sec == "100340" and name in ("ActualProductionQuantity", "FinishedGoodsPurchased", "ChangeInStockOfFinishedGoods",
                                    "SelfOrCaptiveConsumptionIncludingSamples", "OtherQuantitativeAdjustments",
                                    "StockAndOtherAdjustments", "QuantitySoldOfManufacturedAndTradedProduct"):
        return "decimalItemType"
    return "monetaryItemType"


def _clean(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _rules_for(eid, name, typ, text, sec, in_table) -> list[dict]:
    """Turn MCA's wording into the validator's rule types."""
    out = []
    t = text.lower()

    def add(kind, p1="", p2="", p3=""):
        out.append({"no": str(len(out)), "src": "MCA", "element": eid, "type": kind, "p1": p1, "p2": p2, "p3": p3,
                    "msg": text})

    cond = re.search(r"\b(if|in case)\b", t) and not re.match(
        r"^(this is (a )?mandatory( field)?|it is (a )?mandatory field|mandatory)\s*\.", t)
    m = re.search(r"in case (yes|no) is selected in field '(\w+)'", t) or re.search(
        r"mandatory if \"?(yes|no)\"? is selected in field '(\w+)'", t)
    if m:
        target = re.search(r"'(\w+)'", text[m.start():]).group(1)
        add("MandatoryElement_If_YesNo_InTarget", "true" if m.group(1) == "yes" else "false", f"{P}:{target}")
    elif re.search(r"\bmandatory\b", t) and (not cond or re.search(r"mandatory field\s+in case of current year", t)) and not in_table:
        add("MandatoryCurrentYear")  # previous year: cost.py, when previous year figures are reported
    elif in_table and re.search(r"^(this is )?(a )?mandatory( field)?\.?$|^mandatory\.", t):
        add("MandatoryCurrentYear")
    if "greater than or equal to zero" in t or "greater than equal to zero" in t:
        add("NonNegative")
    if "less than or equal to system date" in t:
        add("Validity_Against_SystemDate")
    if typ == "PANNumber":
        add("ValidPANNumber")
    return out


def build(xlsx: str) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb["Specific rules-CAR"]
    sections, tables, rules = [], {}, []
    sec = table = None
    axis = None
    periods = {}
    seen_table_ids: dict = {}
    for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
        pfx, label, name, rule = (list(row) + [None] * 4)[:4]
        label, name, rule = _clean(label), _clean(name), _clean(rule)
        if _clean(pfx) == "Generic validations" or label == "Generic validations":
            break
        if _clean(pfx) == "ELR":
            m = re.match(r"\[(\w+)\]\s*(.*)", label)
            code, title = m.group(1), label
            sec = {"sheet": code, "code": code, "title": title,
                   "role": ROLE + re.sub(r"[^A-Za-z]", "", m.group(2).title()),
                   "hidden": False, "items": [], "tables": [], "note": rule}
            sections.append(sec)
            table = axis = None
            continue
        if sec is None or not name or _clean(pfx) != P:
            continue
        eid = f"{P}_{name}"
        low = label.lower()
        if low.endswith("[table]"):
            key = eid if eid not in seen_table_ids else f"{eid}@{sec['code']}"
            seen_table_ids[eid] = True
            table = {"sheet": sec["code"], "id": eid, "key": key, "title": label, "role": sec["role"], "axes": [],
                     "items": [], "rule": rule, "section": sec["code"]}
            tables[key] = table
            sec["tables"].append(key)
            axis = None
            continue
        if low.endswith("[axis]"):
            axis = {"id": eid, "label": label, "typedDomain": "", "defaultLabel": "", "default": "", "members": []}
            table["axes"].append(axis)
            continue
        if low.endswith("domain") and name.endswith("Domain") and axis is not None:
            axis["default"] = eid
            axis["defaultLabel"] = label
            continue
        if low.endswith("[member]") and axis is not None:
            axis["members"].append({"id": eid, "label": label, "parent": axis.get("default") or ""})
            continue
        abstract = low.endswith("[abstract]")
        if table is not None and low.startswith("total ") and any(x["id"] == eid for x in table["items"]):
            table = None  # the company-wide total after the per-product columns is reported without a dimension
        typ = "stringItemType" if abstract else item_type(name, label, sec["code"])
        it = {"id": eid, "prefix": P, "name": name, "label": label, "type": typ, "labelRole": "label",
              "row": r}
        if label.lower().startswith("total "):
            it["labelRole"] = "totalLabel"
        if abstract:
            it["abstract"] = True
        if rule:
            it["rule"] = rule
        if name in ENUMS:
            it["enum"] = ENUMS[name]
        if sec["code"] in CY_ONLY:
            it["noPY"] = True
        if table is not None and not abstract and not name.startswith("NotesTo"):
            if all(x["id"] != eid for x in table["items"]):
                table["items"].append(dict(it))
        else:
            if all(x["id"] != eid for x in sec["items"]):
                sec["items"].append(it)
            elif rule:  # a 'Total ...' row repeats an element with the total's rule
                for x in sec["items"]:
                    if x["id"] == eid:
                        x["rule"] = (x.get("rule", "") + " " + rule).strip()
        if not abstract and typ not in ("textBlockItemType",):
            periods[eid] = "instant" if (sec["code"] == "100400" and name in INSTANT_100400) else "duration"
        elif not abstract:
            periods[eid] = "duration"
        if not abstract:
            for ru in _rules_for(eid, name, typ, rule, sec["code"], table is not None):
                ru["no"] = str(len(rules) + 1)
                rules.append(ru)
    # typed dimensions: axes without members
    for t in tables.values():
        for a in t["axes"]:
            if not a["members"]:
                a["typedDomain"] = a["id"][:-4] + "Domain"
                a["default"] = ""
                a["defaultLabel"] = ""
    # mandatory line items per table (sheet 'Mandatory line item CAR')
    ws = wb["Mandatory line item CAR"]
    cur = None
    for row in ws.iter_rows(values_only=True):
        elr, tname, item = (list(row) + [None] * 3)[:3]
        elr, item = _clean(elr), _clean(item)
        if elr.startswith("["):
            code = re.match(r"\[(\w+)\]", elr).group(1)
            cur = next((k for k, t in tables.items() if t["section"] == code), None)
            if cur:
                rules.append({"no": str(len(rules) + 1), "src": "MCA", "element": cur, "type": "TableMandatoryElements",
                              "p1": "", "p2": "", "p3": "", "msg": "Mandatory line items for each column."})
        if cur and item and re.match(r"^[A-Z]\w+$", item):
            rules[-1]["p1"] = ",".join(filter(None, [rules[-1]["p1"], f"{P}:{item}"]))
    # parent members exempt from the parent/child check
    ws = wb["Exempt parent member Dimension"]
    exempt = []
    for row in ws.iter_rows(values_only=True):
        vals = [_clean(v) for v in row]
        if len(vals) >= 4 and vals[3].endswith("Domain"):
            exempt.append(f"{P}_{vals[3]}")
    generic = [_clean(r[1]) for r in wb["Generic Business rules"].iter_rows(values_only=True)
               if isinstance(r[0], (int, float)) and r[1]]
    for t in tables.values():
        t.pop("key", None)
    for sec in sections:
        for it in sec["items"]:
            plus_minus = SUMS.get((sec["code"], it["name"]))
            if plus_minus:
                it["sum"] = {"plus": [f"{P}_{x}" for x in plus_minus[0]], "minus": [f"{P}_{x}" for x in plus_minus[1]]}
    return {"key": "COST", "source": Path(xlsx).name, "sections": sections, "tables": tables, "rules": rules,
            "exempt_parents": exempt, "generic_rules": generic}, periods


# Totals that are the lines above them in the CRA-4 annexure layout (non-dimensional only;
# the per-product cost sheets are checked in cost.py). (section, total): (plus, minus)
SUMS = {
    ("100320", "OperatingIncomesOfCompany"): (["NetOperationalRevenueOfManufacturedProductOrService", "OtherOperatingIncomesOfCompany"], []),
    ("100320", "RevenueAsPerFinancialAccounts"): (["OperatingIncomesOfCompany", "OtherIncomesOfCompany"], []),
    ("100320", "RevenueIncludingExceptionalExtraordinaryAndOtherComprehensiveIncome"): (
        ["RevenueAsPerFinancialAccounts", "ExceptionalAndExtraOrdinaryIncome", "OtherComprehensiveIncome"], []),
    ("100370", "NetRevenueFromOperationsOfCompanyForValueAddition"): (["GrossRevenueFromOperationsOfCompany"], ["TaxesAndOtherDutiesOfCompany"]),
    ("100370", "CostOfBoughtOutInputsOfCompany"): (["CostOfMaterialsConsumedOfCompany", "CostOfProcessMaterialsOrChemicalsOfCompany",
                                                    "CostOfStoresAndSparesConsumedOfCompany", "CostOfUtilitiesOfCompany",
                                                    "CostOfOtherBoughtOutInputsOfCompany"], []),
    ("100370", "ValueAddedOfCompany"): (["NetRevenueFromOperationsOfCompanyForValueAddition", "ExportIncentivesOfCompany",
                                         "AdjustmentsInWorkInProgressAndFinishedStocksOfCompany"], ["CostOfBoughtOutInputsOfCompany"]),
    ("100370", "EarningsAvailableForDistribution"): (["ValueAddedOfCompany", "OtherIncomesOfCompany", "ExceptionalAndExtraOrdinaryIncome",
                                                      "OtherComprehensiveIncome"], []),
    ("100370", "DistributionOfEarnings"): (["ToEmployeesAsSalariesWagesRetirementBenefitsAndOthers", "ToShareholdersAsDividend",
                                            "FundsRetainedByCompany", "ToGovernmentAsTaxes", "ExtraordinaryExpenses",
                                            "OtherDistributionOfEarnings"], []),
    ("100400", "NetCurrentAssets"): (["CurrentAssets"], ["CurrentLiabilities"]),
}

INSTANT_100400 = {"ShareCapital", "ReservesAndSurplus", "LongTermBorrowings", "GrossFixedAssets", "NetFixedAssets",
                  "CurrentAssets", "CurrentLiabilities", "NetCurrentAssets", "CapitalEmployed", "NetWorth"}


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else str(ROOT.parent / "Taxonomy" /
                                                    "Final_BusinessRules_based_on_CostingTaxonomy2019_V1.1.xlsx")
    data, periods = build(src)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    PERIODS_OUT.write_text(json.dumps(periods, indent=0), encoding="utf-8")
    n = sum(len(s["items"]) for s in data["sections"])
    print(f"{OUT.name}: {len(data['sections'])} sections, {n} items, {len(data['tables'])} tables, {len(data['rules'])} rules")
