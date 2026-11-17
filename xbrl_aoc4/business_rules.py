"""MCA's official business rules for a taxonomy, applied to a Filing.

The rules come from MCA's business rules workbook (for C&I 2016:
``Final_Business_Rule_CI_Taxonomy_2016_V1.3.xls``), converted by
``tools/extract_business_rules.py`` into ``schema/business_rules_<KEY>.json``.

Three groups of checks:

* element rules ("Specific rules for elements"): mandatory, non-negative,
  dates not after today, valid PAN / CIN / DIN / country, unique values,
  dependencies (mandatory if Yes / greater than zero / entered), comparisons,
  table-mandatory conditions;
* mandatory line items per table, and the member rules (parent and child
  members, members 1..n in sequence), with MCA's exemption lists;
* the generic rules: previous year figures, decimals, presentation currency,
  dates before incorporation, sections that do not apply to the previous
  year, to consolidated or to standalone documents, images in text blocks.

Findings use the validator's codes so that a finding the template rules
already report is not repeated (see ``Validator.run``). Each carries ``ref``,
e.g. "MCA BR 200100", so the user can look the rule up.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from .generator import to_number
from .model import parse_date

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
MONETARY = ("monetaryItemType",)
NUMERIC_PY = ("monetaryItemType", "sharesItemType", "perShareItemType")

CIN_RE = re.compile(r"^[LUFlu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
PAN_RE = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
DIN_RE = re.compile(r"^\d{8}$")

# Generic rule 12: sections with no previous-year data (with the elements that keep it)
NO_PY_SECTIONS = {"400200", "202400", "202500", "202800", "300700", "400300", "202300", "202600", "400500", "301000"}
NO_PY_KEEP = {
    "400100": {"PeriodCoveredByFinancialStatements", "DateOfStartOfReportingPeriod", "DateOfEndOfReportingPeriod"},
    "400400": {"NumberOfSharesHeld", "NumberOfDematSharesHeld", "NumberOfPhysicalSharesHeld", "PercentageOfTotalSharesHeld",
               "PercentageOfSharesPledgedEncumberedToTotalShares", "PrincipalAmount", "InterestDueButNotPaid",
               "InterestAccruedButNotDue", "Indebtedness"},
}
# Generic rule 11 / 13 / 14
NOT_CONSOLIDATED = {"202800", "300700", "400300", "400400", "202300", "202400", "202500", "400500", "301000"}
CONSOLIDATED_GENERAL_INFO = {
    "DisclosureOfCompanyInformationAbstract", "NameOfCompany", "CorporateIdentityNumber", "PermanentAccountNumberOfEntity",
    "AddressOfRegisteredOfficeOfCompany", "TypeOfIndustry", "PeriodCoveredByFinancialStatements", "DateOfStartOfReportingPeriod",
    "DateOfEndOfReportingPeriod", "NatureOfReportStandaloneConsolidated", "ContentOfReport", "DescriptionOfPresentationCurrency",
    "LevelOfRoundingUsedInFinancialStatements", "TypeOfCashFlowStatement"}
NOT_STANDALONE = {"202600"}
# Generic rule 6 does not apply here
NO_PAIRING_SECTIONS = {"201900"}
# Generic rule 3 does not apply to rows that only carry these
MEMBER_RULE_IGNORED_ITEMS = {"DescriptionOfNatureAndPurposeOfOtherReserves", "NatureOfOtherLoansAndAdvances", "NatureOfOtherInventories",
                             "NatureOfOtherTangibleAssets", "NatureOfOtherIntangibleAssets"}
# Generic rule 10: dates that may be before the company was incorporated
BEFORE_INCORPORATION_OK = {
    "EndOfReportingPeriodOfFinancialStatementsOfAssociatesIfEndOfReportingPeriodIsDifferentFromEntitysEndOfReportingPeriod",
    "EndOfReportingPeriodOfFinancialStatementsOfSubsidiaryWhenDifferentFromParent", "StartDateOfAccountingPeriodOfSubsidiary",
    "EndDateOfAccountingPeriodOfSubsidiary", "DateOfBirthOfDirector", "DateOfBirthOfKeyManagerialPersonnelOrDirector",
    "DateToWhichCapitalOfPartnershipFirmRelates", "DateOfBecomingSubsidiary"}
BEFORE_INCORPORATION_OK_RX = re.compile(r"^(Start|End)DateOfAccountingPeriodOfPartnershipFirm|DateOfBirth")


# each line item is given once, against one member ("In case a line item is provided for any member then the
# same line item cannot be provided against any other member"), so the list applies to the table as a whole
WHOLE_TABLE_LINE_ITEMS = {"in-ca_DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable"}


@lru_cache(maxsize=4)
def load_rules(key: str) -> dict | None:
    p = SCHEMA_DIR / f"business_rules_{key}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _local(x: str) -> str:
    return str(x).split("_", 1)[-1]


def _yes(v) -> str:
    s = str(v).strip().lower()
    return {"yes": "true", "no": "false"}.get(s, s)


class BusinessRules:
    def __init__(self, validator):
        self.vd = validator
        self.f = validator.f
        self.s = validator.schema
        self.r = validator.r
        self.rules = load_rules(self.f.taxonomy) or {}
        self.standalone = (self.f.nature or "Standalone") == "Standalone"
        self.today = date.today()
        self._either_done: set = set()
        self.countries = {c.upper() for c in self.rules.get("countries", [])} | {"INDIA"}
        self.first_year = not any(
            not _empty(per.get("PY")) for eid, per in self.f.values.items()
            if (self.s.element(eid) or {}).get("section") in ("100100", "100200"))

    # ------------------------------------------------------------ helpers
    def add(self, code, msg, eid="", pk="", table="", ref="", level="error"):
        sec = ("T:" + table) if table else self.vd.section_of(eid) if eid else ""
        self.r.add(level, code, msg, eid, sec, pk, table)
        self.r.items[-1]["ref"] = ref
        self.r.items[-1]["source"] = "MCA_BR"

    def label(self, eid):
        return self.vd.label(eid)

    def table_of(self, eid) -> str:
        sec = self.vd.section_of(eid)
        if sec.startswith("T:"):
            return sec[2:]
        el = self.s.element(eid) or {}
        if el.get("noCY") and el.get("noPY"):  # a line item that only exists inside a table
            for tid, t in self.s.tables.items():
                if eid in t["item_ids"]:
                    return tid
        return ""

    def section(self, eid) -> str:
        sec = self.vd.section_of(eid)
        if sec.startswith("T:"):
            t = self.s.tables.get(sec[2:], {})
            m = re.search(r"/(\w+)$", t.get("role", ""))
            for s in self.s.sections:
                if s.get("role") == t.get("role"):
                    return s["code"]
            return m.group(1) if m else ""
        return sec

    def rows(self, tid):
        return self.f.tables.get(tid, []) if tid else []

    def needs_py(self, eid) -> bool:
        el = self.s.element(eid) or {}
        if el.get("noPY") or self.first_year or el.get("type") not in NUMERIC_PY:
            return False
        sec = self.section(eid)
        if sec in NO_PY_SECTIONS or sec in NO_PAIRING_SECTIONS:
            return False
        if sec in NO_PY_KEEP and _local(eid) not in NO_PY_KEEP[sec]:
            return False
        return True

    def periods(self, eid, cy_only=False):
        return ("CY", "PY") if not cy_only and self.needs_py(eid) else ("CY",)

    def applies(self, eid) -> bool:
        """False when the element's section does not apply to this kind of document."""
        sec = self.section(eid)
        if not self.standalone:
            if sec in NOT_CONSOLIDATED:
                return False
            if sec == "400100" and _local(eid) not in CONSOLIDATED_GENERAL_INFO:
                return False
        elif sec in NOT_STANDALONE:
            return False
        return True

    def values(self, eid):
        return self.vd.all_values(eid)

    def present(self, eid, pk=None, row=None) -> bool:
        if row is not None:
            per = row["values"].get(eid, {})
            return any(not _empty(v) for p, v in per.items() if pk is None or p == pk)
        return any(not _empty(v) and (pk is None or p == pk) for p, v, _t, _d in self.values(eid))

    def num(self, eid, pk, row=None):
        if row is not None:
            return to_number(row["values"].get(eid, {}).get(pk))
        return to_number(self.vd.v(eid, pk))

    def row_has(self, row, pk=None) -> bool:
        return any(not _empty(v) for per in row["values"].values() for p, v in per.items() if pk is None or p == pk)

    # ------------------------------------------------------------ run
    def run(self):
        if not self.rules:
            return
        for er in self.rules.get("element_rules", []):
            eid = er.get("id")
            if not eid:
                continue
            ref = f"MCA BR {er.get('elr') or ''}".strip()
            for c in er["checks"]:
                try:
                    if er["table"]:
                        self.table_check(eid, c, ref)
                    elif self.s.element(eid) and not self.s.element(eid).get("abstract") and self.applies(eid):
                        getattr(self, "k_" + c["k"], lambda *a: None)(eid, c, ref)
                except Exception as e:  # noqa: BLE001 - one odd rule must not stop the others
                    self.add("RULE", f"MCA rule on '{er['name']}' could not be checked: {e}", eid, ref=ref, level="warning")
        self.line_items()
        self.member_rules()
        self.member_sequence()
        self.generic()

    # ------------------------------------------------------------ element checks
    def k_mandatory(self, eid, c, ref):
        only = c.get("only") or ""
        if only == "standalone" and not self.standalone or only == "consolidated" and self.standalone:
            return
        tid = self.table_of(eid)
        if tid:
            if not self.rows(tid):
                self.add("MANDATORY", f"'{self.label(eid)}' is mandatory, so MCA needs '{self.s.tables[tid]['title']}'.", eid, "CY", tid, ref)
            for i, row in enumerate(self.rows(tid)):
                if self.s.is_detail_column(tid, row["dims"]) and self.row_has(row, "CY") and not self.present(eid, "CY", row):
                    self.add("MANDATORY", f"'{self.label(eid)}' is mandatory in column {i + 1} of '{self.s.tables[tid]['title']}'.",
                             eid, "CY", tid, ref)
            return
        for pk in self.periods(eid, c.get("cy_only")):
            if _empty(self.vd.v(eid, pk)):
                yr = "current" if pk == "CY" else "previous"
                self.add("MANDATORY", f"'{self.label(eid)}' is mandatory ({yr} year).", eid, pk, ref=ref)

    def k_mandatory_if_start_after(self, eid, c, ref):
        fs = parse_date(self.f.fy_start)
        if fs and fs.isoformat() > c["date"]:
            n = len(self.r.items)
            self.k_mandatory(eid, {"cy_only": True}, ref)
            if self.table_of(eid):
                # MCA's tool accepted TaxPrint's file without the principal business activity table, so a missing
                # table line item of this rule is a warning, not an error
                for i in self.r.items[n:]:
                    i["level"] = "warning"

    def _each(self, eid):
        for pk, v, tid, dims in self.values(eid):
            if not _empty(v):
                yield pk, v, tid

    def k_nonneg(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n < 0:
                self.add("NONNEG", f"'{self.label(eid)}' should be greater than or equal to zero (got {v}).", eid, pk, tid, ref)

    def k_positive(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n <= 0:
                self.add("NONNEG", f"'{self.label(eid)}' should be greater than zero (got {v}).", eid, pk, tid, ref)

    def k_max100(self, eid, c, ref):
        # MCA's percentItemType values are fractions (0.25 = 25%), other numbers are plain percentages
        limit = 1 if (self.s.element(eid) or {}).get("type") == "percentItemType" else 100
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n > limit:
                self.add("PERCENT", f"'{self.label(eid)}' should not be more than 100% (got {v}).", eid, pk, tid, ref)

    def k_le_today(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            d = parse_date(v)
            if d and d > self.today:
                self.add("DATE", f"'{self.label(eid)}' cannot be later than today's date.", eid, pk, tid, ref)

    def k_age18(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            d = parse_date(v)
            if d and (self.today.year - d.year - ((self.today.month, self.today.day) < (d.month, d.day))) < 18:
                self.add("DATE", f"'{self.label(eid)}' must be at least 18 years before today.", eid, pk, tid, ref)

    def _pattern(self, eid, rx, code, what, ref):
        for pk, v, tid in self._each(eid):
            if not rx.match(str(v).strip()):
                self.add(code, f"'{self.label(eid)}' must be {what} (got '{v}').", eid, pk, tid, ref)

    def k_pan(self, eid, c, ref):
        self._pattern(eid, PAN_RE, "PAN", "a valid PAN", ref)

    def k_cin(self, eid, c, ref):
        self._pattern(eid, re.compile(CIN_RE.pattern + r"|^F\d{5}$|^[A-Z]{3}-\d{4}$"), "CIN", "a valid CIN", ref)

    def k_din(self, eid, c, ref):
        self._pattern(eid, DIN_RE, "DIN", "a valid 8 digit DIN", ref)

    def k_country(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            if str(v).strip().upper() not in self.countries:
                self.add("COUNTRY", f"'{self.label(eid)}': '{v}' is not in MCA's list of countries.", eid, pk, tid, ref)

    def k_inr(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            if str(v).strip().upper() != "INR":
                self.add("INR", f"'{self.label(eid)}' should be INR (got '{v}').", eid, pk, tid, ref)

    def k_unique(self, eid, c, ref):
        seen = set()
        for pk, v, tid in self._each(eid):
            key = (tid, pk, str(v).strip().upper())
            if key in seen:
                self.add("UNIQUE", f"'{self.label(eid)}' value '{v}' is repeated.", eid, pk, tid, ref)
            seen.add(key)

    def k_diff_company_cin(self, eid, c, ref):
        for pk, v, tid in self._each(eid):
            if self.f.cin and str(v).strip().upper() == self.f.cin.upper():
                self.add("DIFFERENT", f"'{self.label(eid)}' should be different from the company's own CIN.", eid, pk, tid, ref)

    def k_diff_company_pan(self, eid, c, ref):
        own = self.vd.v(self.vd._lid("PermanentAccountNumberOfEntity") or "", "CY")
        for pk, v, tid in self._each(eid):
            if own and str(v).strip().upper() == str(own).strip().upper():
                self.add("DIFFERENT", f"'{self.label(eid)}' should be different from the company's own PAN.", eid, pk, tid, ref)

    def _depends(self, eid, target, trigger, ref, why):
        """eid is mandatory wherever ``trigger(row_or_None, pk)`` is true: row by row when both are in one table."""
        tid, ttid = self.table_of(eid), self.table_of(target)
        if tid and tid == ttid:
            for i, row in enumerate(self.rows(tid)):
                for pk in ("CY", "PY"):
                    if trigger(row, pk) and not self.present(eid, pk, row):
                        self.add("DEPENDENCY", f"'{self.label(eid)}' is mandatory in column {i + 1} of '{self.s.tables[tid]['title']}' "
                                 f"because {why} ({pk}).", eid, pk, tid, ref)
                        break
            return
        for pk in ("CY", "PY"):
            if trigger(None, pk) and not self.present(eid, pk if not tid and self.needs_py(eid) else None):
                self.add("DEPENDENCY", f"'{self.label(eid)}' is mandatory because {why}.", eid, pk, tid, ref)
                return

    def _tval(self, target, row, pk):
        if row is not None:
            return row["values"].get(target, {}).get(pk)
        if self.table_of(target):
            vals = [v for p, v, _t, _d in self.values(target) if p == pk and not _empty(v)]
            return vals[0] if vals else None
        return self.vd.v(target, pk)

    def k_if_value(self, eid, c, ref):
        t, want = c["target"], str(c["value"]).strip().lower()
        if self.table_of(t) and self.table_of(t) != self.table_of(eid):
            hit = any(_yes(v) == want or str(v).strip().lower() == want for _p, v, _t, _d in self.values(t))
            if hit and not self.present(eid):
                self.add("DEPENDENCY", f"'{self.label(eid)}' is mandatory because '{self.label(t)}' is {c['value']}.", eid, "CY", ref=ref)
            return
        self._depends(eid, t, lambda row, pk: pk == "CY" and (lambda v: not _empty(v) and (_yes(v) == want or str(v).strip().lower() == want))(
            self._tval(t, row, pk)), ref, f"'{self.label(t)}' is {'Yes' if want == 'true' else 'No' if want == 'false' else c['value']}")

    def k_if_gt0(self, eid, c, ref):
        t = c["target"]
        self._depends(eid, t, lambda row, pk: (to_number(self._tval(t, row, pk)) or 0) > 0, ref, f"'{self.label(t)}' is greater than zero")

    def k_if_nonzero(self, eid, c, ref):
        t = c["target"]
        self._depends(eid, t, lambda row, pk: (to_number(self._tval(t, row, pk)) or 0) != 0, ref, f"'{self.label(t)}' is not zero")

    def k_if_entered(self, eid, c, ref):
        t = c["target"]
        self._depends(eid, t, lambda row, pk: not _empty(self._tval(t, row, pk)), ref, f"'{self.label(t)}' is entered")
        if c.get("vice_versa"):
            self._depends(t, eid, lambda row, pk: not _empty(self._tval(eid, row, pk)), ref, f"'{self.label(eid)}' is entered")

    def k_if_member(self, eid, c, ref):
        tid = self.table_of(eid)
        for i, row in enumerate(self.rows(tid)):
            if c["member"] in row["dims"].values() and self.row_has(row, "CY") and not self.present(eid, None, row):
                self.add("DEPENDENCY", f"'{self.label(eid)}' is mandatory for {self.s.member_label.get(c['member'], _local(c['member']))} "
                         f"in '{self.s.tables[tid]['title']}'.", eid, "CY", tid, ref)

    def k_either_if_india(self, eid, c, ref):
        tid = self.table_of(eid)
        other = c["other"]
        pair = tuple(sorted((eid, other)))
        if pair in self._either_done:  # both elements carry the same rule
            return
        self._either_done.add(pair)
        eid, other = pair
        for i, row in enumerate(self.rows(tid)):
            country = [v for e, per in row["values"].items() if "Country" in e and "Code" not in e for v in per.values() if not _empty(v)]
            if any(str(v).strip().upper() == "INDIA" for v in country) and not (self.present(eid, None, row) or self.present(other, None, row)):
                self.add("DEPENDENCY", f"Column {i + 1} of '{self.s.tables[tid]['title']}': enter '{self.label(eid)}' or "
                         f"'{self.label(other)}' because the country is India.", eid, "CY", tid, ref)

    def _compare(self, eid, c, ref, op, word):
        t = c["target"]
        tid = self.table_of(eid)
        pairs = []
        if tid and tid == self.table_of(t):
            for row in self.rows(tid):
                for pk in ("CY", "PY"):
                    pairs.append((pk, self.num(eid, pk, row), self.num(t, pk, row), tid))
        elif not tid and not self.table_of(t):
            for pk in ("CY", "PY"):
                a, b = self.vd.v(eid, pk), self.vd.v(t, pk)
                if parse_date(a) and parse_date(b) and not to_number(a):
                    pairs.append((pk, parse_date(a), parse_date(b), ""))
                else:
                    pairs.append((pk, to_number(a), to_number(b), ""))
        for pk, a, b, tb in pairs:
            if a is not None and b is not None and not op(a, b):
                self.add("COMPARE", f"'{self.label(eid)}' ({a}) should be {word} '{self.label(t)}' ({b}).", eid, pk, tb, ref)

    def k_le_element(self, eid, c, ref):
        self._compare(eid, c, ref, lambda a, b: a <= b, "less than or equal to")

    def k_ge_element(self, eid, c, ref):
        self._compare(eid, c, ref, lambda a, b: a >= b, "on or after" if "Date" in eid else "greater than or equal to")

    def k_eq_element(self, eid, c, ref):
        self._compare(eid, c, ref, lambda a, b: a == b, "equal to")

    # ------------------------------------------------------------ table checks
    def table_check(self, tid, c, ref):
        if tid not in self.s.tables:
            return
        t = self.s.tables[tid]
        if self.f.tables.get(tid):
            return
        anchor = next(iter(sorted(t["item_ids"])), "")
        if not self.applies(anchor):
            return
        if c["k"] == "table_always":
            self.add("MCA_TABLE", f"MCA always needs '{t['title']}'.", "", "CY", tid, ref)
        elif c["k"] == "table_if_yes":
            if any(_yes(v) == "true" for p, v, _t, _d in self.values(c["target"]) if p == "CY"):
                self.add("MCA_TABLE", f"MCA needs '{t['title']}' because '{self.label(c['target'])}' is Yes.", "", "CY", tid, ref)
        elif c["k"] == "table_if_amount":
            for pk in ("CY", "PY"):
                for e in c["targets"]:
                    n = to_number(self.vd.v(e, pk)) or 0
                    if n > 0 if c["op"] == "gt0" else n != 0:
                        self.add("MCA_TABLE", f"MCA needs '{t['title']}' because '{self.label(e)}' is not zero ({pk}).", "", pk, tid, ref)
                        return

    # ------------------------------------------------------------ mandatory line items (generic rule 2)
    def line_items(self):
        # line items that are totals of other line items in the table are optional when their components are given
        for li in self.rules.get("mandatory_line_items", []):
            tid = li.get("table")
            if not tid or tid not in self.s.tables or not self.f.tables.get(tid):
                continue
            t = self.s.tables[tid]
            if not self.applies(next(iter(sorted(t["item_ids"])), "")):
                continue
            if li["all_except"]:
                req = [it["id"] for it in t["items"] if not it.get("abstract") and it["id"] not in set(li["items"])
                       and it.get("type") not in ("textBlockItemType",)]
            else:
                req = [e for e in li["items"] if e in t["item_ids"]]
            if not req:
                continue
            ref = f"MCA BR line items #{li['no']}"
            if tid in WHOLE_TABLE_LINE_ITEMS:
                miss = [e for e in req if not any(self.present(e, "CY", row) for row in self.f.tables[tid])]
                if miss:
                    self.add("MCA_ROW", f"MCA needs {', '.join(self.label(e) for e in miss)} in '{t['title']}'.", miss[0], "CY", tid, ref)
                continue
            for i, row in enumerate(self.f.tables[tid]):
                if not self.s.is_detail_column(tid, row["dims"]):
                    continue
                # MCA's tool accepted TaxPrint's file with a line item given for one year of the column only,
                # so an item counts as given when either year has it
                for pk in ("CY",):
                    if not self.row_has(row):
                        continue
                    miss = [e for e in req if not self.present(e, None, row)]
                    if miss:
                        names = ", ".join(self.label(e) for e in miss[:6]) + (f" and {len(miss) - 6} more" if len(miss) > 6 else "")
                        self.add("MCA_ROW", f"MCA needs {names} in column {i + 1} of '{t['title']}' ({pk}).", miss[0], pk, tid, ref)

    # ------------------------------------------------------------ parent / child members (generic rule 3)
    def member_rules(self):
        ex_parent = {(e["table"], e["axis"], e["member"]) for e in self.rules.get("exempt_parent", [])}
        ex_child = {(e["table"], e["axis"], e["parent"], e["member"]) for e in self.rules.get("exempt_child", [])}
        for tid, rows in self.f.tables.items():
            t = self.s.tables.get(tid)
            if not t or not rows:
                continue
            for a in t["axes"]:
                if a.get("typedDomain"):
                    continue
                reportable = {m["id"] for m in a["members"]}
                # the default member is the plain (non-dimensional) fact, and a domain that is not itself a member
                # cannot be reported, so neither is asked for
                parent_of = {m["id"]: m.get("parent") for m in a["members"]
                             if m.get("parent") in reportable and m.get("parent") != a.get("default")}
                children: dict[str, list[str]] = {}
                for m, p in parent_of.items():
                    if p:
                        children.setdefault(p, []).append(m)
                groups: dict = {}
                for row in rows:
                    # the rule is about amounts adding up, so only rows with amounts count
                    useful = any(not _empty(v) for e, per in row["values"].items() if _local(e) not in MEMBER_RULE_IGNORED_ITEMS
                                 and (self.s.element(e) or {}).get("type") in NUMERIC_PY for v in per.values())
                    if not useful:
                        continue
                    other = tuple(sorted((k, v) for k, v in row["dims"].items() if k != a["id"]))
                    groups.setdefault(other, set()).add(row["dims"].get(a["id"]) or a.get("default") or "")
                for other, present in groups.items():
                    for m in sorted(present):
                        p = parent_of.get(m)
                        if p and p not in present and (tid, a["id"], p) not in ex_parent:
                            self.add("MCA_MEMBER", f"'{t['title']}': {self.s.member_label.get(m, _local(m))} is given, so its parent "
                                     f"{self.s.member_label.get(p, _local(p))} is needed too.", "", "CY", tid, "MCA BR generic rule 3")
                        kids = children.get(m)
                        if kids and not present & set(kids) and not all((tid, a["id"], m, k) in ex_child for k in kids) \
                                and m != a.get("default") and m not in {e[2] for e in ex_parent if e[0] == tid}:
                            self.add("MCA_MEMBER", f"'{t['title']}': {self.s.member_label.get(m, _local(m))} is given, so at least "
                                     "one of its sub-members is needed too.", "", "CY", tid, "MCA BR generic rule 3", level="warning")

    # ------------------------------------------------------------ members 1..n (generic rule 4)
    def member_sequence(self):
        for tid, rows in self.f.tables.items():
            t = self.s.tables.get(tid)
            if not t:
                continue
            for a in t["axes"]:
                nums: dict[str, set[int]] = {}
                for row in rows:
                    m = _local(row["dims"].get(a["id"], ""))
                    mm = re.match(r"^(.*?)(\d+)Member$", m)
                    if mm:
                        nums.setdefault(mm.group(1), set()).add(int(mm.group(2)))
                for prefix, got in nums.items():
                    want = set(range(1, max(got) + 1))
                    if got != want:
                        gap = min(want - got)
                        self.add("MCA_SEQUENCE", f"'{t['title']}': {prefix} members must be numbered 1, 2, 3 … in order; "
                                 f"{prefix} {gap} is missing.", "", "CY", tid, "MCA BR generic rule 4")

    # ------------------------------------------------------------ generic rules
    def generic(self):
        cin_year = int(self.f.cin[8:12]) if self.f.cin and CIN_RE.match(self.f.cin) else None
        for eid, per in self.f.values.items():
            el = self.s.element(eid) or {}
            sec = el.get("section") or ""
            name = _local(eid)
            for pk, v in per.items():
                if _empty(v):
                    continue
                # rule 11 / 13 / 14: section not used in this kind of document
                if not self.applies(eid):
                    self.add("MCA_SCOPE", f"'{self.label(eid)}' belongs to [{sec}], which MCA does not accept in a "
                             f"{'consolidated' if not self.standalone else 'standalone'} document.", eid, pk, ref="MCA BR generic rules 11, 13, 14")
                    break
                # rule 12: no previous year in these sections
                if pk == "PY" and (sec in NO_PY_SECTIONS or (sec in NO_PY_KEEP and name not in NO_PY_KEEP[sec])):
                    self.add("MCA_SCOPE", f"'{self.label(eid)}' has a previous-year value, but MCA does not accept previous-year "
                             f"data in [{sec}].", eid, pk, ref="MCA BR generic rule 12")
                self._value_rules(eid, el, pk, v, "", cin_year)
            # rule 6: current and previous year go together
            if el.get("type") in NUMERIC_PY and not self.first_year and sec not in NO_PAIRING_SECTIONS and self.needs_py(eid):
                cy, py = not _empty(per.get("CY")), not _empty(per.get("PY"))
                if py and not cy and not el.get("noCY"):
                    self.add("MCA_PY", f"'{self.label(eid)}' is given for the previous year, so MCA needs the current year too.",
                             eid, "CY", ref="MCA BR generic rule 6")
        for tid, rows in self.f.tables.items():
            for row in rows:
                for eid, per in row["values"].items():
                    el = self.s.element(eid) or {}
                    for pk, v in per.items():
                        if not _empty(v):
                            self._value_rules(eid, el, pk, v, tid, cin_year)

    def _value_rules(self, eid, el, pk, v, tid, cin_year):
        t = el.get("type", "")
        name = _local(eid)
        # rule 8: at most two decimals in amounts
        if t in MONETARY:
            n = to_number(v)
            if n is not None and isinstance(n, Decimal) and n.as_tuple().exponent < -2 and n != n.quantize(Decimal("0.01")):
                self.add("MCA_DECIMALS", f"'{self.label(eid)}' has more than 2 decimal places ({v}).", eid, pk, tid, "MCA BR generic rule 8")
        # rule 10: no dates before the company was incorporated (year taken from the CIN)
        elif t == "dateItemType" and cin_year and name not in BEFORE_INCORPORATION_OK and not BEFORE_INCORPORATION_OK_RX.search(name):
            d = parse_date(v)
            if d and d.year < cin_year:
                self.add("MCA_DATE", f"'{self.label(eid)}' ({v}) is before the company was incorporated ({cin_year}, from the CIN).",
                         eid, pk, tid, "MCA BR generic rule 10")
        # rule 5: no images in the instance
        elif t == "textBlockItemType" and re.search(r"<\s*img\b|data:image/", str(v), re.I):
            self.add("MCA_IMAGE", f"'{self.label(eid)}' contains an image; MCA does not allow images or charts.", eid, pk, tid,
                     "MCA BR generic rule 5")
