"""Cost Audit Report (Form CRA-4) on the MCA Costing Taxonomy 2019.

* ``check_cost``   - the MCA business rules for the Costing taxonomy that are not
  simple per-element rules (summations across tables, product/service sector,
  previous year figures, cost auditors, related parties ...);
* ``make_workbook`` / ``read_workbook`` - the CRA-4 input workbook: one sheet per
  part of the annexure, downloaded from the page (filled with what is already
  known), completed and uploaded again;
* ``read_cost_report`` - the cost auditor's report (Word / PDF): its paragraphs go
  into the matching text blocks, and the auditor's membership number, firm
  registration number, place and date are picked up;
* ``process_cost`` - the browser entry point for CRA-4 (like browser.process).
"""
from __future__ import annotations

import io
import json
import re
from datetime import date
from decimal import Decimal

from .model import Filing, parse_date

P = "in-cost_"
PRODUCT_AXIS = P + "IdentificationOfManufacturedProductOrServiceAxis"
AUDITOR_AXIS = P + "GeneralDetailsOfCostAuditorAxis"
T_PRODUCTS = P + "ManufacturedProductOrServiceTable"            # [100320]
T_QTY_PRODUCT = T_PRODUCTS + "@100330"
T_COST_PRODUCT = T_PRODUCTS + "@100340"
T_QTY_SERVICE = T_PRODUCTS + "@100341"
T_COST_SERVICE = T_PRODUCTS + "@100342"
T_PROFITABILITY = T_PRODUCTS + "@100350"
T_AUDITOR_REPORT = P + "DetailsOfCostAuditorTable"              # [100100]
T_AUDITORS = T_AUDITOR_REPORT + "@100300"
T_INCOMES = P + "DetailsOfIncomesNotConsideredInCostAccountsTable"
T_EXPENSES = P + "DetailsOfExpensesNotConsideredInCostAccountsTable"
T_RELATED = P + "DescriptionOfRelatedPartyTransactionsTable"
T_TAXES = P + "TypesOfIndirectTaxesOfCompanyTable"
# per-product detail sheets: (table, product cost table, element that must equal their sum)
DETAIL_TABLES = [
    (P + "DetailsOfMaterialConsumedOfManufacturedProductTable", T_COST_PRODUCT, P + "CostOfMaterialsConsumed", True),
    (P + "DetailsOfUtilitiesOfManufacturedProductTable", T_COST_PRODUCT, P + "CostOfUtilitiesConsumed", True),
    (P + "DetailsOfIndustrySpecificElementsOfOperatingExpensesTable", T_COST_PRODUCT, P + "CostOfIndustrySpecificOperatingExpenses", False),
    (P + "DetailsOfMaterialConsumedOfServiceTable", T_COST_SERVICE, P + "CostOfMaterialsConsumed", True),
    (P + "DetailsOfUtilitiesForServiceTable", T_COST_SERVICE, P + "CostOfUtilitiesConsumed", True),
    (P + "DetailsOfIndustrySpecificElementsOfOperatingExpensesTable@100342c", T_COST_SERVICE, P + "CostOfIndustrySpecificOperatingExpenses", False),
]
MANUFACTURED = (T_QTY_PRODUCT, T_COST_PRODUCT) + tuple(d[0] for d in DETAIL_TABLES[:3])
SERVICE = (T_QTY_SERVICE, T_COST_SERVICE) + tuple(d[0] for d in DETAIL_TABLES[3:])
NAME_OF = {T_PRODUCTS: "NameOfManufacturedProductOrService", T_PROFITABILITY: "NameOfManufacturedProductOrService"}
for _t in MANUFACTURED:
    NAME_OF[_t] = "NameOfManufacturedProduct"
for _t in SERVICE:
    NAME_OF[_t] = "NameOfService"
# ELRs reported for both years when previous year figures are reported
PY_SECTIONS = {"100320", "100330", "100340", "100340a", "100340b", "100340c", "100341", "100342", "100342a",
               "100342b", "100342c", "100350", "100360", "100360a", "100360b", "100370", "100400"}
AUDITOR_TEXT = [  # [100100] text blocks, each cost auditor's report
    "DisclosureRegardingAuditOfCostRecordsInConformityWithCostAuditingStandardsExplanatory",
    "DisclosureRelatingToAvailabilityOfInformationAndExplanationForPurposeOfCostAuditExplanatory",
    "DisclosureRelatingToMaintenanceOfCostRecordsAsPerRule5OfTheCompaniesCostRecordsAndAuditRules2014Explanatory",
    "DisclosureRelatingToAvailabilityOfCostRecordsOfBranchesNotVisitedExplanatory",
    "DisclosureRegardingAvailabilityOfInformationAsPerCompaniesAct2013Explanatory",
    "DisclosureRelatingToAdequacyOfInternalAuditOfCostRecordsExplanatory",
    "DisclosureRelatingToTrueAndFairViewOfCostOfProductionOrServiceCostOfSalesMarginAndOtherInformation",
    "DisclosureRelatingToAvailabilityOfAuditedAndCertifiedCostStatementsAndSchedulesForEachUnitAndEachProductOrServiceExplanatory",
    "CostAuditorsObservationsOrSuggestionsExplanatory",
]
YES = {"true", "yes", "y", "1"}


def _num(v):
    from .generator import to_number
    return to_number(v)


def _yes(v) -> bool:
    return str(v or "").strip().lower() in YES


def _no(v) -> bool:
    return str(v or "").strip().lower() in {"false", "no", "n", "0"}


def _empty(v) -> bool:
    return v is None or str(v).strip() == ""


def _member(row, axis=PRODUCT_AXIS):
    return str(row["dims"].get(axis, "")).strip()


