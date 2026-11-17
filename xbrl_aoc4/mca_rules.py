"""MCA's official business rules for a taxonomy (schema/mca_rules_<KEY>.json).

The rules file is made from MCA's "Business Rules for <taxonomy>" workbook by
tools/import_business_rules.py. It holds the generic rules, the element rules
(MCA's wording plus machine checks), the mandatory line items of each table,
the parent-child exemptions, the opening/closing balance pairs and the
country and currency lists. ``McaRules(validator).run()`` adds a finding for
each broken rule to the validator's result, coded ``MCA-...``.

Rules that need MCA's own databases (name matches the CIN or DIN, auditor is
on the ICAI list, director is associated with the company) are listed by
``database_rules()`` and cannot be checked offline.
"""
from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal
from functools import lru_cache

from .generator import is_numeric, to_number
from .model import parse_date
from .schema_loader import SCHEMA_DIR

PAN_RE = re.compile(r"^[A-Za-z]{5}\d{4}[A-Za-z]$")
CIN_RE = re.compile(r"^[LUFlu]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}$")
DIN_RE = re.compile(r"^\d{8}$")
PERIOD_NAME = {"CY": "current year", "PY": "previous year", "OPEN": "opening balance sheet of the previous year"}
MONEY_TYPES = ("monetaryItemType",)

# Generic rule 11: extended links that have no previous-year column, with the
# elements that keep one.
NO_PY_SECTIONS = {
    "700400": set(), "700100": set(), "700500": set(), "613400": set(), "700700": set(), "700200": set(),
    "700300": {"PeriodCoveredByFinancialStatements", "DateOfStartOfReportingPeriod", "DateOfEndOfReportingPeriod"},
    "700600": {"NumberOfSharesHeld", "NumberOfDematSharesHeld", "NumberOfPhysicalSharesHeld", "PercentageOfTotalSharesHeld",
               "PercentageOfSharesPledgedEncumberedToTotalShares", "PrincipalAmount", "InterestDueButNotPaid",
               "InterestAccruedButNotDue", "Indebtedness"},
}
NO_PY_TABLES = {"DetailsOfSpecifiedBankNotesHeldAndTransactedDuringThePeriodFrom8November2016To30December2016Table"}
NO_PY_ELEMENTS = {"WhetherTheCompanyHadProvidedRequisiteDisclosuresInItsFinancialStatementsAsToHoldingsAsWellAsDealingsInSpecifiedBankNotesDuringThePeriodFrom8ThNovember2016To30ThDecember2016Explanatory",
                  "WhetherTheseAreInAccordanceWithTheBooksOfAccountsMaintainedByTheCompanyExplanatory"}
# Generic rules 10, 12 and 13: extended links by nature of report.
NOT_IN_CONSOLIDATED = {"700100", "700500", "700600", "700700", "700200"}
CONSOLIDATED_GENERAL_INFO = {
    "DisclosureOfCompanyInformationAbstract", "NameOfCompany", "CorporateIdentityNumber", "PermanentAccountNumberOfEntity",
    "AddressOfRegisteredOfficeOfCompany", "TypeOfIndustry", "PeriodCoveredByFinancialStatements", "DateOfStartOfReportingPeriod",
    "DateOfEndOfReportingPeriod", "NatureOfReportStandaloneConsolidated", "ContentOfReport", "DescriptionOfPresentationCurrency",
    "LevelOfRoundingUsedInFinancialStatements", "TypeOfCashFlowStatement"}
NOT_IN_STANDALONE = {"613400"}
# Generic rule 14: tables with several axes need a member on each axis.
MULTI_AXIS_TABLES = {"DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable", "ClassificationOfBorrowingsTable",
                     "DisclosureOfPropertyPlantAndEquipmentTable", "DisclosureOfDetailedInformationAboutOtherIntangibleAssetsTable",
                     "DetailsOfLoansTable", "DetailsOfAdvancesTable"}
# Asset notes only apply when the balance sheet shows the asset (the same reading
# as MCA's "table is mandatory if <asset> is greater than zero" rules).
NOTE_FACE = {"400600": ["PropertyPlantAndEquipment"], "400700": ["InvestmentProperty"], "400800": ["Goodwill"],
             "400900": ["OtherIntangibleAssets"], "401000": ["BiologicalAssetsOtherThanBearerPlants"],
             "400300": ["BorrowingsNoncurrent", "BorrowingsCurrent"], "400400": ["NoncurrentInvestments"],
             "400500": ["CurrentInvestments"]}
NUMBERED = re.compile(r"^(.*?)(\d+)(\D*Member)$")


@lru_cache(maxsize=4)
def load_rules(key: str) -> dict | None:
    p = SCHEMA_DIR / f"mca_rules_{key}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def has_rules(key: str) -> bool:
    return load_rules(key) is not None


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _yes(v) -> bool:
    return str(v).strip().lower() in ("true", "yes", "y")


def _no(v) -> bool:
    return str(v).strip().lower() in ("false", "no", "n")


