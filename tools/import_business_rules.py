"""Turn MCA's "Business Rules for <taxonomy>" workbook into a rules file the app reads.

    python -m tools.import_business_rules "Business Rules for IndAS Taxonomy V1.2.xlsx" INDAS

writes ``xbrl_aoc4/schema/mca_rules_<KEY>.json``. The workbook is the official
MCA list (sheets "Generic rules", "Specific rules for elements", "Mandatory
Line Items", "Exempt parent member Dimension", "Exempt Child Member Dimension",
"formulas Linkbase", "Country Codes", "Currency Code", "Parent Child Exempt
Calculation"). Each element rule keeps MCA's own wording and is also turned
into machine checks (``checks``) by matching the wording; rules whose wording
is not understood are kept with ``checks: []`` and listed by ``--report``.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xbrl_aoc4.schema_loader import SCHEMA_DIR, load_schema  # noqa: E402

CAMEL = re.compile(r"\b([A-Z][a-z0-9]+(?:[A-Z0-9][A-Za-z0-9]*){1,})\b")
QUOTED = re.compile(r"[\"'‘’“”]([^\"'‘’“”]{3,})[\"'‘’“”]")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


class Resolver:
    """Find the schema id (prefix_Name) for a bare name or an MCA label."""

    def __init__(self, schema, labels: dict[str, str]):
        self.schema = schema
        self.by_name: dict[str, str] = {}
        for eid in list(schema.elements) + list(schema.tables):
            n = eid.split("_", 1)[1]
            # ind-as wins over in-ca for the same local name
            if n not in self.by_name or eid.startswith("ind-as_"):
                self.by_name[n] = eid
        self.by_label: dict[str, str] = {}
        for eid, el in schema.elements.items():
            self.by_label.setdefault(norm(el.get("label")), eid)
        for name, lab in labels.items():   # MCA's own labels from the rules sheet
            if name in self.by_name:
                self.by_label.setdefault(norm(lab), self.by_name[name])
        self.names = labels

    def name(self, n: str) -> str | None:
        n = (n or "").strip().split(":")[-1].split("_")[-1] if ":" in (n or "") else (n or "").strip()
        if n in self.by_name:
            return self.by_name[n]
        if not hasattr(self, "_low"):
            self._low = {k.lower(): v for k, v in self.by_name.items()}
            self._nothe = {re.sub(r"the", "", k.lower()): v for k, v in self.by_name.items()}
        return self._low.get(n.lower()) or (self._nothe.get(re.sub(r"the", "", n.lower())) if len(n) > 25 else None)

    def ref(self, s: str) -> str | None:
        """A quoted string in a rule: an element name or one of its labels."""
        s = s.strip().strip(".").strip()
        return self.name(s) or self.by_label.get(norm(s)) or self._label_prefix(norm(s))

    def _label_prefix(self, n: str) -> str | None:
        if len(n) < 12:
            return None
        hits = [eid for lab, eid in self.by_label.items() if lab.startswith(n) or n.startswith(lab) and len(lab) > 12]
        return hits[0] if len(hits) == 1 else None

    def refs_in(self, text: str, exclude: str = "") -> list[str]:
        out = []
        for m in QUOTED.finditer(text):
            e = self.ref(m.group(1))
            if e and e != exclude and e not in out:
                out.append(e)
        for m in CAMEL.finditer(text):
            e = self.name(m.group(1))
            if e and e != exclude and e not in out:
                out.append(e)
        return out


# ---------------------------------------------------------------- classify
def sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.replace("\n", ". "))
    # MCA's typo "Mandatory table is "yes" is selected" means "if"
    text = re.sub(r"\b(table|mandatory) is (?=[\"'“]*yes)", r"\1 if ", text, flags=re.I)
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z(\"'‘“•])", text)
    return [p.strip(" .;") for p in parts if p.strip(" .;")]


NONNEG = re.compile(r"(greater than|grater than)\s*(or\s*)?(equal|eqaul)\s*(to\s*)?(0|zero)|equal to or greater than (0|zero)|"
                    r"greater than equal to (0|zero)|non[- ]?negative|shall be greater than or equal to zero", re.I)
COND = re.compile(r"\b(if|in case|when|where|based on|unless)\b", re.I)


SCOPE = re.compile(r"\s*(only\s+)?(in|for)\s+(the\s+)?case\s+of\s+(the\s+)?(stand ?alone|consolidated|current financial year)"
                   r"( financial statements?| financial year)?( and stand ?alone)?( instance document| intance document)?( only)?"
                   r"|\s*for current year|\s*in case of current financial year", re.I)
DATABASE = re.compile(r"based on (cin|din|pan|\"?'?cin|director|\w*cin)|as per (icai|icsi) database|associated with the company|"
                      r"valid (srn|membership|cop)|should be based on", re.I)


def classify(text: str, eid: str, R: Resolver, is_table: bool) -> list[dict]:
    checks: list[dict] = []
    add = checks.append
    local = eid.split("_", 1)[-1]
    for s in sentences(text):
        low = s.lower()
        refs = R.refs_in(s, exclude=eid)
        if DATABASE.search(s):
            add({"kind": "mca_database"})
        if re.search(r"should be greater than (0|zero)\b(?! or)", low):
            add({"kind": "positive"})
        if re.search(r"not (be )?greater than 100|less than or equal to 100", low):
            add({"kind": "max100"})
        if re.search(r"greater than or equal to 18 years|18 years", low):
            add({"kind": "age18"})
        m = re.search(r"summation of value entered in all dimensional members of (\w+Table) for\s+\W*(\w+)\W*\s+should be equal to\s+\W*(\w+)", s, re.I)
        if m:
            add({"kind": "table_sum", "table": R.name(m.group(1)), "element": R.name(m.group(2)), "face": R.name(m.group(3))})
            continue
        m = re.search(r"summation of '?(\w+)'? for all dimensional members should be equal to '?(\w+)'?", s, re.I)
        if m:
            add({"kind": "table_sum", "table": None, "element": R.name(m.group(1)), "face": R.name(m.group(2))})
            continue
        m = re.search(r"summation of '(\w+)' and '(\w+)' should be equal to '(\w+)'", s, re.I)
        if m and all(R.name(g) for g in m.groups()):
            add({"kind": "sum_equals", "parts": [R.name(m.group(1)), R.name(m.group(2))], "face": R.name(m.group(3))})
            continue
        m = re.search(r"should be equal to value entered in element '(\w+)'", s, re.I)
        if m and R.name(m.group(1)):
            add({"kind": "eq", "other": R.name(m.group(1))})
        if NONNEG.search(s) and "less than" not in low and "summation" not in low:
            add({"kind": "nonneg"})
        if re.search(r"valid pan|pan as per|income ?tax format|income_tax_pan", low):
            add({"kind": "pan"})
        if re.search(r"\bvalid cin\b|should be a valid cin", low):
            add({"kind": "cin"})
        if re.search(r"\bvalid din\b", low):
            add({"kind": "din"})
        if re.search(r"valid country|list of countr", low):
            add({"kind": "country"})
        if re.search(r"currency code", low):
            add({"kind": "currency"})
        if re.search(r"4 digit .*itc|itc4", low):
            add({"kind": "itc4"})
        if re.search(r"8 digit .*itc|itc8", low):
            add({"kind": "itc8"})
        if re.search(r"\bunique\b", low):
            add({"kind": "unique"})
        if re.search(r"different from (the )?cin of (the )?filing company|different from cin", low):
            add({"kind": "not_own_cin"})
        if re.search(r"different from (the )?pan of (the )?filing company", low):
            add({"kind": "not_own_pan"})
        if re.search(r"less than or equal to (the )?system date|not be (a )?future date|less than or equal to current date", low):
            add({"kind": "not_future"})
        m = re.search(r"greater than or equal to\s+(?!zero|0\b)(.+)", s, re.I)
        if m and "zero" not in m.group(1).lower()[:6]:
            r = R.refs_in(m.group(1), exclude=eid)
            if r:
                add({"kind": "ge", "other": r[0]})
        m = re.search(r"less than or equal to\s+(?!system|current)(.+)", s, re.I)
        if m and "summation" not in low:
            r = R.refs_in(m.group(1), exclude=eid)
            if r:
                add({"kind": "le", "other": r[0]})
        if re.search(r"carryingamountmember should be equal to", low):
            acc = "AccumulatedAmortizationAndImpairmentMember" if "amortization" in low else "AccumulatedDepreciationAndImpairmentMember"
            add({"kind": "carrying", "gross": "GrossCarryingAmountMember", "acc": acc, "net": "CarryingAmountMember"})
        if re.search(r"summation of all shareholder.*100", low):
            add({"kind": "sum_max", "max": 100})
        if re.search(r"either .*(cin|pan).*(cin|pan).*mandatory.*india|either .*mandatory if country", low):
            r = [x for x in R.refs_in(s) if re.search(r"(CIN|PermanentAccountNumber|PAN)", x)]
            add({"kind": "cin_or_pan_if_india", "any": r})
        # ---------------- mandatory
        if "mandatory" not in low:
            continue
        if re.search(r"not (be )?mandatory|optional", low) and "one of these" not in low:
            continue
        if "one of these two elements" in low or re.search(r"one of the (two|following)", low):
            add({"kind": "one_of", "with": refs})
            continue
        cy_only = bool(re.search(r"current (financial )?year", low))
        scope = "standalone" if re.search(r"stand ?alone", low) else "consolidated" if "consolidated" in low and "table" not in low else "all"
        s2 = SCOPE.sub("", s)
        mm = re.search(r"(?:mandatory|in case)[^\"'“]*(?:of|if|for)\s+(?:the\s+)?(?:member\s+)?[\"'“]?(\w+Member)\b", s) or \
            re.search(r"[\"'“](\w+Member)[\"'”]\s+(?:or its child members\s+)?(?:is|are)\s+(?:used|selected|entered)", s) or \
            re.search(r"in case details in respect of '(\w+Member)'", s)
        if mm:
            add({"kind": "mandatory_in_member", "member": mm.group(1), "children": "child" in low})
            continue
        if not COND.search(s2):
            add({"kind": "mandatory", "what": "table" if local.endswith("Table") else "element", "scope": scope, "cy_only": cy_only})
            continue
        what = "table" if local.endswith("Table") else "element"
        if re.search(r"first ?time adoption|whether is this first time adoption", low):
            add({"kind": "mandatory_if_yes", "on": R.name("WhetherCompanyHasAdoptedIndAsFirstTime"), "what": what, "scope": scope})
            continue
        if not COND.search(s) or re.match(r"^(this|it) is (a |an )?mandatory (field|item|table|element)( in case of standalone instance document)?\W*$", low):
            add({"kind": "mandatory", "what": what, "scope": scope})
            continue
        # conditional mandatory
        if re.search(r"\byes\b", low) and refs:
            on = next((r for r in refs if "Whether" in r), refs[0])
            add({"kind": "mandatory_if_yes", "on": on, "what": what, "scope": scope})
        elif re.search(r"\bno\b is selected|'no' is selected|\"no\" is selected", low) and refs:
            add({"kind": "mandatory_if_no", "on": refs[0], "what": what, "scope": scope})
        elif re.search(r"(other than|greater than|more than|not equal to)\s*(0|zero)|is not zero|non[- ]zero|> ?0", low) and refs:
            add({"kind": "mandatory_if_nonzero", "on": refs, "what": what, "scope": scope})
        elif re.search(r"\b(tagged|entered|provided|reported|filled|used|selected)\b", low) and refs:
            sel = re.search(r"['\"‘“]([^'\"’”]+)['\"’”] is selected in", s)
            if sel and refs:
                add({"kind": "mandatory_if_value", "on": refs[-1], "value": sel.group(1), "what": what, "scope": scope})
            else:
                add({"kind": "mandatory_if_entered", "on": refs, "what": what, "scope": scope})
        elif re.search(r"in case of\s+'?(\w+Member)", s):
            add({"kind": "mandatory_in_member", "member": re.search(r"in case of\s+'?(\w+Member)", s).group(1)})
        elif re.search(r"india", low) and refs:
            add({"kind": "mandatory_if_india", "on": refs[0], "india": "other than" not in low})
    # "Mandatory if X. Mandatory for current year only" is one conditional rule, not two
    if any(c["kind"].startswith("mandatory_if") for c in checks):
        cy = any(c["kind"] == "mandatory" and c.get("cy_only") for c in checks)
        checks = [c for c in checks if c["kind"] != "mandatory"]
        for c in checks:
            if cy and c["kind"].startswith("mandatory_if"):
                c["cy_only"] = True
    # one finding per kind+target is enough
    seen, out = set(), []
    for c in checks:
        k = json.dumps(c, sort_keys=True)
        if k not in seen:
            seen.add(k)
            out.append(c)
    return out


# Rules whose wording names fields by description rather than by element name.
OVERRIDES = {
    "ind-as_TaxExpenseOfDiscontinuedOperations": [{"kind": "mandatory_if_entered", "on": ["ind-as_ProfitLossFromDiscontinuedOperationsBeforeTax"], "what": "element", "scope": "all"}],
    "ind-as_OutstandingAmountOfContinuingDefaultPrincipal": [{"kind": "mandatory_if_entered", "on": ["ind-as_BeginningDateOfContinuingDefaultForBorrowings"], "what": "element", "scope": "all", "same_row": True}],
    "ind-as_OutstandingAmountOfContinuingDefaultInterest": [{"kind": "mandatory_if_entered", "on": ["ind-as_BeginningDateOfContinuingDefaultForBorrowings"], "what": "element", "scope": "all", "same_row": True}],
    "ind-as_MethodUsedToAccountForInvestmentsInSubsidiaries": [{"kind": "mandatory_if_table", "table": "ind-as_DisclosureOfDetailsOfSubsidiariesTable"}],
    "ind-as_MethodUsedToAccountForInvestmentsInJointVentures": [{"kind": "mandatory_if_table", "table": "ind-as_DisclosureOfJointVenturesTable"}],
    "ind-as_MethodUsedToAccountForInvestmentsInAssociates": [{"kind": "mandatory_if_table", "table": "ind-as_DisclosureOfSignificantInvestmentsInAssociatesTable"}],
    "in-ca_ReasonWhyAssociateIsNotConsolidated": [{"kind": "mandatory_if_no", "on": "in-ca_WhetherAssociateHasBeenConsideredInConsolidation", "what": "element", "scope": "all"}],
    "ind-as_DescriptionOfOtherContingentLiabilitiesOthers": [{"kind": "mandatory_in_member", "member": "OtherContingentLiabilitiesOthersMember", "children": False}],
    "ind-as_DisclosureOfRecognisedFinanceLeaseAsAssetsByLesseeTable": [{"kind": "one_table_if_yes", "on": "ind-as_WhetherCompanyHasEnteredIntoAnyLeaseAgreement",
                                                                         "tables": ["ind-as_DisclosureOfRecognisedFinanceLeaseAsAssetsByLesseeTable", "ind-as_DisclosureOfFinanceLeaseAndOperatingLeaseByLesseeTable", "ind-as_DisclosureOfFinanceLeaseAndOperatingLeaseByLessorTable"]}],
    "ind-as_DisclosureOfFinanceLeaseAndOperatingLeaseByLesseeTable": [],
    "ind-as_DisclosureOfFinanceLeaseAndOperatingLeaseByLessorTable": [],
    "in-ca_DetailsOfMaterialContractsArrangementsTransactionsAtArmsLengthBasisTable": [{"kind": "mandatory_if_yes", "on": "in-ca_WhetherThereAreMaterialContractsArrangementsTransactionsAtArmsLengthBasis", "what": "table", "scope": "all"}],
    "in-ca_ApplicableSection": [{"kind": "mandatory_if_entered", "on": ["in-ca_CINOfCompany"], "what": "element", "scope": "all", "same_row": True}],
    "in-ca_NameOfSecretarialAuditFirm": [{"kind": "mandatory_if_value", "on": "in-ca_CategoryOfSecretarialAuditor", "value": "Secretarial auditors firm", "what": "element", "scope": "all"}],
    "in-ca_FirmsRegistrationNumberOfSecretarialAuditFirm": [{"kind": "mandatory_if_value", "on": "in-ca_CategoryOfSecretarialAuditor", "value": "Secretarial auditors firm", "what": "element", "scope": "all"}],
    "ind-as_DisclosureOfFairValueMeasurementOfLiabilitiesTable": [{"kind": "mandatory_if_yes", "on": "ind-as_WhetherLiabilitiesHaveBeenMeasuredAtFairValue", "what": "table", "scope": "all"}],
    "ind-as_AmountUnspentCSR": [{"kind": "mandatory_if_greater", "a": "in-ca_PrescribedCSRExpenditure", "b": "in-ca_AmountCSRSpentForFinancialYear"}],
    "in-ca_TypeOfCashFlowStatement": [{"kind": "cash_flow_type"}],
    "in-ca_DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportLineItems": [{"kind": "line_item_one_member", "table": "in-ca_DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable"}],
    "ind-as_DescriptionOfNatureOfRelatedPartyRelationship": [{"kind": "holding_if_subsidiary", "on": "in-ca_WhetherCompanyIsSubsidiaryCompany"}],
}


# ---------------------------------------------------------------- sheets
def rows(ws):
    for r in ws.iter_rows(values_only=True):
        yield [("" if v is None else str(v).strip()) for v in r]


def read_element_rules(ws, R: Resolver, schema):
    role = code = ""
    out = []
    in_table = False
    for r in rows(ws):
        if r[0] == "LinkRole":
            role = r[1]
            code = role.rsplit("_", 1)[-1]
            in_table = False
            continue
        if r[0] in ("", "prefix", "Definition") or len(r) < 8:
            continue
        name = r[1]
        if name.endswith("Table"):
            in_table = True
        elif name.endswith("Abstract") and not name.endswith("LineItemsAbstract") and in_table and not r[3]:
            pass
        text = r[5]
        if not text:
            continue
        eid = R.name(name) or f"{r[0]}_{name}"
        el = schema.element(eid)
        is_table = name.endswith("Table") or (el is not None and el.get("section") is None) or in_table
        out.append({
            "role": code, "eid": eid, "label": r[2], "text": text, "status": r[7],
            "fta": r[8].upper().startswith("Y") if len(r) > 8 else False,
            "known": el is not None or eid in schema.tables,
            "checks": _fix(OVERRIDES[eid], R) if eid in OVERRIDES else classify(text, eid, R, is_table),
        })
    return out


def _fix(checks, R):
    """Override ids are written with their usual prefix; correct it from the schema."""
    def f(x):
        if isinstance(x, str) and "_" in x and x.split("_", 1)[0] in ("ind-as", "in-ca", "in-gaap"):
            return R.name(x.split("_", 1)[1]) or x
        if isinstance(x, list):
            return [f(i) for i in x]
        return x
    return [{k: f(v) for k, v in c.items()} for c in checks]


def split_names(text: str, R: Resolver) -> list[str]:
    out = []
    for m in CAMEL.finditer(text):
        e = R.name(m.group(1))
        if e and e not in out:
            out.append(e)
    return out


def read_mandatory_line_items(ws, R: Resolver, schema):
    out = {}
    for r in rows(ws):
        if len(r) < 3 or not r[1] or r[1] in ("Table Name",) or r[1].startswith("Mandatory elements"):
            continue
        tname = re.match(r"\s*'?(\w+)", r[1]).group(1)
        tid = R.name(tname) or R.name(tname + "Table")
        if not tid or tid not in schema.tables:
            out.setdefault("_unknown", []).append(r[1])
            continue
        text = r[2]
        if re.search(r"all (line items|elements) are mandatory except", text, re.I):
            exc = split_names(re.split(r"except", text, flags=re.I)[1], R)
            items = [i["id"] for i in schema.tables[tid]["items"] if not i.get("abstract") and i["id"] not in exc]
            out[tid] = {"items": items, "except": exc, "text": text}
        else:
            out[tid] = {"items": [e for e in split_names(text, R) if e in schema.tables[tid]["item_ids"]], "text": text}
    return out


def read_member_exemptions(ws, child: bool):
    out = []
    table = axis = parent = ""
    for r in rows(ws):
        r = (r + [""] * 6)[:6]
        if r[1] in ("Name of Table",) or r[2] in ("Name of Axis",):
            continue
        if r[1]:
            table, axis = r[1], r[2]
        if child:
            if r[3]:
                parent = r[3]
            if r[4] and table:
                out.append({"table": table, "axis": axis, "parent": parent, "child": r[4]})
        elif r[3] and table:
            out.append({"table": table, "axis": axis, "member": r[3]})
    return out


def read_calc_exempt(ws):
    out = {}
    for r in rows(ws):
        if len(r) < 2 or not r[0] or r[0] == "Abstract's name":
            continue
        t = r[1]
        if re.search(r"all child elements are exempt", t, re.I):
            out[r[0]] = {"all": True, "except": []}
        elif re.search(r"except", t, re.I):
            out[r[0]] = {"all": True, "except": CAMEL.findall(re.split(r"except", t, flags=re.I)[1])}
        else:
            out[r[0]] = {"all": False, "children": CAMEL.findall(t.split(":", 1)[-1])}
    return out


def read_opening_closing(ws, R: Resolver):
    out, grp = [], []
    for r in rows(ws):
        if len(r) < 2 or r[0] in ("prefix",) or (r[1] == "" and not grp):
            continue
        if r[1] == "":
            _oc_group(grp, R, out)
            grp = []
            continue
        if r[0] in ("ind-as", "in-ca", "in-gaap"):
            grp.append(r)
    _oc_group(grp, R, out)
    return out


def _oc_group(grp, R, out):
    """A group lists the balance twice (beginning and end) and the change once."""
    names = [g[1] for g in grp]
    bal = next((n for n in names if names.count(n) > 1), None)
    chg = next((n for n in names if n != bal), None)
    if bal and chg and R.name(bal) and R.name(chg):
        out.append({"balance": R.name(bal), "change": R.name(chg),
                    "label": next(g[2] for g in grp if g[1] == bal).replace(" at beginning of period", "")})


def read_list(ws):
    out = []
    first = True
    for r in rows(ws):
        if first:
            first = False
            continue
        for v in r:
            if v and v not in out and v not in ("Country Name", "Currency Code"):
                out.append(v)
    return out


def read_generic(ws):
    out = []
    for r in rows(ws):
        if r[0].isdigit() and len(r) > 1:
            out.append({"no": int(r[0]), "text": re.sub(r"[ \t]+", " ", r[1]).strip()})
    return out


def read_elr(ws):
    out = []
    for r in rows(ws):
        if r[0].isdigit():
            m = re.match(r"\[(\w+)\]\s*(.*)", r[2])
            out.append({"id": r[1], "code": m.group(1) if m else "", "name": m.group(2) if m else r[2], "applies": r[3]})
    return out


def build(xlsx: str, key: str) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    schema = load_schema(key)
    sheet = {n.lower().strip(): wb[n] for n in wb.sheetnames}

    def ws(*names):
        for n in names:
            for k, v in sheet.items():
                if k.startswith(n):
                    return v
        return None

    labels = {}
    for r in rows(ws("specific rules")):
        if len(r) > 2 and r[0] in ("ind-as", "in-ca", "in-gaap") and r[1]:
            labels.setdefault(r[1], r[2])
    R = Resolver(schema, labels)
    data = {
        "taxonomy": key,
        "source": Path(xlsx).name,
        "generic": read_generic(ws("generic rules")),
        "elr": read_elr(ws("applicable elr")) if ws("applicable elr") else [],
        "element_rules": read_element_rules(ws("specific rules"), R, schema),
        "mandatory_line_items": read_mandatory_line_items(ws("mandatory line"), R, schema),
        "exempt_parent_members": read_member_exemptions(ws("exempt parent"), child=False),
        "exempt_child_members": read_member_exemptions(ws("exempt child"), child=True),
        "calc_exempt": read_calc_exempt(ws("parent child exempt")),
        "opening_closing": read_opening_closing(ws("formulas"), R),
        "countries": read_list(ws("country")),
        "currencies": read_list(ws("currency")),
    }
    return data


def report(data: dict):
    er = data["element_rules"]
    done = [r for r in er if r["checks"]]
    print(f"{len(er)} element rules, {len(done)} turned into checks, {len(er) - len(done)} kept as text only")
    for r in er:
        if not r["checks"]:
            print(f"  [{r['role']}] {r['eid']}: {r['text'][:150]!r}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    data = build(argv[1], argv[2])
    out = SCHEMA_DIR / f"mca_rules_{argv[2]}.json"
    out.write_text(json.dumps(data, indent=0, ensure_ascii=False), encoding="utf-8")
    print("wrote", out)
    if "--report" in argv:
        report(data)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