# ======================================================================= checks
def check_cost(v) -> None:
    """Costing taxonomy rules beyond the per-element ones (v is a validator.Validator)."""
    f, r, schema = v.f, v.r, v.schema
    rows = lambda tid: f.tables.get(tid, [])  # noqa: E731
    val = lambda row, name, pk="CY": row["values"].get(P + name, {}).get(pk)  # noqa: E731
    lab = v.label
    today = date.today()

    def err(code, msg, name="", sec="", pk="CY", table=""):
        r.add("error", code, msg, P + name if name and not name.startswith(P) else name, sec, pk, table)

    def warn(code, msg, name="", sec="", pk="CY", table=""):
        r.add("warning", code, msg, P + name if name and not name.startswith(P) else name, sec, pk, table)

    # ---------------------------------------------------------- [100200] general information
    cin = v.v(P + "CorporateIdentityNumberOrForeignCompanyRegistrationNumber")
    if cin and f.cin and cin.strip().upper() != f.cin.upper():
        err("CIN", "CIN in general information differs from the filing CIN.", "CorporateIdentityNumberOrForeignCompanyRegistrationNumber", "100200")
    cur = v.v(P + "ReportingCurrencyOfEntity")
    if cur and cur.strip().upper() != "INR":
        err("CURRENCY", "Reporting currency must be INR.", "ReportingCurrencyOfEntity", "100200")
    for name, want in (("DateOfStartOfReportingPeriod", f.fy_start), ("DateOfEndOfReportingPeriod", f.fy_end)):
        got = parse_date(v.v(P + name))
        if got and want and got.isoformat() != want:
            err("PERIOD", f"'{lab(P + name)}' ({got}) must be the same as the filing's financial year ({want}).", name, "100200")
    ps, pe = parse_date(v.v(P + "DateOfStartOfFirstPreviousFinancialYear")), parse_date(v.v(P + "DateOfEndOfFirstPreviousFinancialYear"))
    if bool(ps) != bool(pe):
        err("DEPENDENCY", "Enter both the start and the end of the previous financial year, or neither.",
            "DateOfEndOfFirstPreviousFinancialYear" if ps else "DateOfStartOfFirstPreviousFinancialYear", "100200")
    fs = parse_date(f.fy_start)
    if pe and fs and (fs - pe).days != 1:
        err("PERIOD", "End of the previous financial year must be one day before the start of the reporting period.",
            "DateOfEndOfFirstPreviousFinancialYear", "100200")
    for a, b, name in ((parse_date(f.fy_start), parse_date(f.fy_end), "DurationInMonthsOfReportingPeriod"), (ps, pe, "DurationInMonthsOfPreviousFinancialYear")):
        if a and b:
            months = months_between(a, b)
            if months > 18:
                err("PERIOD", f"'{lab(P + name)}' cannot exceed 18 months.", name, "100200")
            got = _num(v.v(P + name))
            if got is not None and got != months:
                err("PERIOD", f"'{lab(P + name)}' should be {months} (got {got}).", name, "100200")
    board = parse_date(v.v(P + "DateOfBoardOfDirectorsMeetingInWhichAnnexureToCostAuditReportWasApproved"))
    fe = parse_date(f.fy_end)
    if board and fe and board <= fe:
        err("DATE", "Date of the board meeting that approved the annexure must be after the end of the financial year.",
            "DateOfBoardOfDirectorsMeetingInWhichAnnexureToCostAuditReportWasApproved", "100200")

    # ---------------------------------------------------------- [100300] cost auditors
    auditors = [row for row in rows(T_AUDITORS)]
    if not auditors:
        err("TABLE", "Details of cost auditor [100300] is a mandatory table: enter at least one cost auditor.", "", "100300", table=T_AUDITORS)
    members = {str(val(row, "MembershipNumberOfMemberSigningReport") or "").strip().upper() for row in auditors} - {""}
    n_aud = _num(v.v(P + "NumberOfCostAuditorsForReportingPeriod"))
    if n_aud is not None and auditors and len(members) < n_aud:
        err("TABLE", f"Number of cost auditors is {n_aud}, but {len(members)} distinct cost auditor(s) (by membership number) are given in [100300].",
            "NumberOfCostAuditorsForReportingPeriod", "100200")
    srns = {}
    for row in auditors:
        m = _member(row, AUDITOR_AXIS)
        signed = parse_date(val(row, "DateOfSigningCostAuditReportAndAnnexureByCostAuditor"))
        if _empty(val(row, "DateOfSigningCostAuditReportAndAnnexureByCostAuditor")):
            err("MANDATORY", f"Cost auditor {m}: date of signing the cost audit report is mandatory.",
                "DateOfSigningCostAuditReportAndAnnexureByCostAuditor", "T:" + T_AUDITORS, table=T_AUDITORS)
        elif signed and board and signed < board:
            err("DATE", f"Cost auditor {m}: date of signing ({signed}) must be on or after the board meeting date ({board}).",
                "DateOfSigningCostAuditReportAndAnnexureByCostAuditor", "T:" + T_AUDITORS, table=T_AUDITORS)
        elif signed and signed > today:
            err("DATE", f"Cost auditor {m}: date of signing cannot be later than today.",
                "DateOfSigningCostAuditReportAndAnnexureByCostAuditor", "T:" + T_AUDITORS, table=T_AUDITORS)
        mem = str(val(row, "MembershipNumberOfMemberSigningReport") or "").strip()
        if mem and not re.fullmatch(r"[A-Za-z]?\d{3,6}", mem):
            warn("FORMAT", f"Cost auditor {m}: membership number '{mem}' does not look like an ICMAI membership number.",
                 "MembershipNumberOfMemberSigningReport", "T:" + T_AUDITORS, table=T_AUDITORS)
        srn = str(val(row, "SRNNumberOfFormCRA2") or "").strip().upper()
        if srn:
            if srn in srns:
                err("UNIQUE", f"SRN of Form CRA-2 '{srn}' is repeated for cost auditors {srns[srn]} and {m}.",
                    "SRNNumberOfFormCRA2", "T:" + T_AUDITORS, table=T_AUDITORS)
            srns[srn] = m
        for name in ("NumberOfAuditCommitteeMeetingsForWhichCostAuditorWasInvited", "NumberOfAuditCommitteeMeetingAttendedByCostAuditorDuringYear"):
            n = _num(val(row, name))
            if n is not None and n < 0:
                err("NONNEG", f"Cost auditor {m}: '{lab(P + name)}' should be zero or more.", name, "T:" + T_AUDITORS, table=T_AUDITORS)
    if len(auditors) > 1 and sum(_yes(val(row, "WhetherCostAuditorIsLeadAuditor")) for row in auditors) != 1:
        warn("TABLE", "With more than one cost auditor, mark exactly one as the lead auditor.", "WhetherCostAuditorIsLeadAuditor", "100300")

    # ---------------------------------------------------------- [100100] each auditor's report
    if fs and fs > date(2014, 3, 31):
        report = {_member(row, AUDITOR_AXIS): row for row in rows(T_AUDITOR_REPORT)}
        for row in auditors or [{"dims": {AUDITOR_AXIS: "1"}, "values": {}}]:
            m = _member(row, AUDITOR_AXIS)
            rep = report.get(m, {"values": {}})
            missing = [n for n in AUDITOR_TEXT if _empty(rep["values"].get(P + n, {}).get("CY"))]
            for n in missing:
                err("MANDATORY", f"Cost auditor {m}: '{lab(P + n)}' is mandatory (cost audit report, [100100]).", n,
                    "T:" + T_AUDITOR_REPORT, table=T_AUDITOR_REPORT)

    # ---------------------------------------------------------- [100320] products and services
    products = rows(T_PRODUCTS)
    if not products:
        err("TABLE", "Product or service details [100320] is a mandatory table: enter each product or service of the company.",
            "", "100320", table=T_PRODUCTS)
    py_reported = any(_yes(val(row, "WhetherPreviousYearFiguresAreReported")) for row in products)
    covered = {}
    sector = {}
    for row in products:
        m = _member(row)
        sector[m] = str(val(row, "SelectSectorForClassifyingManufacturedProductOrService") or "").strip().lower()
        covered[m] = _yes(val(row, "WhetherManufacturedProductOrServiceCoveredUnderCostAudit"))
        if _no(val(row, "WhetherPreviousYearFiguresAreReported")) and _empty(val(row, "DetailsForNotReportingPreviousYearFiguresExplanatory")):
            err("DEPENDENCY", f"Product/service {m}: give the reason for not reporting previous year figures.",
                "DetailsForNotReportingPreviousYearFiguresExplanatory", "T:" + T_PRODUCTS, table=T_PRODUCTS)
        cta, sub = str(val(row, "CTACodeOfManufacturedProduct") or "").strip(), str(val(row, "SubheadingOfCTACode") or "").strip()
        if sector[m].startswith("manufactured") and covered[m] and not cta:
            err("MANDATORY", f"Product {m}: CTA code is mandatory for a manufactured product covered under cost audit.",
                "CTACodeOfManufacturedProduct", "T:" + T_PRODUCTS, table=T_PRODUCTS)
        if cta and not re.fullmatch(r"\d{4}", cta):
            err("FORMAT", f"Product {m}: CTA code should be the 4 digit heading (got '{cta}').", "CTACodeOfManufacturedProduct",
                "T:" + T_PRODUCTS, table=T_PRODUCTS)
        if cta and not re.fullmatch(r"\d{4}", sub):
            err("FORMAT", f"Product {m}: subheading of CTA code must be 4 digits (CTA code + subheading = 8 digit code).",
                "SubheadingOfCTACode", "T:" + T_PRODUCTS, table=T_PRODUCTS)
        sc, ssub = str(val(row, "ServiceCode") or "").strip(), str(val(row, "SubheadingOfServiceCode") or "").strip()
        if sc and not re.fullmatch(r"\d{6}", sc):
            err("FORMAT", f"Service {m}: service code must be 6 digits (got '{sc}').", "ServiceCode", "T:" + T_PRODUCTS, table=T_PRODUCTS)
        if ssub and not re.fullmatch(r"\d{1,4}", ssub):
            err("FORMAT", f"Service {m}: subheading of service code is at most 4 digits.", "SubheadingOfServiceCode", "T:" + T_PRODUCTS, table=T_PRODUCTS)
        for pk in ("CY",) + (("PY",) if py_reported else ()):
            if _empty(val(row, "NetOperationalRevenueOfManufacturedProductOrService", pk)):
                err("MANDATORY", f"Product/service {m}: net operational revenue is mandatory ({'current' if pk == 'CY' else 'previous'} year).",
                    "NetOperationalRevenueOfManufacturedProductOrService", "T:" + T_PRODUCTS, pk, T_PRODUCTS)
    if products and not any(covered.values()):
        err("TABLE", "Select 'Yes' for 'Whether covered under cost audit' for at least one product or service.",
            "WhetherManufacturedProductOrServiceCoveredUnderCostAudit", "100320", table=T_PRODUCTS)
    for pk in ("CY", "PY"):
        parts = [_num(val(row, "NetOperationalRevenueOfManufacturedProductOrService", pk)) for row in products]
        tot = _num(v.v(P + "NetOperationalRevenueOfManufacturedProductOrService", pk))
        if tot is not None and any(p is not None for p in parts) and sum(p for p in parts if p is not None) != tot:
            err("SUM", f"Total net operational revenue ({tot}) must equal the sum for all products/services "
                f"({sum(p for p in parts if p is not None)}).", "NetOperationalRevenueOfManufacturedProductOrService", "100320", pk)

    # sector: manufactured products have quantitative information and cost statements in 100330/100340,
    # services in 100341/100342, never the other way round
    for m, sec in sector.items():
        want, other = (MANUFACTURED, SERVICE) if sec.startswith("manufactured") else (SERVICE, MANUFACTURED) if sec.startswith("service") else ((), ())
        for tid in other:
            if any(_member(row) == m for row in rows(tid)):
                err("SECTOR", f"Product/service {m} is a {sec}; it must not appear in '{schema.tables[tid]['title']}' "
                    f"[{schema.tables[tid].get('section', '')}].", "", "T:" + tid, table=tid)
        if covered.get(m):
            for tid in want[:2]:
                if not any(_member(row) == m for row in rows(tid)):
                    err("TABLE", f"Product/service {m} is covered under cost audit: fill it in [{schema.tables[tid].get('section', '')}] "
                        f"{schema.tables[tid]['title']}.", "", "T:" + tid, table=tid)
            if not any(_member(row) == m for row in rows(T_PROFITABILITY)):
                err("TABLE", f"Product/service {m} is covered under cost audit: fill its sales, cost of sales and margin in [100350].",
                    "", "T:" + T_PROFITABILITY, table=T_PROFITABILITY)

    # names consistent across the ELRs (generic rule 11)
    names = {}
    for tid, name in NAME_OF.items():
        for row in rows(tid):
            n = str(val(row, name) or "").strip()
            if n:
                names.setdefault(_member(row), {}).setdefault(n.lower(), (n, tid))
    for m, found in names.items():
        if len(found) > 1:
            shown = "; ".join(f"'{n}' in [{schema.tables[t].get('section', '')}]" for n, t in found.values())
            err("NAME", f"Product/service {m} has different names: {shown}. Use the same name everywhere.", "", "")

    # ---------------------------------------------------------- [100330] / [100341] quantities balance
    for tid, a, b in ((T_QTY_PRODUCT, "QuantitySoldOfManufacturedAndTradedProduct", "AvailableQuantityForSaleOfProduct"),
                      (T_QTY_SERVICE, "ActualServiceRendered", "AvailableServicesForSale")):
        for row in rows(tid):
            for pk in ("CY", "PY"):
                x, y = _num(val(row, a, pk)), _num(val(row, b, pk))
                if x is not None and y is not None and x != y:
                    err("SUM", f"Product/service {_member(row)}: '{lab(P + a)}' ({x}) must equal '{lab(P + b)}' ({y}).", a,
                        "T:" + tid, pk, tid)
                for n, q in ((a, x), (b, y)):
                    if q is not None and q < 0:
                        err("NONNEG", f"Product/service {_member(row)}: '{lab(P + n)}' should be zero or more.", n, "T:" + tid, pk, tid)

    # ---------------------------------------------------------- [100340a-c] / [100342a-c] detail sheets
    for tid, cost_tid, cost_el, both_years in DETAIL_TABLES:
        t = schema.tables.get(tid) or {}
        detail_axis = next((a for a in t.get("axes", []) if a["id"] != PRODUCT_AXIS), None)
        by_product: dict = {}
        for row in rows(tid):
            by_product.setdefault(_member(row), []).append(row)
        for crow in rows(cost_tid):
            m = _member(crow)
            for pk in ("CY", "PY") if both_years else ("CY",):
                amt = _num(crow["values"].get(cost_el, {}).get(pk))
                if not amt:
                    continue
                det = by_product.get(m, [])
                if not det:
                    err("TABLE", f"Product/service {m}: '{lab(cost_el)}' is {amt}, so '{t.get('title', tid)}' "
                        f"[{t.get('section', '')}] must be filled ({'current' if pk == 'CY' else 'previous'} year).",
                        cost_el, "T:" + tid, pk, tid)
                    continue
                s = sum((_num(d["values"].get(cost_el, {}).get(pk)) or Decimal(0)) for d in det)
                if s != amt:
                    err("SUM", f"Product/service {m}: the {t.get('title', tid).replace(' [table]', '').lower()} add up to {s}, "
                        f"but '{lab(cost_el)}' in the cost statement is {amt}.", cost_el, "T:" + tid, pk, tid)
        if detail_axis and detail_axis.get("members"):
            order = [mm["id"] for mm in detail_axis["members"]]
            for m, det in by_product.items():
                used = sorted(order.index(d["dims"].get(detail_axis["id"])) for d in det if d["dims"].get(detail_axis["id"]) in order)
                if used and used != list(range(len(used))):
                    err("SEQUENCE", f"Product/service {m}: use the members of '{detail_axis['label']}' in order (1, 2, 3 ...) without gaps.",
                        "", "T:" + tid, table=tid)

    # ---------------------------------------------------------- [100350] / [100360] profitability and reconciliation
    for pk in ("CY", "PY"):
        margins = [_num(val(row, "Margin", pk)) for row in rows(T_PROFITABILITY)]
        audited = _num(v.v(P + "ProfitLossForAuditedProductOrServices", pk))
        if audited is not None and any(x is not None for x in margins) and sum(x for x in margins if x is not None) != audited:
            err("SUM", f"Profit for audited products/services ({audited}) must equal the total margin in [100350] "
                f"({sum(x for x in margins if x is not None)}).", "ProfitLossForAuditedProductOrServices", "100360", pk)
        for row in rows(T_PROFITABILITY):
            s, c, mg = (_num(val(row, n, pk)) for n in ("Sales", "CostOfSales", "Margin"))
            if None not in (s, c, mg) and s - c != mg:
                err("SUM", f"Product/service {_member(row)}: margin ({mg}) should be sales ({s}) less cost of sales ({c}).", "Margin",
                    "T:" + T_PROFITABILITY, pk, T_PROFITABILITY)
        for tid, name in ((T_INCOMES, "AmountOfIncomesNotConsideredInCostAccounts"), (T_EXPENSES, "AmountOfExpensesnotConsideredInCostAccounts")):
            tot = _num(v.v(P + name, pk))
            parts = [_num(val(row, name, pk)) for row in rows(tid)]
            if tot and not any(p is not None for p in parts):
                err("TABLE", f"'{lab(P + name)}' is {tot}, so give its details in '{schema.tables[tid]['title']}'.", name, "T:" + tid, pk, tid)
            elif tot is not None and any(p is not None for p in parts) and sum(p for p in parts if p is not None) != tot:
                err("SUM", f"The details of '{lab(P + name)}' add up to {sum(p for p in parts if p is not None)}, not {tot}.", name,
                    "T:" + tid, pk, tid)

    # ---------------------------------------------------------- [100370] / [100400]
    for pk in ("CY", "PY"):
        for a, b in (("DistributionOfEarnings", "EarningsAvailableForDistribution"),
                     ("NetRevenueFromOperationsOfCompany", "OperatingIncomesOfCompany"),
                     ("ProfitBeforeTax", "ProfitLossAsPerFinancialAccounts")):
            x, y = _num(v.v(P + a, pk)), _num(v.v(P + b, pk))
            if x is not None and y is not None and x != y:
                err("COMPARE", f"'{lab(P + a)}' ({x}) must equal '{lab(P + b)}' ({y}).", a, v.section_of(P + a), pk)
        g, n = _num(v.v(P + "GrossFixedAssets", pk)), _num(v.v(P + "NetFixedAssets", pk))
        if g is not None and n is not None and n > g:
            err("COMPARE", f"Net fixed assets ({n}) cannot be more than gross fixed assets ({g}).", "NetFixedAssets", "100400", pk)
    if f.cin[:1] in ("L", "U") and _empty(v.v(P + "ShareCapital")):
        err("MANDATORY", "'Share capital' is mandatory for an Indian company (0 if it has no share capital).", "ShareCapital", "100400")

    # previous year: once previous year figures are reported, the company-wide parts need them too
    if py_reported:
        for rule in schema.rules:
            el = schema.element(rule["element"])
            if rule["type"] != "MandatoryCurrentYear" or not el or el.get("noPY") or el.get("type") == "textBlockItemType":
                continue
            if el.get("section") in PY_SECTIONS and not v.section_of(rule["element"]).startswith("T:"):
                if _empty(v.v(rule["element"], "PY")):
                    err("MANDATORY", f"'{el['label']}' is mandatory for the previous year too (previous year figures are reported).",
                        rule["element"], el.get("section", ""), "PY")
        if not ps:
            err("MANDATORY", "Previous year figures are reported, so enter the previous financial year's dates.",
                "DateOfStartOfFirstPreviousFinancialYear", "100200")

    # ---------------------------------------------------------- [100410] related parties
    if _yes(v.v(P + "WhetherCompanyHasRelatedPartyTransactionsForSaleOrPurchaseOfGoodsOrServices")) and not rows(T_RELATED):
        err("TABLE", "The company has related party transactions, so fill 'Description of related party transactions' [100410].",
            "WhetherCompanyHasRelatedPartyTransactionsForSaleOrPurchaseOfGoodsOrServices", "100410", table=T_RELATED)
    for row in rows(T_RELATED):
        who = str(val(row, "NameOfRelatedParty") or _member(row, P + "NameOfRelatedPartyAxis"))
        country = str(val(row, "CountryOfRelatedParty") or "").strip().lower()
        if country == "india" and _empty(val(row, "CINOfRelatedParty")) and _empty(val(row, "PermanentAccountNumberOfRelatedParty")):
            err("DEPENDENCY", f"Related party '{who}' is in India: enter its CIN or PAN.", "CINOfRelatedParty", "T:" + T_RELATED, table=T_RELATED)
        if "other" in str(val(row, "BasisAdoptedToDetermineNormalPriceOfRelatedPartyTransaction") or "").lower() and \
                _empty(val(row, "DescriptionOfOtherBasisAdoptedToDetermineNormalPrice")):
            err("DEPENDENCY", f"Related party '{who}': describe the other basis used for the normal price.",
                "DescriptionOfOtherBasisAdoptedToDetermineNormalPrice", "T:" + T_RELATED, table=T_RELATED)

    # ---------------------------------------------------------- [100420] indirect taxes
    if not any(row["dims"] for row in rows(T_TAXES)):
        err("TABLE", "Reconciliation of indirect taxes [100420] is a mandatory table: fill at least one tax column "
            "(e.g. CGST, SGST, IGST).", "", "100420", table=T_TAXES)
    for row in rows(T_TAXES):
        a, b, d = (_num(val(row, n)) for n in ("DutiesTaxesPaidByCompany", "DutiesTaxesPayableByCompany", "DifferenceBetweenDutiesTaxesPaidAndPayable"))
        if None not in (a, b, d) and a - b != d:
            err("SUM", f"{schema.member_label.get(next(iter(row['dims'].values()), ''), 'Tax')}: difference ({d}) should be "
                f"taxes paid ({a}) less taxes payable ({b}).", "DifferenceBetweenDutiesTaxesPaidAndPayable", "T:" + T_TAXES, table=T_TAXES)


