"""Validation of a Filing before the XML is generated.

Checks performed:

* filing level: CIN format, reporting period (<= 18 months, ordered dates),
  standalone/consolidated, mandatory general information;
* data types of every entered value (amounts, shares, dates, booleans,
  CIN / PAN / DIN / SRN / ITC codes, percentages);
* the MCA business rules shipped with the InstantXBRL template
  ("Specific Business rules" sheet): mandatory, mandatory-in-current-year,
  standalone/consolidated-only, non-negative, percentage, dependency,
  yes/no-triggered fields, dates not in the future, less-than-element,
  different-from-element, table-mandatory elements, etc.;
* MCA's official business rules workbook for the taxonomy (business_rules.py):
  element rules, mandatory line items, parent/child members and the generic
  rules;
* accounting checks: totals equal the sum of their components (template
  formulas / taxonomy calculation linkbase) and balance sheet balances.

Each finding is a dict: {level: 'error'|'warning', code, element, section,
period, message}.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from .business_rules import BusinessRules
from .calc import check_totals, fill_totals
from .generator import is_numeric, to_number
from .model import Filing, parse_date
from .schema_loader import load_schema

CIN_RE = re.compile(r"^[LUFlu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
FCRN_RE = re.compile(r"^F\d{5}$")
LLPIN_RE = re.compile(r"^[A-Z]{3}-\d{4}$")
PAN_RE = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
DIN_RE = re.compile(r"^\d{8}$")
SRN_RE = re.compile(r"^[A-Za-z]\d{8}$")
TYPE_PATTERNS = {
    "CINNumber": (CIN_RE, "a valid CIN (21 characters, e.g. U74899DL1993PTC051877)"),
    "PANNumber": (PAN_RE, "a valid PAN (e.g. ABCDE1234F)"),
    "DINNumber": (DIN_RE, "an 8 digit DIN"),
    "SRNNumber": (SRN_RE, "a valid SRN (letter followed by 8 digits)"),
    "ITCNumber4Digits": (re.compile(r"^\d{4}$"), "a 4 digit ITC/NPCS code"),
    "ITCNumber8Digits": (re.compile(r"^\d{8}$"), "an 8 digit ITC/NPCS code"),
}
BOOL_WORDS = {"true", "false", "yes", "no"}


class Result:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, level, code, message, element="", section="", period="", table=""):
        self.items.append({"level": level, "code": code, "message": message, "element": element,
                           "section": section, "period": period, "table": table})

    @property
    def errors(self):
        return [i for i in self.items if i["level"] == "error"]

    @property
    def warnings(self):
        return [i for i in self.items if i["level"] == "warning"]

    def to_dict(self):
        return {"errors": self.errors, "warnings": self.warnings,
                "ok": not self.errors, "count": len(self.items)}


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


class Validator:
    def __init__(self, filing: Filing):
        self.f = filing
        self.f.ensure_periods()
        self.schema = load_schema(filing.taxonomy)
        self.r = Result()
        self.vals = fill_totals(filing, self.schema)

    # ------------------------------------------------------------ helpers
    def label(self, eid):
        el = self.schema.element(eid)
        return el["label"] if el else eid

    def section_of(self, eid):
        el = self.schema.element(eid)
        if el and el.get("section"):
            return el["section"]
        for tid, t in self.schema.tables.items():
            if eid in t["item_ids"]:
                return "T:" + tid
        return ""

    def v(self, eid, pk="CY"):
        return self.vals.get(eid, {}).get(pk)

    def all_values(self, eid):
        """Yield (pk, value, table_id, dims) for an element everywhere it appears."""
        for pk, v in self.vals.get(eid, {}).items():
            yield pk, v, "", None
        for tid, rows in self.f.tables.items():
            for row in rows:
                for pk, v in row["values"].get(eid, {}).items():
                    yield pk, v, tid, row["dims"]

    def has_any(self, eid) -> bool:
        return any(not _empty(v) for _, v, _, _ in self.all_values(eid))

    def table_rows(self, tid):
        return self.f.tables.get(tid, [])

    # ------------------------------------------------------------ checks
    def check_filing(self):
        f = self.f
        if not f.cin:
            self.r.add("error", "CIN", "Corporate identity number (CIN) is required.", section="400100")
        elif not (CIN_RE.match(f.cin) or FCRN_RE.match(f.cin)):
            self.r.add("error", "CIN", f"CIN '{f.cin}' is not in the valid 21 character format.", section="400100")
        fs, fe = parse_date(f.fy_start), parse_date(f.fy_end)
        if not fs or not fe:
            self.r.add("error", "PERIOD", "Financial year start and end dates are required.")
        else:
            if fe <= fs:
                self.r.add("error", "PERIOD", "Financial year end date must be after the start date.")
            months = (fe.year - fs.year) * 12 + fe.month - fs.month + (1 if fe.day >= fs.day - 1 else 0)
            if months > 18:
                self.r.add("error", "PERIOD", "Reporting period cannot exceed 18 months (Section 2(41)).")
            if fe > date.today():
                self.r.add("warning", "PERIOD", "Financial year end date is in the future.")
        if f.nature not in ("Standalone", "Consolidated"):
            self.r.add("error", "NATURE", "Nature of report must be Standalone or Consolidated.")
        cin_fact = self.v(self._id("in-ca", "CorporateIdentityNumber"))
        if cin_fact and f.cin and cin_fact.strip().upper() != f.cin.upper():
            self.r.add("error", "CIN", "CIN in general information differs from the filing CIN.",
                       element=self._id("in-ca", "CorporateIdentityNumber"))

    def _id(self, prefix, name):
        return f"{prefix}_{name}"

    def check_types(self):
        today = date.today()

        def chk(eid, pk, v, tid, dims):
            el = self.schema.element(eid)
            if el is None or _empty(v):
                return
            t = el.get("type", "")
            sec = self.section_of(eid)
            lab = el["label"]
            if is_numeric(el):
                if to_number(v) is None:
                    self.r.add("error", "TYPE", f"'{lab}' must be a number (got '{v}').", eid, sec, pk, tid)
                elif t == "sharesItemType" and to_number(v) != to_number(v).to_integral_value():
                    self.r.add("warning", "TYPE", f"'{lab}' should be a whole number of shares.", eid, sec, pk, tid)
            elif t == "dateItemType":
                d = parse_date(v)
                if not d:
                    self.r.add("error", "TYPE", f"'{lab}' must be a date (YYYY-MM-DD or DD-MM-YYYY).", eid, sec, pk, tid)
            elif t == "booleanItemType":
                if str(v).strip().lower() not in BOOL_WORDS:
                    self.r.add("error", "TYPE", f"'{lab}' must be Yes or No.", eid, sec, pk, tid)
            elif t in TYPE_PATTERNS:
                rx, what = TYPE_PATTERNS[t]
                if not rx.match(str(v).strip()):
                    self.r.add("error", "TYPE", f"'{lab}' must be {what} (got '{v}').", eid, sec, pk, tid)

        for eid, per in self.f.values.items():
            for pk, v in per.items():
                chk(eid, pk, v, "", None)
        for tid, rows in self.f.tables.items():
            for row in rows:
                for eid, per in row["values"].items():
                    for pk, v in per.items():
                        chk(eid, pk, v, tid, row["dims"])
        _ = today

    # ------------------------------------------------------------ rules
    def check_rules(self):
        standalone = (self.f.nature or "Standalone") == "Standalone"
        today = date.today()
        for rule in self.schema.rules:
            typ = rule["type"]
            eid = rule["element"]
            el = self.schema.element(eid)
            if el is None:
                continue
            lab = el["label"]
            sec = self.section_of(eid)
            msg = rule["msg"]
            in_table = sec.startswith("T:")
            # rules on table elements only apply when the table has been used
            if in_table and not self.table_rows(sec[2:]) and typ.startswith("Mandatory"):
                continue
            try:
                getattr(self, "rule_" + typ, self.rule_unknown)(rule, eid, el, lab, sec, msg, standalone, today)
            except Exception as e:  # noqa: BLE001 - never crash validation on a rule
                self.r.add("warning", "RULE", f"Rule {rule['no']} ({typ}) on '{lab}' could not be evaluated: {e}", eid, sec)

    def rule_unknown(self, *a, **k):
        return

    def _mandatory(self, eid, el, lab, sec, msg, periods):
        if sec.startswith("T:"):
            for row in self.table_rows(sec[2:]):
                if not self.schema.is_detail_column(sec[2:], row["dims"]):
                    continue
                for pk in periods:
                    if _empty(row["values"].get(eid, {}).get(pk)):
                        self.r.add("error", "MANDATORY", f"'{lab}' is mandatory in each column of the table ({pk}).",
                                   eid, sec, pk, sec[2:])
                        break
            return
        for pk in periods:
            if pk == "PY" and el.get("noPY"):
                continue
            if pk == "CY" and el.get("noCY"):
                continue
            if _empty(self.v(eid, pk)):
                self.r.add("error", "MANDATORY", f"'{lab}' is mandatory ({'current' if pk == 'CY' else 'previous'} year). {msg}".strip(),
                           eid, sec, pk)

    def rule_Mandatory(self, rule, eid, el, lab, sec, msg, standalone, today):
        self._mandatory(eid, el, lab, sec, msg, ("CY", "PY") if self._needs_py(el) else ("CY",))

    def _needs_py(self, el):
        return not el.get("noPY") and el.get("type") in ("monetaryItemType", "sharesItemType", "perShareItemType", "decimalItemType")

    def rule_MandatoryCurrentYear(self, rule, eid, el, lab, sec, msg, standalone, today):
        if rule["p1"] == "Standalone" and not standalone:
            return
        if rule["p1"] == "Consolidated" and standalone:
            return
        self._mandatory(eid, el, lab, sec, msg, ("CY",))

    def rule_MandatoryStandalone(self, rule, eid, el, lab, sec, msg, standalone, today):
        if standalone:
            self._mandatory(eid, el, lab, sec, msg, ("CY",))

    def rule_MandatoryConsolidated(self, rule, eid, el, lab, sec, msg, standalone, today):
        if not standalone:
            self._mandatory(eid, el, lab, sec, msg, ("CY",))

    rule_MandatoryConsolidatedCurrentYear = rule_MandatoryConsolidated

    def rule_MandatoryINR(self, rule, eid, el, lab, sec, msg, standalone, today):
        self._mandatory(eid, el, lab, sec, msg, ("CY",))

    def rule_NonNegative(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            n = to_number(v)
            if n is not None and n < 0:
                self.r.add("error", "NONNEG", f"'{lab}' should be greater than or equal to zero.", eid, sec, pk, tid)

    def rule_Percent(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            n = to_number(v)
            if n is not None and not (0 <= n <= 100):
                # MCA percentages are fractions in some elements; accept 0-1 as well
                self.r.add("error", "PERCENT", f"'{lab}' should be between 0 and 100.", eid, sec, pk, tid)

    def rule_ValidPANNumber(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            if not _empty(v) and not PAN_RE.match(str(v).strip()):
                self.r.add("error", "PAN", f"'{lab}' must be a valid PAN (got '{v}').", eid, sec, pk, tid)

    def rule_MandatoryDependency(self, rule, eid, el, lab, sec, msg, standalone, today):
        if not self.has_any(eid):
            return
        for dep in filter(None, rule["p1"].split(",")):
            dep = dep.strip().replace(":", "_")
            if self.schema.element(dep) and not self.has_any(dep):
                self.r.add("error", "DEPENDENCY", f"'{self.label(dep)}' is mandatory because '{lab}' is entered.", dep,
                           self.section_of(dep))

    def rule_MandatoryViceVersa(self, rule, eid, el, lab, sec, msg, standalone, today):
        dep = rule["p1"].replace(":", "_")
        if not self.schema.element(dep):
            return
        a, b = self.has_any(eid), self.has_any(dep)
        if rule.get("p2"):
            # "Yes" here <-> one of these values there (e.g. subsidiary = Yes <-> a 'Holding company' relationship)
            wanted = {w.strip().lower() for w in rule["p2"].split(",") if w.strip()}
            a = any(str(v).strip().lower() in ("true", "yes") for _pk, v, _t, _d in self.all_values(eid))
            b = any(str(v).strip().lower() in wanted for _pk, v, _t, _d in self.all_values(dep))
            if a and not b:
                self.r.add("error", "DEPENDENCY", f"'{self.label(dep)}' must include {rule['p2']} when '{lab}' is Yes.",
                           dep, self.section_of(dep))
            elif b and not a:
                self.r.add("error", "DEPENDENCY", f"'{lab}' must be Yes when '{self.label(dep)}' includes {rule['p2']}.",
                           eid, self.section_of(eid))
            return
        if a != b:
            missing = dep if a else eid
            other = eid if a else dep
            self.r.add("error", "DEPENDENCY", f"'{self.label(missing)}' is mandatory when '{self.label(other)}' is entered.",
                       missing, self.section_of(missing))

    def rule_MandatoryElement_If_YesNo_InTarget(self, rule, eid, el, lab, sec, msg, standalone, today):
        want = (rule["p1"] or "true").strip().lower()
        target = rule["p2"].replace(":", "_")
        for pk, v, tid, dims in self.all_values(target):
            val = str(v).strip().lower()
            val = {"yes": "true", "no": "false"}.get(val, val)
            if val == want and not self.has_any(eid):
                self.r.add("error", "DEPENDENCY",
                           f"'{lab}' is mandatory because '{self.label(target)}' is {'Yes' if want == 'true' else 'No'}.",
                           eid, sec, "CY")
                return

    def rule_ElementMandatory_IfCorrespondingElementNotZero(self, rule, eid, el, lab, sec, msg, standalone, today):
        dep = rule["p1"].replace(":", "_")
        for pk, v, tid, dims in self.all_values(eid):
            n = to_number(v)
            if n is not None and n > 0 and not self.has_any(dep):
                self.r.add("error", "DEPENDENCY", f"'{self.label(dep)}' is mandatory because '{lab}' is greater than zero.",
                           dep, self.section_of(dep))
                return

    def rule_Validity_Against_SystemDate(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            d = parse_date(v)
            if d and d > today:
                self.r.add("error", "DATE", f"'{lab}' cannot be later than today's date.", eid, sec, pk, tid)

    def rule_DateBelow18Year(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            d = parse_date(v)
            if d and (today.year - d.year - ((today.month, today.day) < (d.month, d.day))) < 18:
                self.r.add("error", "DATE", f"'{lab}' must be at least 18 years before today.", eid, sec, pk, tid)

    def rule_NonNegativeAndLessThanElement(self, rule, eid, el, lab, sec, msg, standalone, today):
        other = rule["p1"].replace(":", "_")
        if sec.startswith("T:"):
            for row in self.table_rows(sec[2:]):
                for pk in ("CY", "PY"):
                    a = to_number(row["values"].get(eid, {}).get(pk))
                    b = to_number(row["values"].get(other, {}).get(pk))
                    if a is not None and a < 0:
                        self.r.add("error", "NONNEG", f"'{lab}' should be >= 0.", eid, sec, pk, sec[2:])
                    if a is not None and b is not None and a > b:
                        self.r.add("error", "COMPARE", f"'{lab}' ({a}) should be <= '{self.label(other)}' ({b}).",
                                   eid, sec, pk, sec[2:])
            return
        for pk in ("CY", "PY"):
            a, b = to_number(self.v(eid, pk)), to_number(self.v(other, pk))
            if a is not None and a < 0:
                self.r.add("error", "NONNEG", f"'{lab}' should be >= 0.", eid, sec, pk)
            if a is not None and b is not None and a > b:
                self.r.add("error", "COMPARE", f"'{lab}' ({a}) should be <= '{self.label(other)}' ({b}).", eid, sec, pk)

    def rule_DifferentFrom_TargetElement(self, rule, eid, el, lab, sec, msg, standalone, today):
        other = rule["p1"].replace(":", "_")
        for pk, v, tid, dims in self.all_values(eid):
            for pk2, v2, tid2, dims2 in self.all_values(other):
                if tid == tid2 and dims == dims2 and pk == pk2 and not _empty(v) and str(v).strip() == str(v2).strip():
                    self.r.add("error", "DIFFERENT", f"'{lab}' should be different from '{self.label(other)}'.", eid, sec, pk, tid)

    def rule_DifferentCIN_FromElement(self, rule, eid, el, lab, sec, msg, standalone, today):
        for pk, v, tid, dims in self.all_values(eid):
            if not _empty(v) and self.f.cin and str(v).strip().upper() == self.f.cin.upper():
                self.r.add("error", "DIFFERENT", f"'{lab}' should be different from the company's own CIN.", eid, sec, pk, tid)

    def rule_TableMandatoryElements(self, rule, eid, el, lab, sec, msg, standalone, today):
        tid = eid
        if tid not in self.schema.tables:
            return
        req = [x.strip().replace(":", "_") for x in rule["p1"].split(",") if x.strip()]
        for i, row in enumerate(self.table_rows(tid)):
            if not self.schema.is_detail_column(tid, row["dims"]):
                continue
            for r_ in req:
                if self.schema.element(r_) and all(_empty(x) for x in row["values"].get(r_, {}).values()):
                    self.r.add("error", "TABLE", f"'{self.label(r_)}' is required in column {i + 1} of '{self.schema.tables[tid]['title']}'.",
                               r_, "T:" + tid, "CY", tid)

    rule_AllDetailsOfTable = rule_TableMandatoryElements

    def rule_TableDependsOnElementValue(self, rule, eid, el, lab, sec, msg, standalone, today):
        want = (rule["p1"] or "true").lower()
        tid = rule["p2"]
        val = str(self.v(eid) or "").strip().lower()
        val = {"yes": "true", "no": "false"}.get(val, val)
        if val == want and tid in self.schema.tables and not self.table_rows(tid):
            self.r.add("error", "TABLE", f"'{self.schema.tables[tid]['title']}' must be filled because '{lab}' is Yes.",
                       eid, sec, "CY", tid)

    def rule_ElementUnique_InTuples(self, rule, eid, el, lab, sec, msg, standalone, today):
        seen = {}
        for pk, v, tid, dims in self.all_values(eid):
            if _empty(v):
                continue
            key = (tid, pk, str(v).strip().upper())
            if key in seen:
                self.r.add("error", "UNIQUE", f"'{lab}' value '{v}' is repeated.", eid, sec, pk, tid)
            seen[key] = True

    # ------------------------------------------------------------ accounting
    def check_totals(self):
        for tot, pk, entered, computed, kids in check_totals(self.f, self.schema):
            self.r.add("error", "TOTAL",
                       f"'{self.label(tot)}' is {entered} but its components add up to {computed}.",
                       tot, self.section_of(tot), pk)
        bs = [("Assets", "EquityAndLiabilities")]
        prefix = "in-gaap" if self.f.taxonomy == "AS" else "ind-as"
        for a, b in bs:
            for pk in ("CY", "PY"):
                x, y = to_number(self.v(f"{prefix}_{a}", pk)), to_number(self.v(f"{prefix}_{b}", pk))
                if x is not None and y is not None and x != y:
                    self.r.add("error", "BALANCE",
                               f"Balance sheet does not balance ({'CY' if pk == 'CY' else 'PY'}): total assets {x} vs total equity and liabilities {y}.",
                               f"{prefix}_{a}", self.section_of(f"{prefix}_{a}"), pk)

    def check_tables(self):
        for tid, rows in self.f.tables.items():
            t = self.schema.tables.get(tid)
            if not t:
                self.r.add("warning", "TABLE", f"Table '{tid}' is not part of the template; its facts are written as-is.")
                continue
            seen = set()
            for i, row in enumerate(rows):
                key = tuple(sorted(row["dims"].items()))
                if key in seen:
                    self.r.add("error", "TABLE", f"Column {i + 1} of '{t['title']}' repeats the same member(s) as another column.",
                               section="T:" + tid, table=tid)
                seen.add(key)
                for a in t["axes"]:
                    m = row["dims"].get(a["id"])
                    if a.get("typedDomain") and _empty(m):
                        self.r.add("error", "TABLE", f"Column {i + 1} of '{t['title']}': enter a value for '{a['label']}'.",
                                   section="T:" + tid, table=tid)

    # ------------------------------------------------------------ MCA's own checks (from its validation tool)
    MCA_TABLE_IF_AMOUNT = [
        ("DisclosureOfClassesOfShareCapitalTable", ["ShareCapital"]),
        ("StatementOfChangesInReservesTable", ["ReservesAndSurplus"]),
        ("ClassificationOfBorrowingsTable", ["LongTermBorrowings", "ShortTermBorrowings"]),
        ("DetailsOfNoncurrentInvestmentsTable", ["NoncurrentInvestments"]),
        ("DisclosureOfBreakupOfProvisionsTable", ["LongTermProvisions", "ShortTermProvisions"]),
        ("LoansAndAdvancesTable", ["LongTermLoansAndAdvances", "ShortTermLoansAndAdvances"]),
        ("SubclassificationOfTradeReceivablesTable", ["TradeReceivables"]),
        ("DisclosureOfTangibleAssetsTable", ["TangibleAssets"]),
        ("DisclosureOfAdditionalInformationTangibleAssetsTable", ["TangibleAssets"]),
        ("DisclosureOfIntangibleAssetsTable", ["IntangibleAssets"]),
        ("DisclosureOfAdditionalInformationIntangibleAssetsTable", ["IntangibleAssets"]),
        ("DisclosureOfPrincipalProductOrServicesTable", ["RevenueFromSaleOfProducts", "RevenueFromSaleOfServices"]),
    ]
    MCA_TABLE_IF_YES = [
        ("DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable", "WhetherThereAreAnyShareholdersHoldingMoreThanFivePerCentSharesInCompany"),
        ("DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable", "WhetherThereAreAnyRelatedPartyTransactionsDuringYear"),
        ("DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable", "WhetherCompaniesAuditorsReportOrderIsApplicableOnCompany"),
        ("ClassificationOfCSRSpendingTable", "WhetherProvisionsOfCorporateSocialResponsibilityAreApplicableOnCompany"),
        ("DisclosureOfNetProfitsForLastThreeFinancialYearsTable", "WhetherProvisionsOfCorporateSocialResponsibilityAreApplicableOnCompany"),
    ]
    MCA_TABLE_ALWAYS = ["DetailsRegardingAuditorsTable", "DetailsOfDirectorsSigningBoardReportTable",
                        "DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable",
                        "DetailsOfDirectorsSigningFinancialStatementsTable"]
    MCA_AT_LEAST_ONE = [
        ("RevenueFromSaleOfServices", ["DomesticRevenueServices", "ExportRevenueServices"]),
        ("RevenueFromSaleOfServices", ["BrokerageDiscountsRebatesOnRevenueFromSaleOfServices", "RevenueFromSaleOfServicesGross",
                                       "ReturnsOnRevenueFromSaleOfServices", "OtherAllowancesDeductionsOnRevenueFromSaleOfServices"]),
        ("DepreciationDepletionAndAmortisationExpense", ["AmortisationExpense", "DepletionExpense", "DepreciationExpense"]),
    ]

    def _lid(self, local):
        for p in ("in-gaap_", "in-ca_"):
            if self.schema.element(p + local) or p + local in self.schema.tables:
                return p + local
        return None

    def check_mca(self):
        if self.f.taxonomy != "AS":
            return
        n = lambda x: to_number(x)  # noqa: E731
        fs = parse_date(self.f.fy_start)
        if fs and fs.isoformat() > "2018-04-01":
            for local in ("NumberOfEmployeesInTheCompanyAtTheEndOfTheFinancialYear",
                          "WhetherCompanyHasPublishedSustainabilityReportForTheFinancialYear"):
                eid = self._lid(local)
                if eid and _empty(self.v(eid, "CY")):
                    self.r.add("error", "MANDATORY", f"'{self.label(eid)}' is mandatory for years starting after 01-04-2018.",
                               eid, self.section_of(eid), "CY")
        for tname, faces in self.MCA_TABLE_IF_AMOUNT:
            tid = self._lid(tname)
            if not tid or self.f.tables.get(tid):
                continue
            for pk in ("CY", "PY"):
                hit = [fc for fc in faces if self._lid(fc) and (n(self.v(self._lid(fc), pk)) or 0) != 0]
                if hit and not (tname == "DisclosureOfPrincipalProductOrServicesTable" and pk == "PY"):
                    self.r.add("error", "MCA_TABLE", f"MCA needs '{self.schema.tables[tid]['title']}' because "
                               f"'{self.label(self._lid(hit[0]))}' is not zero ({pk}).", "", "T:" + tid, pk, tid)
                    break
        for tname, flag in self.MCA_TABLE_IF_YES:
            tid, fid = self._lid(tname), self._lid(flag)
            if tid and fid and str(self.v(fid, "CY")).lower() == "true" and not self.f.tables.get(tid):
                self.r.add("error", "MCA_TABLE", f"MCA needs '{self.schema.tables[tid]['title']}' because "
                           f"'{self.label(fid)}' is Yes.", "", "T:" + tid, "CY", tid)
        for tname in self.MCA_TABLE_ALWAYS:
            tid = self._lid(tname)
            if tid and not self.f.tables.get(tid):
                self.r.add("error", "MCA_TABLE", f"MCA always needs '{self.schema.tables[tid]['title']}'.", "", "T:" + tid, "CY", tid)
        for parent, kids in self.MCA_AT_LEAST_ONE:
            pid = self._lid(parent)
            if not pid:
                continue
            for pk in ("CY", "PY"):
                if (n(self.v(pid, pk)) or 0) == 0:
                    continue
                if not any(self._lid(k) and not _empty(self.v(self._lid(k), pk)) for k in kids):
                    self.r.add("error", "MCA_ONE_OF", f"As '{self.label(pid)}' is given ({pk}), MCA needs at least one of: "
                               + ", ".join(self.label(self._lid(k)) for k in kids if self._lid(k)) + ".", pid, self.section_of(pid), pk)
        # table totals must agree with the face of the statements
        def tsum(tname, eid_local, pick, pk):
            tid = self._lid(tname)
            eid = self._lid(eid_local)
            tot, seen = 0, False
            for row in self.f.tables.get(tid, []) if tid else []:
                if pick(row["dims"]):
                    v = n(row["values"].get(eid, {}).get(pk))
                    if v is not None:
                        tot += v
                        seen = True
            return tot if seen else None
        short = lambda d: {k.split("_", 1)[1]: str(v).split("_", 1)[-1] for k, v in d.items()}  # noqa: E731
        sums = [
            ("DisclosureOfClassesOfShareCapitalTable", "ShareCapital", "ShareCapital",
             lambda d: short(d).get("ClassesOfShareCapitalAxis") in ("EquitySharesMember", "PreferenceSharesMember") and len(d) == 1),
            ("SubclassificationOfTradeReceivablesTable", "TradeReceivables", "TradeReceivables", lambda d: len(d) == 1),
            ("DetailsOfNoncurrentInvestmentsTable", "NoncurrentInvestments", "NoncurrentInvestments", lambda d: len(d) == 1),
            ("DisclosureOfBreakupOfProvisionsTable", "Provisions", "LongTermProvisions", lambda d: short(d).get("ClassificationBasedOnTimePeriodAxis") == "LongTermMember"),
            ("DisclosureOfBreakupOfProvisionsTable", "Provisions", "ShortTermProvisions", lambda d: short(d).get("ClassificationBasedOnTimePeriodAxis") == "ShortTermMember"),
            ("DisclosureOfTangibleAssetsTable", "TangibleAssets", "TangibleAssets", lambda d: list(short(d).values()) == ["OwnedAssetsMember"]),
            ("DisclosureOfIntangibleAssetsTable", "IntangibleAssets", "IntangibleAssets", lambda d: list(short(d).values()) == ["IntangibleAssetsOtherThanInternallyGeneratedMember"]),
        ]
        for tname, item, face, pick in sums:
            tid, fid = self._lid(tname), self._lid(face)
            if not tid or not fid or not self.f.tables.get(tid):
                continue
            for pk in ("CY", "PY"):
                got, want = tsum(tname, item, pick, pk), n(self.v(fid, pk))
                if got is not None and want is not None and got != want:
                    self.r.add("error", "MCA_SUM", f"'{self.schema.tables[tid]['title']}' adds up to {got} but "
                               f"'{self.label(fid)}' is {want} ({pk}).", fid, "T:" + tid, pk, tid)
        # carrying amount = gross - accumulated depreciation, for every class
        for tname, cax, acc in (("DisclosureOfTangibleAssetsTable", "CarryingAmountAccumulatedDepreciationAndGrossCarryingAmountAxis",
                                 "AccumulatedDepreciationAndImpairmentMember"),
                                ("DisclosureOfIntangibleAssetsTable", "CarryingAmountAccumulatedAmortizationAndImpairmentAndGrossCarryingAmountAxis",
                                 "AccumulatedAmortizationAndImpairmentMember")):
            tid = self._lid(tname)
            item = self._lid("TangibleAssets" if "Intangible" not in tname else "IntangibleAssets")
            groups: dict = {}
            for row in self.f.tables.get(tid, []) if tid else []:
                d = short(row["dims"])
                m = d.pop(cax, "CarryingAmountMember")
                groups.setdefault(tuple(sorted(d.items())), {})[m] = row["values"].get(item, {})
            for key, g in groups.items():
                if not {"GrossCarryingAmountMember", acc, "CarryingAmountMember"} <= set(g):
                    continue
                for pk in ("CY", "PY"):
                    a, b, c = (n(g[x].get(pk)) for x in ("GrossCarryingAmountMember", acc, "CarryingAmountMember"))
                    if None not in (a, b, c) and a - b != c:
                        self.r.add("error", "MCA_SUM", f"'{self.schema.tables[tid]['title']}' {dict(key) or 'total'}: carrying amount {c} "
                                   f"should be gross {a} minus accumulated {b} ({pk}).", item, "T:" + tid, pk, tid)

    # mandatory line items per table row, as MCA's tool reports them: (table, elements, periods, member filter)
    MCA_ROW_REQUIRED = [
        ("LoansAndAdvancesTable", ["LoansAndAdvancesDueByFirmsOrCompaniesInWhichAnyDirectorIsPartnerOrDirector"], ("CY", "PY"), None),
        ("SubclassificationOfTradeReceivablesTable", ["TradeReceivablesDueByFirmsOrCompaniesInWhichAnyDirectorIsPartnerOrDirector"], ("CY", "PY"), None),
        ("DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable",
         ["AmountWrittenOffDuringPeriodInRespectOfDebtsDueFromRelatedParties", "AmountWrittenBackDuringPeriodInRespectOfDebtsDueToRelatedParties"], ("ANY",), None),
        ("DisclosureOfClassesOfShareCapitalTable", ["NumberOfSharesOutstanding", "IncreaseDecreaseInNumberOfSharesOutstanding",
                                                    "IncreaseDecreaseInShareCapital"], ("CY", "PY"), r"EquityShares|PreferenceShares"),
        ("DisclosureOfClassesOfShareCapitalTable", ["AmountPerShareCalledInCaseSharesNotFullyCalled"], ("CY", "PY"), r"(Equity|Preference)Shares\d+Member"),
        ("DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable",
         ["DateOfBirthOfKeyManagerialPersonnelOrDirector", "QualificationOfKeyManagerialPersonnelOrDirector", "SharesHeldByKeyManagerialPersonnelOrDirector",
          "GrossSalaryToKeyManagerialPersonnelOrDirector", "KeyManagerialPersonnelOrDirectorRemuneration"], ("CY",), None),
        ("DetailsRegardingAuditorsTable", ["AddressOfAuditors", "PermanentAccountNumberOfAuditorOrAuditorsFirm", "SRNOfFormADT1"], ("CY",), None),
    ]
    # MCA's tool knows these closed lists (the XML schema rejects anything else)
    MCA_ENUMS = {
        "TypeOfNoncurrentInvestments": [
            "Investment in public sector equity instruments", "Investment in subsidiaries equity instruments",
            "Investment in other Indian companies equity instruments", "Investment in public sector preference shares",
            "Investment in subsidiaries preference shares", "Investment in other Indian companies preference shares",
            "Investments in government or trust securities", "Investments in debentures or bonds", "Investments in mutual funds",
            "Investments in partnership firms", "Investment property", "Investment in foreign sources", "Other non-current investments"],
    }

    def check_mca_rows(self):
        if self.f.taxonomy != "AS":
            return
        short = lambda d: {k.split("_", 1)[1]: str(v).split("_", 1)[-1] for k, v in d.items()}  # noqa: E731
        for tname, els, pks, member_rx in self.MCA_ROW_REQUIRED:
            tid = self._lid(tname)
            for i, row in enumerate(self.f.tables.get(tid, []) if tid else []):
                if member_rx and not any(re.search(member_rx, m) for m in short(row["dims"]).values()):
                    continue
                if pks == ("ANY",):
                    # MCA accepted TaxPrint's file with these given for one year of the column only
                    miss = [e for e in els if self._lid(e) and all(_empty(v) for v in row["values"].get(self._lid(e), {}).values())]
                    if miss:
                        self.r.add("error", "MCA_ROW", f"MCA needs {', '.join(self.label(self._lid(e)) for e in miss)} in column {i + 1} "
                                   f"of '{self.schema.tables[tid]['title']}'.", self._lid(miss[0]), "T:" + tid, "CY", tid)
                    continue
                for pk in pks:
                    # a row MCA sees for that year: it has some value for the year (or its closing date)
                    if not any(not _empty(per.get(pk)) for per in row["values"].values()):
                        continue
                    miss = [e for e in els if self._lid(e) and _empty(row["values"].get(self._lid(e), {}).get(pk))]
                    if miss:
                        self.r.add("error", "MCA_ROW", f"MCA needs {', '.join(self.label(self._lid(e)) for e in miss)} in column {i + 1} "
                                   f"of '{self.schema.tables[tid]['title']}' ({pk}).", self._lid(miss[0]), "T:" + tid, pk, tid)
        # previous-year movement for every asset line that has a previous-year balance
        for tname, x in (("DisclosureOfTangibleAssetsTable", "TangibleAssets"), ("DisclosureOfIntangibleAssetsTable", "IntangibleAssets")):
            tid, xid, cid = self._lid(tname), self._lid(x), self._lid("ChangesIn" + x)
            rows = self.f.tables.get(tid, []) if tid else []
            gaps = [r for r in rows if not _empty(r["values"].get(xid, {}).get("PY")) and _empty(r["values"].get(cid, {}).get("PY"))]
            if gaps:
                self.r.add("error", "MCA_PY", f"MCA needs last year's movement (additions, depreciation, disposals) in '{self.schema.tables[tid]['title']}' "
                           f"for {len(gaps)} lines that have a balance on {self.f.py_end}. The documents only give this year's schedule: "
                           "upload last year's AOC-4 XML with the documents and it is taken from there.", cid, "T:" + tid, "PY", tid)
        rp = self._lid("WhetherThereAreAnyRelatedPartyTransactionsDuringYear")
        tid = self._lid("DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable")
        if rp and tid and str(self.v(rp, "PY")).lower() == "true" and not any(
                not _empty(per.get("PY")) for r in self.f.tables.get(tid, []) for per in r["values"].values()):
            self.r.add("error", "MCA_PY", "MCA needs last year's related party transactions because last year is answered Yes. "
                       "They are not in this year's documents: upload last year's AOC-4 XML with the documents.", rp, "T:" + tid, "PY", tid)
        # managerial remuneration remark
        tid = self._lid("DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable")
        mr = self._lid("DisclosureInAuditorsReportRelatingToManagerialRemuneration")
        if tid and mr and self.f.tables.get(tid) and not any(not _empty(r["values"].get(mr, {}).get("CY")) for r in self.f.tables[tid]):
            self.r.add("error", "MCA_ROW", f"MCA needs '{self.label(mr)}' in the auditors' remarks table.", mr, "T:" + tid, "CY", tid)
        # dividend appropriations exist only on the surplus in MCA's hypercube
        tid = self._lid("StatementOfChangesInReservesTable")
        for r in self.f.tables.get(tid, []) if tid else []:
            mem = short(r["dims"]).get("ComponentsOfReservesAxis", "")
            if mem and mem != "SurplusMember":
                bad = [e for e in r["values"] if re.search(r"_(Dividend|DividendTax)Appropriation$", e)]
                if bad:
                    self.r.add("error", "MCA_DIM", f"'{self.label(bad[0])}' is only allowed on the surplus, not on {mem}.", bad[0], "T:" + tid, "CY", tid)
        # closed lists
        for local, allowed in self.MCA_ENUMS.items():
            eid = self._lid(local)
            for tid2, rows in self.f.tables.items():
                for r in rows:
                    for pk, v in r["values"].get(eid, {}).items():
                        if not _empty(v) and v not in allowed:
                            self.r.add("error", "MCA_ENUM", f"'{v}' is not one of MCA's values for '{self.label(eid)}'.", eid, "T:" + tid2, pk, tid2)
        # CSR: the average is the average of the three years; 2% of it is the prescribed amount
        avg_id = self._lid("AverageNetProfitForLastThreeFinancialYears")
        npt = self._lid("DisclosureOfNetProfitsForLastThreeFinancialYearsTable")
        npe = self._lid("NetProfitComputedUnderSection198AndAdjustedAsPerRule21FOfCompaniesCSRPolicyRules2014")
        vals = [to_number(r["values"].get(npe, {}).get("CY")) for r in self.f.tables.get(npt, [])] if npt else []
        vals = [float(v) for v in vals if v is not None]
        avg = to_number(self.v(avg_id, "CY")) if avg_id else None
        avg = float(avg) if avg is not None else None
        if vals and avg is not None and abs(avg - sum(vals) / len(vals)) > 0.01:
            self.r.add("error", "MCA_CSR", f"'{self.label(avg_id)}' is {avg} but the average of the years in the net profits table is "
                       f"{round(sum(vals) / len(vals), 2)}.", avg_id, self.section_of(avg_id), "CY")
        pre = self._lid("PrescribedCSRExpenditure")
        pv = to_number(self.v(pre, "CY")) if pre else None
        pv = float(pv) if pv is not None else None
        if avg is not None and pv is not None and abs(pv - round(avg * 0.02, 2)) > 0.01:
            self.r.add("error", "MCA_CSR", f"'{self.label(pre)}' should be 2% of the average net profit ({round(avg * 0.02, 2)}), not {pv}.",
                       pre, self.section_of(pre), "CY")
        ag, mode = self._lid("DetailsOfImplementingAgency"), self._lid("ModeOfAmountSpent")
        ct = self._lid("ClassificationOfCSRSpendingTable")
        if ag and mode and ct and _empty(self.v(ag, "CY")) and any(
                str(r["values"].get(mode, {}).get("CY", "")).lower() not in ("", "direct") for r in self.f.tables.get(ct, [])):
            self.r.add("error", "MCA_CSR", f"'{self.label(ag)}' is needed because the CSR money was spent through an implementing agency.",
                       ag, self.section_of(ag), "CY")
        # an amount given for this year needs last year as well
        for eid, per in self.f.values.items():
            el = self.schema.element(eid) or {}
            if el.get("section") is None or el.get("noPY") or el.get("type") not in ("monetaryItemType", "sharesItemType", "perShareItemType"):
                continue
            if not _empty(per.get("CY")) and _empty(per.get("PY")):
                self.r.add("error", "MCA_PY", f"'{self.label(eid)}' is given for the current year, so MCA needs the previous year too.",
                           eid, self.section_of(eid), "PY")

    def run(self) -> Result:
        self.check_filing()
        self.check_types()
        self.check_rules()
        self.check_totals()
        self.check_tables()
        self.check_mca()
        self.check_mca_rows()
        BusinessRules(self).run()
        # de-duplicate; an official-rule finding is dropped when another check already reported the same thing
        seen, out = set(), []
        reported = {(i["code"], i["element"], i["period"], i["table"]) for i in self.r.items if i.get("source") != "MCA_BR"}
        for i in self.r.items:
            k = (i["level"], i["code"], i["element"], i["period"], i["message"])
            if i.get("source") == "MCA_BR" and (i["code"], i["element"], i["period"], i["table"]) in reported:
                continue
            if k not in seen:
                seen.add(k)
                out.append(i)
        self.r.items = out
        return self.r


def validate(filing: Filing) -> Result:
    return Validator(filing).run()
