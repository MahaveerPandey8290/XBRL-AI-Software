"""Turn MCA's official business rules workbook into JSON the validator reads.

MCA publishes the rules for each taxonomy as an Excel workbook (for C&I 2016:
``Final_Business_Rule_CI_Taxonomy_2016_V1.3.xls``). This script reads it and
writes ``xbrl_aoc4/schema/business_rules_<KEY>.json`` with:

* ``element_rules``: the free-text rule of every element ("Specific rules for
  elements"), split into checks the validator understands. A sentence that is
  not understood is kept under ``manual`` so nothing is silently dropped;
* ``mandatory_line_items``: required line items per table;
* ``exempt_parent`` / ``exempt_child``: members exempt from the parent/child
  member rule (generic rule 3);
* ``calc_exempt``: abstracts exempt from the parent/child amount rule (generic
  rule 1);
* ``countries``, ``generic`` (text of the generic rules), ``changes``.

Usage::

    python tools/extract_business_rules.py AS "reference/mca/Final_Business_Rule_CI_Taxonomy_2016_V1.3.xls"
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import xlrd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from xbrl_aoc4.schema_loader import load_schema  # noqa: E402

OUT_DIR = ROOT / "xbrl_aoc4" / "schema"

# The workbook carries the current tabs plus older copies with slightly
# different names ("Mandatory line-items" vs "Mandatory Line Items"). The
# "Changes to Business Rules" tab names the current ones.
TABS = {
    "elements": "Specific rules for elements",
    "generic": "Generic rules",
    "elr": "Applicable ELR",
    "line_items": "Mandatory Line Items",
    "exempt_parent": "Exempt parent member Dimension",
    "exempt_child": "Exempt Child Member Dimension",
    "calc_exempt": "Parent Child Exempt Calculation",
    "countries": "Country Codes",
    "changes": "Changes to Business Rules",
}


def cell(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"[ \t ]+", " ", str(v)).strip()


class Resolver:
    """Map names used in the workbook (ids, lower-case ids, labels, typos) to schema ids."""

    def __init__(self, schema):
        self.schema = schema
        self.by_name: dict[str, str] = {}
        self.by_label: dict[str, str] = {}
        for eid, el in schema.elements.items():
            name = eid.split("_", 1)[1]
            self.by_name.setdefault(name.lower(), eid)
            lab = re.sub(r"\s*\[.*?\]\s*", "", el.get("label", "")).strip().lower()
            if lab:
                self.by_label.setdefault(lab, eid)
        self.tables: dict[str, str] = {}
        for tid in schema.tables:
            name = tid.split("_", 1)[1]
            self.tables[name.lower()] = tid
            self.tables[re.sub(r"table$", "", name.lower())] = tid
        self.members: dict[str, str] = {}
        for t in schema.tables.values():
            for a in t["axes"]:
                self.members[a["id"].split("_", 1)[1].lower()] = a["id"]
                for m in a["members"]:
                    self.members[m["id"].split("_", 1)[1].lower()] = m["id"]
                if a.get("default"):
                    self.members[a["default"].split("_", 1)[1].lower()] = a["default"]

    def element(self, name: str) -> str | None:
        n = re.sub(r"\s+", "", name or "").strip("'\"“”‘’.,").lower()
        if not n:
            return None
        if n in self.by_name:
            return self.by_name[n]
        # the workbook sometimes drops/adds 'The', or pluralises
        for alt in (n.replace("bythecompany", "bycompany"), n.replace("ofthecompany", "ofcompany"), n.rstrip("s"), n + "s"):
            if alt in self.by_name:
                return self.by_name[alt]
        lab = re.sub(r"\s+", " ", name.strip("'\"“”‘’ .,")).lower()
        return self.by_label.get(lab)

    def table(self, name: str) -> str | None:
        n = re.sub(r"\s+|'", "", name.split(" in Notes")[0].split(" in notes")[0]).lower()
        return self.tables.get(n) or self.tables.get(re.sub(r"table$", "", n))

    def member(self, name: str) -> str | None:
        return self.members.get(re.sub(r"\s+", "", name or "").lower())


# ---------------------------------------------------------------- clause parser
NONNEG = re.compile(r"(greater than|grater than)\s*(or\s*)?(equal|eqaul|equals)\s*(to)?\s*(zero|0)|equal to or greater than\s*zero|shall be greater than or equal to zero", re.I)
POSITIVE = re.compile(r"should be greater than zero|this should be greater than zero", re.I)
MAX100 = re.compile(r"(less than or equal to|not be greater than)\s*100\s*%", re.I)
LE_TODAY = re.compile(r"less than (or equal to )?(the )?system date", re.I)
PAN = re.compile(r"valid PAN", re.I)
CIN = re.compile(r"valid CIN", re.I)
DIN = re.compile(r"valid DIN", re.I)
COUNTRY = re.compile(r"valid country", re.I)
UNIQUE = re.compile(r"(should be unique|unique in all the dimension)", re.I)
DIFF_CIN = re.compile(r"different from (the )?CIN of (the )?filing company", re.I)
DIFF_PAN = re.compile(r"different from (the )?PAN of (the )?(filing )?company", re.I)
AGE18 = re.compile(r"greater than or equal to 18 years", re.I)
INR = re.compile(r"Should be INR", re.I)
MANDATORY = re.compile(r"^(this|it)\s+(is|will be|shall be|ia)\s+(a\s+)?mandatory\s*(field|item|element|table)?\s*\.?$|^this (item|field|table) (is|will be) mandatory\.?$|^this item will be mandatory\.?$|^this is a mandatory (field|item|element) \.?$", re.I)
MAND_STANDALONE = re.compile(r"mandatory (field|item)?\s*(only )?(in case of|for) stand ?alone", re.I)
MAND_CONSOLIDATED = re.compile(r"^mandatory in case of consolidated", re.I)
MAND_CY = re.compile(r"mandatory (field )?in case of current financial year", re.I)
MAND_DATE = re.compile(r'mandatory if "?DateOfStartOfReportingPeriod"? is more than (\d\d)/(\d\d)/(\d{4})', re.I)
Q = r"[\"'“”‘’]\s*([A-Za-z][A-Za-z0-9 _\-]*?)\s*[\"'“”‘’]"
IF_YES = [
    re.compile(r"mandatory[^.]*?if\s+yes is selected in (?:element |field )?" + Q, re.I),
    re.compile(r"mandatory[^.]*?in case yes is selected in (?:element |field )?" + Q, re.I),
    re.compile(r"mandatory[^.]*?if\s*" + Q + r"\s*is (?:selected as )?[\"'‘’]?(yes|no)\b", re.I),
    re.compile(r"mandatory[^.]*?if\s+([A-Z][A-Za-z0-9]+) is (yes|no)\b", re.I),
]
IF_GT0 = [
    re.compile(r"mandatory[^.]*?if (?:value in |value entered in field |amount of )?" + Q + r"[^.]*?is greater than zero", re.I),
    re.compile(r"mandatory[^.]*?if ([A-Z][A-Za-z0-9]+) is greater than zero", re.I),
    re.compile(r"mandatory[^.]*?in case value in " + Q + r"[^.]*?is greater than zero", re.I),
]
IF_NONZERO = [
    re.compile(r"mandatory in case " + Q + r" is other than zero", re.I),
    re.compile(r"mandatory in case value in ([A-Z][A-Za-z0-9]+) is other than zero", re.I),
]
IF_ENTERED = [
    re.compile(r"mandatory[^.]*?(?:if|in case)\s+(?:value is entered in field\s*)?" + Q + r"\s*is entered", re.I),
    re.compile(r"mandatory in case value is entered in field " + Q, re.I),
    re.compile(r"mandatory if " + Q + r" is entered and vice", re.I),
]
VICE_VERSA = re.compile(r"vice[ -]?a[ -]?versa", re.I)
LE_EL = re.compile(r"less than or equal to\s*" + Q, re.I)
GE_EL = re.compile(r"greater than or equal to\s*(?:" + Q + r"|([A-Z][A-Za-z0-9]{12,}))", re.I)
IF_MEMBER = re.compile(r"mandatory in case of\s*[\"'‘’]?([A-Za-z]+Member)[\"'‘’]?", re.I)
IF_MEMBER2 = re.compile(r"mandatory in case details in respect of " + Q + r" is entered", re.I)
EITHER = re.compile(r"either .*(mandatory) (if|in case) country", re.I)
TABLE_ALWAYS = re.compile(r"^this (is|ia) a mandatory table|^this table is mandatory\.?$|^this table is mandatory$", re.I)
TABLE_IF_YES = re.compile(r"(?:table|this item)[^.]*?mandatory[^.]*?(?:if|in case)\s+(?:'?yes'?\s+(?:is\s+)?selected in (?:field )?" + Q + r"|" + Q + r"\s*is\s*'?yes)", re.I)
TABLE_IF_AMOUNT = re.compile(r"table[^.]*?mandatory[^.]*?(?:if|in case)\s+(.*?)(?:is|are)\s+(greater than zero|other than zero)", re.I)

# "Mandatory if corresponding Number of shares is entered and vice-a-versa": amount -> its number of shares
SHARE_PAIRS = {
    "AmountOfPublicIssueDuringPeriod": "NumberOfSharesIssuedInPublicOffering",
    "AmountOfBonusIssueDuringPeriod": "NumberOfSharesIssuedAsBonusShares",
    "AmountOfRightsIssueDuringPeriod": "NumberOfSharesIssuedAsRights",
    "AmountOfOtherPrivatePlacementIssueDuringPeriod": "NumberOfSharesIssuedInOtherPrivatePlacement",
    "AmountOfOtherPreferentialAllotmentIssueDuringPeriod": "NumberOfSharesIssuedAsOtherPreferentialAllotment",
    "AmountOfIssueAllottedForContractsWithoutPaymentReceivedInCashDuringPeriod": "NumberOfSharesAllottedForContractsWithoutPaymentReceivedInCash",
    "AmountOfIssueUnderSchemeOfAmalgamationDuringPeriod": "NumberOfSharesIssuedUnderSchemeOfAmalgamation",
    "AmountOfOtherIssuesDuringPeriod": "NumberOfOtherIssuesOfShares",
    "AmountOfOtherIssueArisingOutOfConversionOfSecuritiesDuringPeriod": "NumberOfOtherIssueOfSharesArisingOutOfConversionOfSecurities",
    "DecreaseInAmountOfSharesRedeemed": "NumberOfSharesRedeemed",
    "DecreaseInAmountOfSharesBoughtBack": "NumberOfSharesBoughtBack",
    "OtherDecreaseInAmountOfShares": "OtherDecreaseInNumberOfShares",
    "DecreaseInShareCapitalDuringPeriod": "DecreaseInNumberOfSharesDuringPeriod",
}


def _name(eid: str) -> str:
    return str(eid).split("_", 1)[-1]


SERVER_SIDE = re.compile(r"ICAI database|ICSI database|associated with (the )?company|based on (CIN|DIN|')|should belong to CIN|marked as .defective|same as entered in the Form|Name of the company should be based", re.I)


def split_clauses(text: str) -> list[str]:
    text = text.replace("|", "\n")
    parts = []
    for line in text.split("\n"):
        for p in re.split(r"(?<=[a-z0-9%\)'\"”’])\.\s+(?=[A-Z])", line):
            p = p.strip(" .;")
            if p:
                parts.append(p)
    return parts


def quoted(rx_list, clause):
    for rx in rx_list:
        m = rx.search(clause)
        if m:
            return m
    return None


def parse_rule(elem_id: str, text: str, res: Resolver, is_table: bool) -> tuple[list[dict], list[str]]:
    checks: list[dict] = []
    manual: list[str] = []

    def add(**c):
        if c not in checks:
            checks.append(c)

    def target(name):
        return res.element(name)

    for cl in split_clauses(text):
        hit = False
        low = cl.lower()
        if re.fullmatch(r"mandatory line item", cl.strip(), re.I):
            continue  # covered by the 'Mandatory Line Items' tab
        if SERVER_SIDE.search(cl):
            manual.append("[MCA database] " + cl)
            continue
        if is_table:
            if TABLE_ALWAYS.search(cl):
                add(k="table_always")
                hit = True
            m = TABLE_IF_YES.search(cl)
            if m:
                t = target(m.group(1) or m.group(2))
                if t:
                    add(k="table_if_yes", target=t)
                    hit = True
            m = TABLE_IF_AMOUNT.search(cl)
            if m and not hit:
                names = re.findall(r"[\"'“”‘’]\s*([A-Za-z][A-Za-z0-9 ]*?)\s*[\"'“”‘’]|\b([A-Z][a-z]+[A-Z][A-Za-z]+)\b", m.group(1))
                ts = [target(a or b) for a, b in names]
                ts = [t for t in ts if t]
                if ts:
                    add(k="table_if_amount", targets=ts, op="gt0" if "greater" in m.group(2) else "nonzero")
                    hit = True
            if not hit:
                manual.append(cl)
            continue
        if MANDATORY.search(cl.strip()):
            add(k="mandatory")
            hit = True
        if MAND_STANDALONE.search(cl):
            add(k="mandatory", only="standalone")
            hit = True
        if MAND_CONSOLIDATED.search(cl):
            add(k="mandatory", only="consolidated")
            hit = True
        if MAND_CY.search(cl):
            add(k="mandatory", only="standalone" if "stand alone" in low or "standalone" in low else "", cy_only=True)
            hit = True
        m = MAND_DATE.search(cl)
        if m:
            add(k="mandatory_if_start_after", date=f"{m.group(3)}-{m.group(2)}-{m.group(1)}")
            hit = True
        if NONNEG.search(cl):
            add(k="nonneg")
            hit = True
        if POSITIVE.search(cl):
            add(k="positive")
            hit = True
        if MAX100.search(cl):
            add(k="max100")
            hit = True
        if LE_TODAY.search(cl):
            add(k="le_today")
            hit = True
        if AGE18.search(cl):
            add(k="age18")
            hit = True
        if INR.search(cl):
            add(k="inr")
            hit = True
        if PAN.search(cl):
            add(k="pan")
            hit = True
        if CIN.search(cl):
            add(k="cin")
            hit = True
        if DIN.search(cl):
            add(k="din")
            hit = True
        if COUNTRY.search(cl):
            add(k="country")
            hit = True
        if UNIQUE.search(cl):
            add(k="unique")
            hit = True
        if DIFF_CIN.search(cl):
            add(k="diff_company_cin")
            hit = True
        if DIFF_PAN.search(cl):
            add(k="diff_company_pan")
            hit = True
        if "mandatory" in low and not hit or ("mandatory" in low and re.search(r"\b(if|in case|only if)\b", low)):
            m = quoted(IF_YES, cl)
            if m:
                t = target(m.group(1))
                val = (m.group(2) if m.lastindex and m.lastindex >= 2 else "yes").lower()
                if t:
                    add(k="if_value", target=t, value="true" if val == "yes" else "false")
                    hit = True
            if not hit and "selected" in low:
                m = re.search(r"mandatory[^.]*?(?:if|in case)\s*" + Q + r"\s*is selected in\s*" + Q, cl, re.I)
                if m and target(m.group(2)):
                    add(k="if_value", target=target(m.group(2)), value=m.group(1))
                    hit = True
            m = quoted(IF_GT0, cl)
            if m and target(m.group(1)):
                add(k="if_gt0", target=target(m.group(1)))
                hit = True
            m = quoted(IF_NONZERO, cl)
            if m and target(m.group(1)):
                add(k="if_nonzero", target=target(m.group(1)))
                hit = True
            m = quoted(IF_ENTERED, cl)
            if m and target(m.group(1)):
                add(k="if_entered", target=target(m.group(1)), vice_versa=bool(VICE_VERSA.search(cl)))
                hit = True
            m = IF_MEMBER.search(cl) or IF_MEMBER2.search(cl)
            if m and res.member(m.group(1)):
                add(k="if_member", member=res.member(m.group(1)))
                hit = True
            if EITHER.search(cl):
                other = [target(x) for x in re.findall(r"[\"'‘’]?([A-Z][A-Za-z]+)[\"'‘’]?", cl)]
                other = [o for o in other if o and o != elem_id]
                if other:
                    add(k="either_if_india", other=other[0])
                    hit = True
            if "corresponding number of shares" in low and target(SHARE_PAIRS.get(_name(elem_id), "")):
                add(k="if_entered", target=target(SHARE_PAIRS[_name(elem_id)]), vice_versa=True)
                hit = True
        m = LE_EL.search(cl)
        if m and not MAX100.search(cl) and not LE_TODAY.search(cl):
            t = target(m.group(1))
            if t:
                add(k="le_element", target=t)
                hit = True
        m = GE_EL.search(cl)
        if m and not NONNEG.search(cl) and "system date" not in low and "18 years" not in low:
            t = target(m.group(1) or m.group(2))
            if t:
                add(k="ge_element", target=t)
                hit = True
        m = re.search(r"should be equal to value entered in element\s*" + Q, cl, re.I)
        if m and target(m.group(1)):
            add(k="eq_element", target=target(m.group(1)))
            hit = True
        if not hit:
            manual.append(cl)
    return checks, manual


# ---------------------------------------------------------------- tabs
def element_rules(sh, res: Resolver):
    out = []
    elr = ""
    for r in range(sh.nrows):
        name, text = cell(sh.cell_value(r, 0)), cell(sh.cell_value(r, 1)) if sh.ncols > 1 else ""
        name = name.strip("| ").strip()
        m = re.match(r"\[(\d{6})A?\]", name)
        if m:
            elr = m.group(1)
            continue
        if not text or name.startswith("ELR/"):
            continue
        is_table = name.endswith("Table")
        eid = res.table(name) if is_table else res.element(name)
        checks, manual = parse_rule(eid or name, text, res, is_table)
        out.append({"elr": elr, "name": name, "id": eid, "table": is_table, "text": text,
                    "checks": checks, "manual": manual})
    return out


def line_items(sh, res: Resolver):
    out = []
    for r in range(sh.nrows):
        no, tname, text = (cell(sh.cell_value(r, c)) for c in range(3))
        if not re.match(r"^\d+$", no):
            continue
        tid = res.table(tname.strip()) or res.table(tname.strip() + "Table")
        if not tid:
            for t in re.findall(r"[A-Za-z]+", tname):
                tid = res.table(t) or res.table(t.replace("LineItems", "Table"))
                if tid:
                    break
        all_except = bool(re.search(r"all elements (are )?mandatory", text, re.I))
        names = re.findall(r"\b([A-Z][a-z]+(?:[A-Z0-9][A-Za-z0-9]*)+)\b", text)
        items, unresolved = [], []
        for n in names:
            if n in ("Mandatory", "Elements", "Element", "Atleast"):
                continue
            e = res.element(n)
            if e and res.schema.element(e).get("abstract"):
                continue
            if e and e not in items:
                items.append(e)
            elif not e and n not in unresolved:
                unresolved.append(n)
        out.append({"no": int(no), "table_name": tname, "table": tid, "all_except": all_except,
                    "items": items, "unresolved": unresolved, "text": text})
    return out


def exempt_parent(sh, res: Resolver):
    out = []
    for r in range(sh.nrows):
        t, a, m = (cell(sh.cell_value(r, c)) for c in range(3))
        if t.endswith("Table") and res.member(m):
            out.append({"table": res.table(t), "axis": res.member(a), "member": res.member(m)})
    return out


def exempt_child(sh, res: Resolver):
    out = []
    t = a = p = ""
    for r in range(sh.nrows):
        row = [cell(sh.cell_value(r, c)) for c in range(4)]
        if row[0].endswith("Table"):
            t = row[0]
        if row[1].endswith("Axis"):
            a = row[1]
        if row[2].endswith("Member"):
            p = row[2]
        if row[3].endswith("Member") and t:
            out.append({"table": res.table(t), "axis": res.member(a), "parent": res.member(p), "member": res.member(row[3]),
                        "names": [t, a, p, row[3]]})
    return out


def calc_exempt(sh, res: Resolver):
    out = []
    for r in range(sh.nrows):
        name, text = cell(sh.cell_value(r, 0)), cell(sh.cell_value(r, 1))
        if not text or name.startswith("Abstract"):
            continue
        keep = [res.element(x) for x in re.findall(r"'?([A-Z][A-Za-z]+)'?", text.split("except")[-1])] if "except" in text else []
        children = [res.element(x) for x in re.findall(r"\b([A-Z][a-z]+(?:[A-Z][A-Za-z]*)+)\b", text)] if "Following" in text else []
        out.append({"abstract": res.element(name), "name": name, "except": [k for k in keep if k],
                    "children": [c for c in children if c], "text": text})
    return out


def main(key: str, path: str):
    schema = load_schema(key)
    res = Resolver(schema)
    wb = xlrd.open_workbook(path)
    sheets = {s.name.strip(): s for s in wb.sheets()}
    gen = sheets[TABS["generic"]]
    generic = [{"no": int(float(cell(gen.cell_value(r, 0)))), "text": cell(gen.cell_value(r, 1))}
               for r in range(gen.nrows) if re.match(r"^\d+$", cell(gen.cell_value(r, 0)))]
    ch = sheets[TABS["changes"]]
    changes = [{"tab": cell(ch.cell_value(r, 2)), "element": cell(ch.cell_value(r, 3)), "rule": cell(ch.cell_value(r, 4))}
               for r in range(1, ch.nrows) if cell(ch.cell_value(r, 3))]
    co = sheets[TABS["countries"]]
    countries = sorted({cell(co.cell_value(r, c)).upper() for r in range(1, co.nrows) for c in (0, 2) if cell(co.cell_value(r, c))})
    out = {
        "source": Path(path).name,
        "element_rules": element_rules(sheets[TABS["elements"]], res),
        "mandatory_line_items": line_items(sheets[TABS["line_items"]], res),
        "exempt_parent": exempt_parent(sheets[TABS["exempt_parent"]], res),
        "exempt_child": exempt_child(sheets[TABS["exempt_child"]], res),
        "calc_exempt": calc_exempt(sheets[TABS["calc_exempt"]], res),
        "countries": countries,
        "generic": generic,
        "changes": changes,
    }
    dest = OUT_DIR / f"business_rules_{key}.json"
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    er = out["element_rules"]
    print(f"{dest.name}: {len(er)} element rules ({sum(1 for e in er if e['checks'])} with checks, "
          f"{sum(1 for e in er if e['manual'])} with sentences left for review, {sum(1 for e in er if not e['id'])} unresolved names); "
          f"{len(out['mandatory_line_items'])} line-item tables, {len(out['exempt_parent'])} parent and "
          f"{len(out['exempt_child'])} child member exemptions, {len(countries)} countries")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