def months_between(a: date, b: date) -> int:
    m = (b.year - a.year) * 12 + b.month - a.month
    return m + (1 if b.day >= a.day - 1 else 0)


# ======================================================================= header and derived facts
def fill_header(f: Filing) -> list[str]:
    """Company details, period dates and fixed answers that come from the filing details."""
    notes = []

    def put(name, value, why=None):
        if value in (None, "") or f.get_value(P + name, "CY") not in (None, ""):
            return
        f.set_value(P + name, "CY", str(value))
        if why:
            notes.append(why)

    put("CorporateIdentityNumberOrForeignCompanyRegistrationNumber", f.cin)
    put("NameOfCompany", f.company)
    put("DateOfStartOfReportingPeriod", f.fy_start)
    put("DateOfEndOfReportingPeriod", f.fy_end)
    put("ReportingCurrencyOfEntity", "INR")
    fs, fe = parse_date(f.fy_start), parse_date(f.fy_end)
    if fs and fe:
        put("DurationInMonthsOfReportingPeriod", months_between(fs, fe))
    products = f.tables.get(T_PRODUCTS, [])
    if any(_yes(r["values"].get(P + "WhetherPreviousYearFiguresAreReported", {}).get("CY")) for r in products):
        put("DateOfStartOfFirstPreviousFinancialYear", f.py_start)
        put("DateOfEndOfFirstPreviousFinancialYear", f.py_end)
        ps, pe = parse_date(f.py_start), parse_date(f.py_end)
        if ps and pe:
            put("DurationInMonthsOfPreviousFinancialYear", months_between(ps, pe))
    auditors = {str(r["values"].get(P + "MembershipNumberOfMemberSigningReport", {}).get("CY") or "").strip()
                for r in f.tables.get(T_AUDITORS, [])} - {""}
    if auditors:
        put("NumberOfCostAuditorsForReportingPeriod", len(auditors),
            f"Number of cost auditors set to {len(auditors)} (distinct membership numbers in the cost auditor details).")
    # the total of the products' net operational revenue
    for pk in ("CY", "PY"):
        parts = [_num(r["values"].get(P + "NetOperationalRevenueOfManufacturedProductOrService", {}).get(pk)) for r in products]
        if any(p is not None for p in parts) and f.get_value(P + "NetOperationalRevenueOfManufacturedProductOrService", pk) in (None, ""):
            from .generator import fmt_number
            f.set_value(P + "NetOperationalRevenueOfManufacturedProductOrService", pk, fmt_number(sum(p for p in parts if p is not None)))
    return notes