def _local(x: str) -> str:
    return str(x).split("_", 1)[-1].split(":")[-1]


class McaRules:
    def __init__(self, validator):
        self.V = validator
        self.f = validator.f
        self.s = validator.schema
        self.data = load_rules(self.s.key) or {}
        self.standalone = (self.f.nature or "Standalone") == "Standalone"
        self.today = date.today()
        self.section_items = {sec["code"]: {it["id"] for it in sec["items"]} for sec in self.s.sections}
        # first financial year (generic rule 5): nothing at all for the previous year
        self.has_py = any("PY" in per for per in self.f.values.values()) or any(
            "PY" in vals for rows in self.f.tables.values() for r in rows for vals in r["values"].values())
        self.countries = {c.strip().upper() for c in self.data.get("countries", [])}
        self.currencies = {c.strip().upper() for c in self.data.get("currencies", [])}

    # ----------------------------------------------------------- helpers
    def add(self, code, msg, eid="", pk="", tid="", level="error"):
        sec = self.V.section_of(eid) if eid else ("T:" + tid if tid else "")
        # missing values keep the codes the page and the zero-fill already understand
        full = code if code in ("MANDATORY", "DEPENDENCY", "TABLE") else "MCA-" + code
        for i in self.V.r.items:
            if eid and i["element"] == eid and i["period"] == pk and i["code"] == full and i.get("table", "") == tid:
                return
        self.V.r.add(level, full, msg, eid, sec, pk, tid)

    def lab(self, eid):
        return self.V.label(eid) if self.s.element(eid) else (self.s.tables[eid]["title"] if eid in self.s.tables else _local(eid))

    def val(self, eid, pk="CY"):
        return self.V.v(eid, pk)

    def tables_of(self, eid, role=""):
        out = [tid for tid, t in self.s.tables.items() if eid in t["item_ids"]]
        if role:
            inrole = [tid for tid in out if self.s.tables[tid]["role"].endswith("_" + role)]
            out = inrole or out
        return out

    def rows(self, tid):
        return self.f.tables.get(tid, [])

    def in_table(self, eid, role):
        """True when the rule is about the element inside a table of this extended link."""
        if eid in self.s.tables:
            return True
        if eid in self.section_items.get(role, set()):
            return False
        return bool(self.tables_of(eid, role))

    def present(self, eid):
        return self.V.has_any(eid)

    def nonzero_periods(self, eid):
        out = []
        for pk, v in self.V.vals.get(eid, {}).items():
            n = to_number(v)
            if n is not None and n != 0:
                out.append(pk)
        for tid, rows in self.f.tables.items():
            for r in rows:
                for pk, v in r["values"].get(eid, {}).items():
                    n = to_number(v)
                    if n is not None and n != 0 and pk not in out:
                        out.append(pk)
        return out

    def any_yes(self, eid):
        return any(_yes(v) for _pk, v, _t, _d in self.V.all_values(eid))

    def any_no(self, eid):
        return any(_no(v) for _pk, v, _t, _d in self.V.all_values(eid))

    def applies(self, scope):
        return scope == "all" or (scope == "standalone") == self.standalone

    def py_applies(self, eid, el=None, tid=""):
        el = el or self.s.element(eid) or {}
        if el.get("noPY") or not self.has_py:
            return False
        sec = el.get("section") or ""
        if sec in NO_PY_SECTIONS and _local(eid) not in NO_PY_SECTIONS[sec]:
            return False
        if _local(eid) in NO_PY_ELEMENTS or _local(tid) in NO_PY_TABLES:
            return False
        return True

    def section_applies(self, code, eid=""):
        if not self.standalone and code in NOT_IN_CONSOLIDATED:
            return False
        if self.standalone and code in NOT_IN_STANDALONE:
            return False
        if not self.standalone and code == "700300" and eid and _local(eid) not in CONSOLIDATED_GENERAL_INFO:
            return False
        return True

    # ----------------------------------------------------------- mandatory
    def require(self, eid, role, why="", periods=None, rows=None):
        """Report eid as missing. ``rows`` limits a table check to those rows."""
        if eid in self.s.tables:
            if not self.rows(eid):
                self.add("TABLE", f"'{self.lab(eid)}' must be filled{why}.", tid=eid)
            return
        el = self.s.element(eid)
        if el is None or el.get("abstract"):
            return
        if self.in_table(eid, role):
            for tid in self.tables_of(eid, role):
                for i, r in enumerate(self.rows(tid) if rows is None else rows):
                    if not any(not _empty(v) for v in r["values"].get(eid, {}).values()):
                        self.add("TABLE", f"'{self.lab(eid)}' is required in column {i + 1} of '{self.lab(tid)}'{why}.",
                                 eid, "CY", tid)
                        break
            return
        if periods is None:
            periods = ["CY"]
            if el.get("type") in MONEY_TYPES and self.py_applies(eid, el):
                periods.append("PY")
        for pk in periods:
            if pk == "CY" and el.get("noCY"):
                continue
            if _empty(self.val(eid, pk)):
                self.add("DEPENDENCY" if why else "MANDATORY", f"'{self.lab(eid)}' is mandatory ({PERIOD_NAME[pk]}){why}.", eid, pk)

    # ----------------------------------------------------------- element rules
    def run_element_rules(self):
        for rule in self.data.get("element_rules", []):
            eid, role = rule["eid"], rule["role"]
            if not rule.get("known") or not self.section_applies(role, eid):
                continue
            for c in rule["checks"]:
                fn = getattr(self, "c_" + c["kind"], None)
                if fn is None:
                    continue
                try:
                    fn(eid, role, c, rule)
                except Exception as e:  # noqa: BLE001 - one rule must never stop the others
                    self.add("RULE", f"MCA rule on '{self.lab(eid)}' could not be checked: {e}", eid, level="warning")

    def c_mandatory(self, eid, role, c, rule):
        if not self.applies(c.get("scope", "all")):
            return
        if self.in_table(eid, role) and eid not in self.s.tables:
            return  # line items inside a table: handled by the table's mandatory line items
        if role in NOTE_FACE and not any(self.nonzero_periods(self._face(f)) for f in NOTE_FACE[role]):
            return
        self.require(eid, role, periods=["CY"] if c.get("cy_only") else None)
        if rule.get("fta") and self.any_yes(self._fta_id()) and self.s.element(eid) and _empty(self.val(eid, "OPEN")):
            self.add("MANDATORY", f"'{self.lab(eid)}' is mandatory in the opening balance sheet (first-time adoption of Ind AS).",
                     eid, "OPEN")

    def _face(self, local):
        return next((e for e in (f"ind-as_{local}", f"in-gaap_{local}") if self.s.element(e)), local)

    def _fta_id(self):
        return next((e for e in self.s.elements if e.endswith("_WhetherCompanyHasAdoptedIndAsFirstTime")), "")

    def c_mandatory_if_yes(self, eid, role, c, rule):
        on = c.get("on")
        if on and self.applies(c.get("scope", "all")) and self.any_yes(on):
            self.require(eid, role, f" because '{self.lab(on)}' is Yes", periods=["CY"])

    def c_mandatory_if_no(self, eid, role, c, rule):
        on = c.get("on")
        if on and self.any_no(on):
            self.require(eid, role, f" because '{self.lab(on)}' is No", periods=["CY"])

    def c_mandatory_if_nonzero(self, eid, role, c, rule):
        if not self.applies(c.get("scope", "all")):
            return
        for on in c.get("on", []):
            pks = self.nonzero_periods(on)
            if not pks:
                continue
            el = self.s.element(eid) or {}
            per = [pk for pk in pks if pk in ("CY", "PY") and (pk == "CY" or el.get("type") in MONEY_TYPES and self.py_applies(eid, el))]
            self.require(eid, role, f" because '{self.lab(on)}' is not zero", periods=per or ["CY"])
            return

    def c_mandatory_if_entered(self, eid, role, c, rule):
        for on in c.get("on", []):
            if c.get("same_row"):
                for tid in self.tables_of(eid, role):
                    rows = [r for r in self.rows(tid) if any(not _empty(v) for v in r["values"].get(on, {}).values())]
                    if rows:
                        self.require(eid, role, f" because '{self.lab(on)}' is entered", rows=rows)
                continue
            if self.present(on) or (on in self.s.tables and self.rows(on)):
                self.require(eid, role, f" because '{self.lab(on)}' is entered", periods=["CY"])
                return

    def c_mandatory_if_value(self, eid, role, c, rule):
        on, want = c.get("on"), str(c.get("value", "")).strip().lower()
        if on and any(str(v).strip().lower() == want for _pk, v, _t, _d in self.V.all_values(on)):
            self.require(eid, role, f" because '{self.lab(on)}' is '{c['value']}'", periods=["CY"])

    def c_mandatory_if_table(self, eid, role, c, rule):
        t = c.get("table")
        if t and self.rows(t):
            self.require(eid, role, f" because '{self.lab(t)}' is filled", periods=["CY"])

    def c_mandatory_if_greater(self, eid, role, c, rule):
        a, b = to_number(self.val(c["a"])), to_number(self.val(c["b"]))
        if a is not None and a > (b or 0):
            self.require(eid, role, f" because '{self.lab(c['a'])}' is more than '{self.lab(c['b'])}'", periods=["CY"])

    def c_mandatory_if_india(self, eid, role, c, rule):
        on = c.get("on")
        for tid in self.tables_of(eid, role) or [""]:
            rows = self.rows(tid) if tid else []
            if tid:
                hit = [r for r in rows if any((str(v).strip().upper() == "INDIA") == c.get("india", True)
                                              for v in r["values"].get(on, {}).values() if not _empty(v))]
                if hit:
                    self.require(eid, role, f" because '{self.lab(on)}' is {'India' if c.get('india', True) else 'not India'}", rows=hit)
            else:
                v = self.val(on)
                if not _empty(v) and (str(v).strip().upper() == "INDIA") == c.get("india", True):
                    self.require(eid, role, periods=["CY"])

    def c_mandatory_in_member(self, eid, role, c, rule):
        member = c["member"]
        stem = member[:-len("Member")] if member.endswith("Member") else member
        for tid in self.tables_of(eid, role):
            hit = [r for r in self.rows(tid) if any(
                _local(m) == member or (c.get("children") and _local(m).startswith(stem)) for m in r["dims"].values() if m)]
            hit = [r for r in hit if any(not _empty(v) for vals in r["values"].values() for v in vals.values())]
            if hit:
                self.require(eid, role, f" for '{member}'", rows=hit)

    def c_one_of(self, eid, role, c, rule):
        group = [eid] + [x for x in c.get("with", []) if x != eid]
        for tid in self.tables_of(eid, role):
            for i, r in enumerate(self.rows(tid)):
                if not any(not _empty(v) for g in group for v in r["values"].get(g, {}).values()):
                    self.add("MANDATORY", f"Enter one of {', '.join(repr(self.lab(g)) for g in group)} in column {i + 1} of '{self.lab(tid)}'.",
                             eid, "CY", tid)

    def c_one_table_if_yes(self, eid, role, c, rule):
        on = c.get("on")
        if on and self.any_yes(on) and not any(self.rows(t) for t in c.get("tables", [])):
            self.add("TABLE", f"Fill at least one of the lease tables because '{self.lab(on)}' is Yes.", eid, "CY", eid)

    def c_holding_if_subsidiary(self, eid, role, c, rule):
        on = c.get("on")
        if on and self.any_yes(on) and not any("holding" in str(v).lower() for _pk, v, _t, _d in self.V.all_values(eid)):
            self.add("RELATED", "Select 'Holding company' or 'Ultimate holding company' for at least one related party, "
                     "because the company is a subsidiary.", eid, "CY")

    def c_cash_flow_type(self, eid, role, c, rule):
        t = str(self.val(eid) or "").lower()
        other = "310000" if "indirect" in t else "320000" if "direct" in t else ""
        if other:
            filled = [e for e in self.section_items.get(other, set()) if not _empty(self.val(e)) and is_numeric(self.s.element(e) or {})]
            if filled:
                self.add("CASHFLOW", f"Type of cash flow statement is '{self.val(eid)}', so the "
                         f"{'direct' if other == '310000' else 'indirect'} cash flow statement [{other}] must be left blank.", eid, "CY")

    # value checks
    def _each(self, eid):
        for pk, v, tid, dims in self.V.all_values(eid):
            if not _empty(v):
                yield pk, v, tid

    def c_nonneg(self, eid, role, c, rule):
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n < 0:
                self.add("NONNEG", f"'{self.lab(eid)}' should be greater than or equal to zero (got {v}).", eid, pk, tid)

    def c_positive(self, eid, role, c, rule):
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n <= 0:
                self.add("POSITIVE", f"'{self.lab(eid)}' should be greater than zero (got {v}).", eid, pk, tid)

    def c_max100(self, eid, role, c, rule):
        for pk, v, tid in self._each(eid):
            n = to_number(v)
            if n is not None and n > 100:
                self.add("PERCENT", f"'{self.lab(eid)}' should not be more than 100% (got {v}).", eid, pk, tid)

    def _pattern(self, eid, rx, what, code):
        for pk, v, tid in self._each(eid):
            if not rx.match(str(v).strip()):
                self.add(code, f"'{self.lab(eid)}' must be {what} (got '{v}').", eid, pk, tid)

    def c_pan(self, eid, role, c, rule):
        self._pattern(eid, PAN_RE, "a valid PAN (e.g. ABCDE1234F)", "PAN")

    def c_cin(self, eid, role, c, rule):
        self._pattern(eid, CIN_RE, "a valid CIN", "CIN")

    def c_din(self, eid, role, c, rule):
        self._pattern(eid, DIN_RE, "a valid 8 digit DIN", "DIN")

    def c_itc4(self, eid, role, c, rule):
        self._pattern(eid, re.compile(r"^\d{4}$"), "a valid 4 digit ITC/NPCS code", "ITC")

    def c_itc8(self, eid, role, c, rule):
        self._pattern(eid, re.compile(r"^\d{8}$"), "a valid 8 digit ITC/NPCS code", "ITC")

    def c_country(self, eid, role, c, rule):
        if self.countries:
            for pk, v, tid in self._each(eid):
                if str(v).strip().upper() not in self.countries:
                    self.add("COUNTRY", f"'{self.lab(eid)}' must be a country from MCA's list (got '{v}').", eid, pk, tid)

    def c_currency(self, eid, role, c, rule):
        if self.currencies:
            for pk, v, tid in self._each(eid):
                if str(v).strip().upper() not in self.currencies:
                    self.add("CURRENCY", f"'{self.lab(eid)}' must be a currency code from MCA's list (got '{v}').", eid, pk, tid)

    def c_not_future(self, eid, role, c, rule):
        for pk, v, tid in self._each(eid):
            d = parse_date(v)
            if d and d > self.today:
                self.add("DATE", f"'{self.lab(eid)}' cannot be later than today.", eid, pk, tid)

    def c_age18(self, eid, role, c, rule):
        t = self.today
        for pk, v, tid in self._each(eid):
            d = parse_date(v)
            if d and (t.year - d.year - ((t.month, t.day) < (d.month, d.day))) < 18:
                self.add("DATE", f"'{self.lab(eid)}' must be at least 18 years before today.", eid, pk, tid)

    def c_unique(self, eid, role, c, rule):
        seen = set()
        for pk, v, tid in self._each(eid):
            k = (tid, pk, str(v).strip().upper())
            if k in seen:
                self.add("UNIQUE", f"'{self.lab(eid)}' value '{v}' is repeated; each must be unique.", eid, pk, tid)
            seen.add(k)

    def c_not_own_cin(self, eid, role, c, rule):
        for pk, v, tid in self._each(eid):
            if self.f.cin and str(v).strip().upper() == self.f.cin.upper():
                self.add("DIFFERENT", f"'{self.lab(eid)}' must be different from the company's own CIN.", eid, pk, tid)

    def c_not_own_pan(self, eid, role, c, rule):
        own = next((self.val(e) for e in self.s.elements if e.endswith("_PermanentAccountNumberOfEntity")), None)
        for pk, v, tid in self._each(eid):
            if own and str(v).strip().upper() == str(own).strip().upper():
                self.add("DIFFERENT", f"'{self.lab(eid)}' must be different from the company's own PAN.", eid, pk, tid)

    def _pairs(self, eid, other, role):
        """(a, b, pk, tid) for eid and other in the same context."""
        if self.in_table(eid, role):
            for tid in self.tables_of(eid, role):
                for r in self.rows(tid):
                    for pk, a in r["values"].get(eid, {}).items():
                        b = r["values"].get(other, {}).get(pk)
                        if b is None and other not in self.s.tables[tid]["item_ids"]:
                            b = self.val(other, pk)
                        yield a, b, pk, tid
        else:
            for pk, a in self.V.vals.get(eid, {}).items():
                yield a, self.val(other, pk), pk, ""

    def _cmp(self, eid, role, other, op, word):
        el = self.s.element(eid) or {}
        for a, b, pk, tid in self._pairs(eid, other, role):
            if _empty(a) or _empty(b):
                continue
            if el.get("type") == "dateItemType":
                x, y = parse_date(a), parse_date(b)
            else:
                x, y = to_number(a), to_number(b)
            if x is not None and y is not None and not op(x, y):
                self.add("COMPARE", f"'{self.lab(eid)}' ({a}) should be {word} '{self.lab(other)}' ({b}).", eid, pk, tid)

    def c_ge(self, eid, role, c, rule):
        self._cmp(eid, role, c["other"], lambda x, y: x >= y, "on or after" if (self.s.element(eid) or {}).get("type") == "dateItemType" else "at least")

    def c_le(self, eid, role, c, rule):
        self._cmp(eid, role, c["other"], lambda x, y: x <= y, "on or before" if (self.s.element(eid) or {}).get("type") == "dateItemType" else "at most")

    def c_eq(self, eid, role, c, rule):
        self._cmp(eid, role, c["other"], lambda x, y: x == y, "equal to")

    def c_carrying(self, eid, role, c, rule):
        net, gross, acc = c["net"], c["gross"], c["acc"]
        for tid in self.tables_of(eid, role):
            groups: dict = {}
            for r in self.rows(tid):
                kind = None
                rest = []
                for a, m in sorted(r["dims"].items()):
                    lm = _local(m or "")
                    if lm in (net, gross, acc):
                        kind = lm
                    else:
                        rest.append((a, m))
                if kind:
                    groups.setdefault(tuple(rest), {})[kind] = r["values"].get(eid, {})
            for g in groups.values():
                if net not in g or gross not in g:
                    continue
                for pk, nv in g[net].items():
                    n, gr = to_number(nv), to_number(g[gross].get(pk))
                    ac = to_number(g.get(acc, {}).get(pk)) or Decimal(0)
                    if n is not None and gr is not None and n != gr - abs(ac):
                        self.add("CARRYING", f"'{self.lab(eid)}': carrying amount {n} should equal gross carrying amount {gr} "
                                 f"less accumulated depreciation/amortisation {abs(ac)} ({PERIOD_NAME.get(pk, pk)}).", eid, pk, tid)

    def c_table_sum(self, eid, role, c, rule):
        el_id, face = c.get("element") or eid, c.get("face") or eid
        for tid in ([c["table"]] if c.get("table") else self.tables_of(el_id, role)):
            rows = self.rows(tid)
            if not rows:
                continue
            for pk in ("CY", "PY"):
                parts = [to_number(r["values"].get(el_id, {}).get(pk)) for r in rows]
                parts = [p for p in parts if p is not None]
                total = to_number(self.val(face, pk))
                if parts and total is not None and sum(parts) != total:
                    self.add("SUM", f"Total of '{self.lab(el_id)}' in '{self.lab(tid)}' is {sum(parts)}, but '{self.lab(face)}' "
                             f"is {total} ({PERIOD_NAME[pk]}).", face, pk, tid)

    def c_sum_equals(self, eid, role, c, rule):
        for pk in ("CY", "PY"):
            parts = [to_number(self.val(p, pk)) for p in c["parts"]]
            total = to_number(self.val(c["face"], pk))
            if total is not None and any(p is not None for p in parts):
                s = sum(p for p in parts if p is not None)
                if s != total:
                    self.add("SUM", f"{' + '.join(repr(self.lab(p)) for p in c['parts'])} is {s}, but '{self.lab(c['face'])}' "
                             f"is {total} ({PERIOD_NAME[pk]}).", eid, pk)

    def c_sum_max(self, eid, role, c, rule):
        for tid in self.tables_of(eid, role):
            for pk in ("CY", "PY"):
                s = sum((to_number(r["values"].get(eid, {}).get(pk)) or Decimal(0)) for r in self.rows(tid))
                if s > c.get("max", 100):
                    self.add("PERCENT", f"'{self.lab(eid)}' adds up to {s}% across shareholders; it cannot exceed 100%.", eid, pk, tid)

    def c_cin_or_pan_if_india(self, eid, role, c, rule):
        for tid in self.tables_of(eid, role):
            items = self.s.tables[tid]["item_ids"]
            country = [i for i in items if "Country" in i]
            ids = [i for i in items if re.search(r"(CIN|PermanentAccountNumber|PANOf)", _local(i)) and "Foreign" not in i]
            for i, r in enumerate(self.rows(tid)):
                india = any(str(v).strip().upper() == "INDIA" for e in country for v in r["values"].get(e, {}).values())
                if india and not any(not _empty(v) for e in ids for v in r["values"].get(e, {}).values()):
                    self.add("CINPAN", f"Column {i + 1} of '{self.lab(tid)}': enter the CIN or PAN, because the country is India.",
                             eid, "CY", tid)

    # ----------------------------------------------------------- generic rules
    def g_parent_child(self):
        """Rule 1: a total that is entered needs at least one component."""
        from .calc import relationships
        exempt = self.data.get("calc_exempt", {})
        abstract_of = {}
        for sec in self.s.sections:
            cur = ""
            for it in sec["items"]:
                if it.get("abstract"):
                    cur = it["name"]
                else:
                    abstract_of.setdefault(it["id"], cur)
        mandatory = {r["eid"] for r in self.data.get("element_rules", []) if any(c["kind"] == "mandatory" for c in r["checks"])}

        def is_exempt(child):
            ab = abstract_of.get(child, "")
            e = exempt.get(ab)
            if e is None:
                hit = [v for v in exempt.values() if not v.get("all") and _local(child) in v.get("children", [])]
                return bool(hit)
            return e.get("all") and _local(child) not in e.get("except", [])

        for tot, kids in relationships(self.s).items():
            el = self.s.element(tot) or {}
            sec = el.get("section") or ""
            if not self.section_applies(sec, tot):
                continue
            kids = [k for k, _w in kids if tot in mandatory or k in mandatory or not is_exempt(k)]
            if not kids:
                continue
            for pk in ("CY", "PY"):
                n = to_number(self.f.values.get(tot, {}).get(pk))
                if n is None or n == 0:
                    continue
                if all(_empty(self.f.values.get(k, {}).get(pk)) for k in kids):
                    self.add("PARENT-CHILD", f"'{self.lab(tot)}' is entered ({PERIOD_NAME[pk]}) but none of its components are; "
                             f"enter at least one of {', '.join(repr(self.lab(k)) for k in kids[:4])}{'…' if len(kids) > 4 else ''}.",
                             tot, pk)

    def g_mandatory_line_items(self):
        """Rule 2: in a used table, every column that has data needs the mandatory line items."""
        for tid, spec in self.data.get("mandatory_line_items", {}).items():
            if tid.startswith("_"):
                continue
            sec = self.s.tables.get(tid, {}).get("role", "").rsplit("_", 1)[-1]
            if not self.section_applies(sec):
                continue
            for i, r in enumerate(self.rows(tid)):
                if not any(not _empty(v) for vals in r["values"].values() for v in vals.values()):
                    continue
                for e in spec["items"]:
                    if not any(not _empty(v) for v in r["values"].get(e, {}).values()):
                        self.add("TABLE", f"'{self.lab(e)}' is a mandatory line item in column {i + 1} of '{self.lab(tid)}'.",
                                 e, "CY", tid)

    def g_sequence(self):
        """Rule 3: numbered members (1, 2, 3 ...) must not skip a number."""
        for tid, rows in self.f.tables.items():
            seen: dict = {}
            for r in rows:
                for a, m in r["dims"].items():
                    mm = NUMBERED.match(_local(m or ""))
                    if mm:
                        seen.setdefault((a, mm.group(1), mm.group(3)), set()).add(int(mm.group(2)))
            for (a, pre, suf), nums in seen.items():
                missing = sorted(set(range(1, max(nums) + 1)) - nums)
                if missing:
                    self.add("SEQUENCE", f"'{self.lab(tid) if tid in self.s.tables else tid}': members must be in order; "
                             f"{pre}{missing[0]}{suf} is missing before {pre}{max(nums)}{suf}.", tid=tid)

    def g_no_images(self):
        """Rule 4."""
        rx = re.compile(r"<img\b|data:image/|<svg\b|<object\b|<embed\b", re.I)
        for eid, per in self.f.values.items():
            for pk, v in per.items():
                if isinstance(v, str) and rx.search(v):
                    self.add("IMAGE", f"'{self.lab(eid)}' contains an image; images, charts and graphs are not allowed.", eid, pk)
        for tid, rows in self.f.tables.items():
            for r in rows:
                for eid, per in r["values"].items():
                    for pk, v in per.items():
                        if isinstance(v, str) and rx.search(v):
                            self.add("IMAGE", f"'{self.lab(eid)}' contains an image; images are not allowed.", eid, pk, tid)

    def g_previous_year(self):
        """Rule 5: a current-year amount needs the previous-year amount (not in tables, not in the first year)."""
        if not self.has_py:
            return
        for eid, per in self.f.values.items():
            el = self.s.element(eid) or {}
            if el.get("type") not in MONEY_TYPES or _empty(per.get("CY")) or not self.py_applies(eid, el):
                continue
            if not self.section_applies(el.get("section") or "", eid):
                continue
            if _empty(self.V.vals.get(eid, {}).get("PY")):
                self.add("PREVIOUS-YEAR", f"'{self.lab(eid)}' has a current-year amount, so the previous-year amount is also required.",
                         eid, "PY")

    def g_opening_closing(self):
        """Rule 6: closing balance = opening balance + change; previous year's closing = this year's opening."""
        for oc in self.data.get("opening_closing", []):
            bal, chg = oc["balance"], oc["change"]
            ctx = [("", self.V.vals.get(bal, {}), self.V.vals.get(chg, {}))]
            for tid in self.tables_of(bal):
                if chg in self.s.tables[tid]["item_ids"]:
                    ctx += [(tid, r["values"].get(bal, {}), r["values"].get(chg, {})) for r in self.rows(tid)]
            for tid, b, c in ctx:
                for pk, prev in (("CY", "PY"), ("PY", "OPEN")):
                    close, open_, ch = to_number(b.get(pk)), to_number(b.get(prev)), to_number(c.get(pk))
                    if close is None or open_ is None or ch is None:
                        continue
                    if open_ + ch != close:
                        self.add("OPEN-CLOSE", f"'{self.lab(bal)}': opening {open_} + change {ch} = {open_ + ch}, "
                                 f"but the closing balance is {close} ({PERIOD_NAME[pk]}).", bal, pk, tid)

    def g_decimals(self):
        """Rule 7: amounts can have at most 2 decimal places."""
        def chk(eid, pk, v, tid=""):
            el = self.s.element(eid) or {}
            if el.get("type") in MONEY_TYPES and not _empty(v):
                n = to_number(v)
                if n is not None and n != n.quantize(Decimal("0.01")):
                    self.add("DECIMALS", f"'{self.lab(eid)}' has more than 2 decimal places ({v}).", eid, pk, tid)
        for eid, per in self.f.values.items():
            for pk, v in per.items():
                chk(eid, pk, v)
        for tid, rows in self.f.tables.items():
            for r in rows:
                for eid, per in r["values"].items():
                    for pk, v in per.items():
                        chk(eid, pk, v, tid)

    def g_nature_of_report(self):
        """Rules 10, 12 and 13: some extended links don't apply to standalone or consolidated reports."""
        for eid, per in self.f.values.items():
            el = self.s.element(eid) or {}
            sec = el.get("section") or ""
            if any(not _empty(v) for v in per.values()) and not self.section_applies(sec, eid):
                self.add("NATURE", f"'{self.lab(eid)}' [{sec}] does not apply to a {'standalone' if self.standalone else 'consolidated'} "
                         "report; leave it blank.", eid, "CY", level="warning")
        for tid, rows in self.f.tables.items():
            sec = self.s.tables.get(tid, {}).get("role", "").rsplit("_", 1)[-1]
            if rows and not self.section_applies(sec):
                self.add("NATURE", f"'{self.lab(tid)}' [{sec}] does not apply to a {'standalone' if self.standalone else 'consolidated'} "
                         "report; leave it blank.", tid=tid, level="warning")

    def g_no_previous_year(self):
        """Rule 11: some extended links have no previous-year figures."""
        for eid, per in self.f.values.items():
            el = self.s.element(eid) or {}
            if not _empty(per.get("PY")) and self.has_py is not None and not self._py_allowed(eid, el):
                self.add("NO-PY", f"'{self.lab(eid)}' has no previous-year figure in MCA's rules; remove the previous-year value.",
                         eid, "PY", level="warning")
        for tid, rows in self.f.tables.items():
            sec = self.s.tables.get(tid, {}).get("role", "").rsplit("_", 1)[-1]
            for r in rows:
                for eid, vals in r["values"].items():
                    if not _empty(vals.get("PY")) and ((sec in NO_PY_SECTIONS and _local(eid) not in NO_PY_SECTIONS[sec]) or _local(tid) in NO_PY_TABLES):
                        self.add("NO-PY", f"'{self.lab(eid)}' in '{self.lab(tid)}' has no previous-year figure in MCA's rules.",
                                 eid, "PY", tid, level="warning")
                        break

    def _py_allowed(self, eid, el):
        sec = el.get("section") or ""
        if sec in NO_PY_SECTIONS and _local(eid) not in NO_PY_SECTIONS[sec]:
            return False
        return _local(eid) not in NO_PY_ELEMENTS

    def g_multi_axis(self):
        """Rule 14: listed tables need a member on every axis."""
        for tid, rows in self.f.tables.items():
            t = self.s.tables.get(tid)
            if not t or _local(tid) not in MULTI_AXIS_TABLES:
                continue
            for i, r in enumerate(rows):
                missing = [a["label"] for a in t["axes"] if _empty(r["dims"].get(a["id"]))]
                if missing and any(not _empty(v) for vals in r["values"].values() for v in vals.values()):
                    self.add("AXIS", f"Column {i + 1} of '{t['title']}': choose a member for {', '.join(missing)}.", tid=tid)

    def g_first_time_adoption(self):
        """Rule 16: first-time adopters give the opening balance sheet of the previous year."""
        fta = self._fta_id()
        if not fta or not any(_yes(v) for v in [self.val(fta, "CY")] if v is not None):
            return
        for eid in self.section_items.get("110000", set()):
            el = self.s.element(eid) or {}
            if el.get("type") not in MONEY_TYPES:
                continue
            entered = any(not _empty(self.V.vals.get(eid, {}).get(pk)) for pk in ("CY", "PY"))
            if entered and _empty(self.V.vals.get(eid, {}).get("OPEN")):
                self.add("FTA", f"'{self.lab(eid)}' needs a value in the opening balance sheet of the previous year "
                         "(first-time adoption of Ind AS).", eid, "OPEN")

    # ----------------------------------------------------------- run
    def run(self):
        if not self.data:
            return
        self.run_element_rules()
        self.g_parent_child()
        self.g_mandatory_line_items()
        self.g_sequence()
        self.g_no_images()
        self.g_previous_year()
        self.g_opening_closing()
        self.g_decimals()
        self.g_nature_of_report()
        self.g_no_previous_year()
        self.g_multi_axis()
        self.g_first_time_adoption()


def output_filter(filing, schema):
    """For the generator: a function (eid, pk, table_id) -> True when MCA's generic rules
    10-13 say the fact must not be in the instance (an extended link that doesn't apply to
    this nature of report, or a previous-year figure where MCA has none). None when the
    taxonomy has no imported rules."""
    if not has_rules(schema.key):
        return None
    standalone = (filing.nature or "Standalone") == "Standalone"

    def code_of(eid, tid):
        if tid:
            return schema.tables.get(tid, {}).get("role", "").rsplit("_", 1)[-1]
        return (schema.element(eid) or {}).get("section") or ""

    def drop(eid, pk, tid=""):
        sec = code_of(eid, tid)
        if not standalone and (sec in NOT_IN_CONSOLIDATED or (sec == "700300" and not tid and _local(eid) not in CONSOLIDATED_GENERAL_INFO)):
            return True
        if standalone and sec in NOT_IN_STANDALONE:
            return True
        if pk == "PY":
            if sec in NO_PY_SECTIONS and _local(eid) not in NO_PY_SECTIONS[sec]:
                return True
            if _local(eid) in NO_PY_ELEMENTS or _local(tid) in NO_PY_TABLES:
                return True
        return False
    return drop


def database_rules(key: str) -> list[dict]:
    """Rules that MCA checks against its own records (CIN/DIN names, ICAI/ICSI lists)."""
    data = load_rules(key) or {}
    return [r for r in data.get("element_rules", []) if any(c["kind"] == "mca_database" for c in r["checks"])]


def summary(key: str) -> dict:
    data = load_rules(key)
    if not data:
        return {}
    er = data["element_rules"]
    return {"source": data["source"], "element_rules": len(er),
            "checked": sum(1 for r in er if any(c["kind"] != "mca_database" for c in r["checks"])),
            "database": len(database_rules(key)), "generic": len(data["generic"])}