# ======================================================================= workbook
MARK = "CRA-4 Costing input workbook"


def _short(title: str) -> str:
    t = re.sub(r"^\[\w+\]\s*", "", title)
    t = re.sub(r"\[table\]|\[abstract\]", "", t, flags=re.I).strip()
    return t


def _sheet_name(prefix: str, code: str, title: str, used: set) -> str:
    base = f"{prefix}{code} {_short(title)}"[:31].rstrip()
    name, i = base, 2
    while name.lower() in used:
        name = f"{base[:28]}~{i}"
        i += 1
    used.add(name.lower())
    return re.sub(r"[\[\]\*\?/\\:]", "-", name)


def _layout(t: dict) -> str:
    """One typed axis (products, auditors): items down, one column per product and year.
    Several axes or named members: one row per column of the table."""
    return "vertical" if len(t["axes"]) == 1 and t["axes"][0].get("typedDomain") else "long"


def make_workbook(f: Filing | None = None, schema=None, n_columns: int = 5) -> bytes:
    """The CRA-4 input workbook, filled with whatever the filing already holds."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    from .schema_loader import load_schema
    schema = schema or load_schema("COST")
    f = f or Filing(taxonomy="COST")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Read me"
    bold, grey = Font(bold=True), PatternFill("solid", fgColor="DDDDDD")
    head = PatternFill("solid", fgColor="1F3A5F")
    white = Font(bold=True, color="FFFFFF")
    small = Font(size=8, color="888888")
    ws["A1"] = MARK
    ws["A1"].font = Font(bold=True, size=14)
    lines = [
        "Fill this workbook and upload it on the XBRL page with 'Costing (CRA-4)' selected.",
        "Each sheet is one part of the annexure to the cost audit report (Costing Taxonomy 2019).",
        "Sheets starting with a number: one row per item, current year and previous year columns.",
        "Sheets starting with T: tables. Grey row 2 holds the XBRL keys: do not change it.",
        "Products/services and cost auditors are numbered 1, 2, 3 ... Use the same number for the same",
        "product in every sheet (quantities, cost statement, materials, utilities, profitability).",
        f"Product sheets have {n_columns} product columns; to add a product, copy a column pair and change the",
        "number in the grey key row (e.g. 6|CY and 6|PY).",
        "Amounts in rupees. Yes/No answers: Yes or No. Dates: DD-MM-YYYY.",
        "Previous year columns are needed only if previous year figures are reported.",
    ]
    for i, line in enumerate(lines, start=3):
        ws.cell(i, 1, line)
    ws.column_dimensions["A"].width = 110
    used = {"read me"}
    yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)

    def show(el, v):
        if v in (None, ""):
            return None
        if el.get("type") == "booleanItemType":
            return "Yes" if _yes(v) else "No" if _no(v) else v
        if el.get("type") in ("monetaryItemType", "decimalItemType", "integerItemType", "percentItemType"):
            n = _num(v)
            return float(n) if n is not None and n != n.to_integral_value() else int(n) if n is not None else v
        if el.get("type") == "dateItemType":
            d = parse_date(v)
            return d.strftime("%d-%m-%Y") if d else v
        return v

    def validations(sheet, el, ref):
        if el.get("type") == "booleanItemType":
            if yes_no not in sheet.data_validations.dataValidation:
                sheet.add_data_validation(yes_no)
            yes_no.add(ref)
        elif el.get("enum"):
            dv = DataValidation(type="list", formula1='"' + ",".join(el["enum"]) + '"', allow_blank=True)
            sheet.add_data_validation(dv)
            dv.add(ref)

    for sec in schema.sections:
        items = [it for it in sec["items"] if not it.get("abstract")]
        if items:
            ws = wb.create_sheet(_sheet_name("", sec["code"], sec["title"], used))
            yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
            for c, h in enumerate(["Key", "Item", "Current year", "Previous year", "MCA rule"], start=1):
                ws.cell(1, c, h).font = white
                ws.cell(1, c).fill = head
            for r, it in enumerate(items, start=2):
                ws.cell(r, 1, it["id"]).font = small
                ws.cell(r, 2, it["label"]).font = bold if it.get("labelRole") == "totalLabel" else Font()
                for c, pk in ((3, "CY"), (4, "PY")):
                    cell = ws.cell(r, c, show(it, f.get_value(it["id"], pk)))
                    if pk == "PY" and it.get("noPY"):
                        cell.fill = grey
                    elif it.get("type") == "textBlockItemType":
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                    validations(ws, it, cell.coordinate)
                ws.cell(r, 5, (it.get("rule") or "")[:300]).alignment = Alignment(wrap_text=True, vertical="top")
            ws.column_dimensions["A"].width = 12
            ws.column_dimensions["B"].width = 60
            ws.column_dimensions["C"].width = 22
            ws.column_dimensions["D"].width = 22
            ws.column_dimensions["E"].width = 70
            ws.freeze_panes = "C2"
        for tid in sec.get("tables", []):
            t = schema.tables[tid]
            ws = wb.create_sheet(_sheet_name("T", sec["code"], t["title"], used))
            yes_no = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
            py = sec["code"] not in ("100100", "100300", "100410", "100420")
            items = [it for it in t["items"] if not it.get("abstract")]
            rows = f.tables.get(tid, [])
            if _layout(t) == "vertical":
                ax = t["axes"][0]
                members = sorted({str(r["dims"].get(ax["id"], "")) for r in rows} - {""}, key=lambda x: (len(x), x))
                members = members + [str(i) for i in range(1, n_columns + 1) if str(i) not in members]
                members = members[:max(n_columns, len(rows))]
                ws.cell(1, 1, "layout:vertical").font = small
                ws.cell(1, 2, f"{_short(t['title'])} - {ax['label'].replace(' [axis]', '')} number").font = bold
                ws.cell(2, 1, "Key").font = small
                ws.cell(2, 2, "axis:" + ax["id"]).font = small
                col = 3
                cols = []
                for m in members:
                    for pk in ("CY", "PY") if py else ("CY",):
                        ws.cell(1, col, f"{m} - {'Current year' if pk == 'CY' else 'Previous year'}").font = white
                        ws.cell(1, col).fill = head
                        ws.cell(2, col, f"{m}|{pk}").font = small
                        ws.cell(2, col).fill = grey
                        ws.column_dimensions[ws.cell(1, col).column_letter].width = 20
                        cols.append((col, m, pk))
                        col += 1
                for r, it in enumerate(items, start=3):
                    ws.cell(r, 1, it["id"]).font = small
                    ws.cell(r, 2, it["label"])
                    for c, m, pk in cols:
                        row = next((x for x in rows if str(x["dims"].get(ax["id"], "")) == m), None)
                        cell = ws.cell(r, c, show(it, row["values"].get(it["id"], {}).get(pk)) if row else None)
                        validations(ws, it, cell.coordinate)
                ws.column_dimensions["A"].width = 12
                ws.column_dimensions["B"].width = 60
                ws.freeze_panes = "C3"
            else:
                ws.cell(1, 1, "layout:long").font = small
                keys, heads = [], []
                for ax in t["axes"]:
                    keys.append("axis:" + ax["id"])
                    heads.append(ax["label"].replace(" [axis]", "") + (" (number)" if ax.get("typedDomain") else ""))
                for it in items:
                    for pk in ("CY", "PY") if py and it.get("type") != "stringItemType" else ("CY",):
                        keys.append(f"{it['id']}|{pk}")
                        heads.append(it["label"] + ("" if not py or it.get("type") == "stringItemType" else
                                                    " - " + ("Current year" if pk == "CY" else "Previous year")))
                for c, (k, h) in enumerate(zip(keys, heads), start=2):
                    ws.cell(1, c, h).font = white
                    ws.cell(1, c).fill = head
                    ws.cell(1, c).alignment = Alignment(wrap_text=True, vertical="top")
                    ws.cell(2, c, k).font = small
                    ws.cell(2, c).fill = grey
                    ws.column_dimensions[ws.cell(1, c).column_letter].width = 22
                ws.cell(2, 1, "Key").font = small
                ws.row_dimensions[1].height = 60
                for ax_i, ax in enumerate(t["axes"]):
                    if ax.get("members"):
                        labels = [mm["label"].replace(" [member]", "") for mm in ax["members"]]
                        dv = DataValidation(type="list", formula1='"' + ",".join(labels) + '"', allow_blank=True)
                        ws.add_data_validation(dv)
                        letter = ws.cell(1, 2 + ax_i).column_letter
                        dv.add(f"{letter}3:{letter}200")
                for r, row in enumerate(rows, start=3):
                    for c, k in enumerate(keys, start=2):
                        if k.startswith("axis:"):
                            m = row["dims"].get(k[5:], "")
                            ws.cell(r, c, schema.member_label.get(m, m).replace(" [member]", "") if m else None)
                        else:
                            eid, pk = k.split("|")
                            ws.cell(r, c, show(schema.element(eid) or {}, row["values"].get(eid, {}).get(pk)))
                for c, k in enumerate(keys, start=2):
                    if "|" in k:
                        el = schema.element(k.split("|")[0]) or {}
                        letter = ws.cell(1, c).column_letter
                        if el.get("type") == "booleanItemType" or el.get("enum"):
                            validations(ws, el, f"{letter}3:{letter}200")
                ws.freeze_panes = "B3"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _cell(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    low = s.lower()
    if low in ("yes", "true"):
        return "true"
    if low in ("no", "false"):
        return "false"
    return s


def is_cost_workbook(path) -> bool:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ok = "Read me" in wb.sheetnames and str(wb["Read me"]["A1"].value or "").startswith(MARK)
        wb.close()
        return ok
    except Exception:  # noqa: BLE001
        return False


def read_workbook(path, f: Filing, schema=None) -> list[str]:
    """Read a filled CRA-4 input workbook into the filing."""
    import openpyxl
    from .schema_loader import load_schema
    schema = schema or load_schema("COST")
    wb = openpyxl.load_workbook(path, data_only=True)
    by_label = {}
    for t in schema.tables.values():
        for a in t["axes"]:
            for i, mm in enumerate(a["members"], start=1):
                by_label[(a["id"], mm["label"].replace(" [member]", "").lower())] = mm["id"]
                by_label[(a["id"], str(i))] = mm["id"]
                by_label[(a["id"], mm["id"].lower())] = mm["id"]
    n_vals, n_rows, log = 0, 0, []
    for ws in wb.worksheets[1:]:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        a1 = str(rows[0][0] or "")
        if a1 == "Key":  # item sheet
            for r in rows[1:]:
                eid = str(r[0] or "").strip()
                if not schema.element(eid):
                    continue
                for c, pk in ((2, "CY"), (3, "PY")):
                    v = _cell(r[c] if len(r) > c else None)
                    if v:
                        f.set_value(eid, pk, v)
                        n_vals += 1
            continue
        if not a1.startswith("layout:") or len(rows) < 2:
            continue
        keys = [str(k or "").strip() for k in rows[1]]
        tid = None
        axes = [k[5:] for k in keys if k.startswith("axis:")]
        item_ids = [k.split("|")[0] for k in keys if "|" in k] + [str(r[0] or "") for r in rows[2:]]
        for key, t in schema.tables.items():
            if [a["id"] for a in t["axes"]] == axes and any(i in t["item_ids"] for i in item_ids if i):
                if tid is None or sum(i in t["item_ids"] for i in item_ids) > sum(i in schema.tables[tid]["item_ids"] for i in item_ids):
                    tid = key
        if not tid:
            log.append(f"Sheet '{ws.title}': not recognised")
            continue
        if a1 == "layout:vertical":
            ax = axes[0]
            for r in rows[2:]:
                eid = str(r[0] or "").strip()
                if not schema.element(eid):
                    continue
                for c, k in enumerate(keys):
                    if "|" not in k or c >= len(r):
                        continue
                    m, pk = k.split("|", 1)
                    v = _cell(r[c])
                    if v and m:
                        row = f.table_row(tid, {ax: m})
                        row["values"].setdefault(eid, {})[pk] = v
                        n_vals += 1
        else:
            for r in rows[2:]:
                dims, vals = {}, {}
                for c, k in enumerate(keys):
                    if c >= len(r) or not k:
                        continue
                    v = _cell(r[c])
                    if not v:
                        continue
                    if k.startswith("axis:"):
                        ax = k[5:]
                        dims[ax] = by_label.get((ax, v.lower()), v)
                    elif "|" in k:
                        eid, pk = k.split("|", 1)
                        vals.setdefault(eid, {})[pk] = v
                if vals and len(dims) == len(axes):
                    row = f.table_row(tid, dims)
                    for eid, per in vals.items():
                        row["values"].setdefault(eid, {}).update(per)
                        n_vals += len(per)
                    n_rows += 1
                elif vals:
                    log.append(f"Sheet '{ws.title}': a row without {', '.join(a.split('_', 1)[-1] for a in axes)} was skipped")
    return [f"read {n_vals} values ({n_rows} table rows)"] + log[:10]


# ======================================================================= the cost auditor's report
REPORT_KEYS = [  # paragraph wording -> [100100] text block
    (r"cost auditing standards", AUDITOR_TEXT[0]),
    (r"(obtained|sought).{0,60}information and explanations", AUDITOR_TEXT[1]),
    (r"(proper )?cost records.{0,120}(rule\s*5|maintained)", AUDITOR_TEXT[2]),
    (r"branches not visited|not visited by us", AUDITOR_TEXT[3]),
    (r"companies act,? 2013.{0,80}(required|manner)|required by the (companies )?act", AUDITOR_TEXT[4]),
    (r"internal audit", AUDITOR_TEXT[5]),
    (r"true and fair view", AUDITOR_TEXT[6]),
    (r"(audited|certified).{0,40}cost statements|cost statements and schedules", AUDITOR_TEXT[7]),
    (r"observations?( and|/| or) suggestions?", AUDITOR_TEXT[8]),
]


def read_cost_report(text: str, f: Filing) -> list[str]:
    """Fill the [100100] text blocks (cost auditor 1) and auditor details from the report's wording."""
    notes = []
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=\s*\(?[a-z0-9ivx]{1,4}[.)]\s)", text) if len(p.strip()) > 30]
    row = f.table_row(T_AUDITOR_REPORT, {AUDITOR_AXIS: "1"})
    put = 0
    for rx, name in REPORT_KEYS:
        hits = [p for p in paras if re.search(rx, p, re.I | re.S)]
        if hits and _empty(row["values"].get(P + name, {}).get("CY")):
            row["values"][P + name] = {"CY": "<p>" + "</p><p>".join(_esc(h) for h in hits[:3]) + "</p>"}
            put += 1
    if put:
        notes.append(f"{put} paragraphs of the cost audit report put into the matching [100100] text blocks.")
    aud = f.table_row(T_AUDITORS, {AUDITOR_AXIS: "1"})
    found = {
        "MembershipNumberOfMemberSigningReport": r"(?:membership|m\.?\s*no)\.?\s*(?:no\.?|number)?\s*[:\-]?\s*([A-Z]?\d{3,6})",
        "FirmsRegistrationNumber": r"(?:firm(?:'s)?\s+registration\s+(?:no\.?|number)|FRN)\s*[:\-]?\s*([A-Z0-9/]{4,12})",
        "PlaceOfSigningCostAuditReportAndAnnexureByCostAuditor": r"place\s*[:\-]\s*([A-Za-z .]{3,30})",
        "DateOfSigningCostAuditReportAndAnnexureByCostAuditor": r"date[d]?\s*[:\-]\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
    }
    for name, rx in found.items():
        m = re.search(rx, text, re.I)
        if m and _empty(aud["values"].get(P + name, {}).get("CY")):
            v = m.group(1).strip()
            if name.startswith("DateOf"):
                d = parse_date(v.replace(".", "-").replace("/", "-"))
                v = d.isoformat() if d else v
            aud["values"][P + name] = {"CY": v}
            notes.append(f"'{name}' read from the cost audit report: {v}")
    if not aud["values"]:
        f.tables[T_AUDITORS].remove(aud)
    if not row["values"]:
        f.tables[T_AUDITOR_REPORT].remove(row)
    return notes


def _esc(s: str) -> str:
    return re.sub(r"\s+", " ", s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ======================================================================= browser entry point
def process_cost(files: list, h: dict) -> str:
    from .browser import _inputs, _review, _Sources, _write
    from .generator import generate, output_filename
    from .importers.docs_reader import document_html, html_to_text
    from .importers.instance_reader import read_instance
    from .schema_loader import load_schema
    from .tables import apply_table_answers
    from .validator import validate

    schema = load_schema("COST")
    f = Filing(taxonomy="COST", nature="")
    log, notes = [], []
    prior = None
    S = _Sources(f)
    for name, data in files:
        p = _write(name, data)
        ext = p.suffix.lower()
        if ext == ".xml":
            fx, w = read_instance(p, "COST")
            if h.get("fy_end") and fx.fy_end and str(fx.fy_end) < str(h["fy_end"]):
                prior = fx
                log.append(f"{name}: last year's CRA-4 ({fx.fy_start} to {fx.fy_end}); its current year figures are used as previous year")
                continue
            f.values.update(fx.values)
            for tid, rows in fx.tables.items():
                f.tables.setdefault(tid, []).extend(rows)
            for k in ("cin", "fy_start", "fy_end", "py_start", "py_end"):
                if getattr(fx, k) and not getattr(f, k):
                    setattr(f, k, getattr(fx, k))
            log += [f"{name}: read {len(fx.values)} items and {sum(len(r) for r in fx.tables.values())} table columns"] + w[:10]
            S.stage(f"from {name}")
        elif ext in (".xlsx", ".xlsm") and is_cost_workbook(p):
            log += [f"{name}: " + x for x in read_workbook(p, f, schema)]
            S.stage(f"from {name}")
        elif ext in (".docx", ".pdf", ".txt", ".htm", ".html"):
            try:
                body = document_html(p) if ext not in (".txt",) else p.read_text(errors="ignore")
            except Exception as e:  # noqa: BLE001
                log.append(f"{name}: could not be read ({e})")
                continue
            text = html_to_text(body) if body and ext != ".txt" else body
            if re.search(r"cost accounting polic", name + text[:400], re.I):
                f.set_value(P + "CostAccountingPolicyExplanatory", "CY", body)
                log.append(f"{name}: put into 'Cost accounting policy'")
            else:
                notes += read_cost_report(text, f)
                log.append(f"{name}: read as the cost audit report")
            S.stage(f"from {name}")
        else:
            log.append(f"{name}: for CRA-4 upload the CRA-4 input workbook (.xlsx), a CRA-4 XML, or the cost audit report (.docx/.pdf)")
    for k in ("company", "cin", "fy_start", "fy_end"):
        if h.get(k):
            setattr(f, k, h[k].strip().upper() if k == "cin" else h[k])
    f.ensure_periods()
    if prior:  # last year's current-year figures are this year's previous-year figures
        n = 0
        for eid, per in prior.values.items():
            el = schema.element(eid)
            if el and not el.get("noPY") and per.get("CY") not in (None, "") and f.get_value(eid, "PY") in (None, ""):
                f.set_value(eid, "PY", per["CY"])
                n += 1
        for tid, rows in prior.tables.items():
            if tid in (T_AUDITORS, T_AUDITOR_REPORT, T_RELATED, T_TAXES):
                continue
            for prow in rows:
                row = f.table_row(tid, prow["dims"], create=False)
                if row is None:
                    continue
                for eid, per in prow["values"].items():
                    if per.get("CY") not in (None, "") and row["values"].get(eid, {}).get("PY") in (None, ""):
                        el = schema.element(eid) or {}
                        if el.get("type") not in ("stringItemType", "textBlockItemType", "booleanItemType"):
                            row["values"].setdefault(eid, {})["PY"] = per["CY"]
                            n += 1
        if n:
            notes.append(f"{n} previous year figures taken from last year's CRA-4.")
        S.stage("from last year's CRA-4 (its current year)")
    for eid, per in (h.get("values") or {}).items():
        if schema.element(eid) is None:
            continue
        for pk, v in per.items():
            if v not in (None, ""):
                f.set_value(eid, pk, str(v))
    apply_table_answers(f, schema, h.get("values") or {})
    S.stage("your answer / correction")
    notes += fill_header(f)
    S.stage("from the filing details you entered")
    res = validate(f)
    xml, warns = generate(f)
    import base64
    return json.dumps({
        "taxonomy": "COST", "company": f.company or f.get_value(P + "NameOfCompany", "CY") or "", "cin": f.cin,
        "fy_start": f.fy_start, "fy_end": f.fy_end, "nature": "Cost audit report (CRA-4)",
        "errors": res.errors, "warnings": res.warnings, "log": log, "mapped": [], "xml": xml,
        "filename": output_filename(f), "facts": xml.count('contextRef="'), "notes": notes + warns,
        "inputs": _inputs(res, schema), "review": _review(f, schema, S.src),
        "workbook": base64.b64encode(make_workbook(f, schema)).decode(),
    })
