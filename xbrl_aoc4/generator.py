"""Generate the MCA AOC-4 XBRL instance document from a Filing.

The output follows the layout of InstantXBRL / TaxPrint instances (context ids
``I_ddmmyyyy_ddmmyyyy`` / ``D_...``, ``I_Opening``, ``..._TBL_n`` dimension
contexts, INR / INRPerShare / Pure / Shares units).
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape

from .calc import fill_totals
from .config import CIN_SCHEME, STANDARD_NS, TAXONOMIES
from .model import Filing, parse_date
from .periods import PeriodResolver
from .schema_loader import load_schema
from .taxonomy import cached_namespaces

UNIT_FOR_TYPE = {
    "monetaryItemType": "INR",
    "perShareItemType": "INRPerShare",
    "sharesItemType": "Shares",
}
NUMERIC_TYPES = {"monetaryItemType", "sharesItemType", "decimalItemType", "percentItemType", "pureItemType",
                 "perShareItemType", "integerItemType", "nonNegativeIntegerItemType", "positiveIntegerItemType"}
UNIT_XML = {
    "INR": '<unit id="INR">\n<measure>iso4217:INR</measure>\n</unit>',
    "INRPerShare": ('<unit id="INRPerShare">\n<divide>\n<unitNumerator>\n<measure>iso4217:INR</measure>\n'
                    '</unitNumerator>\n<unitDenominator>\n<measure>xbrli:shares</measure>\n</unitDenominator>\n'
                    '</divide>\n</unit>'),
    "Pure": '<unit id="Pure">\n<measure>xbrli:pure</measure>\n</unit>',
    "Shares": '<unit id="Shares">\n<measure>xbrli:shares</measure>\n</unit>',
}
TRUE_WORDS = {"true", "yes", "y", "1"}
FALSE_WORDS = {"false", "no", "n", "0"}

AMOUNT_SCALE = {"Rupees": 1, "Hundreds": 100, "Thousands": 1000, "Lakhs": 100000, "Millions": 1000000,
                "Crores": 10000000}


def is_numeric(el: dict) -> bool:
    t = (el or {}).get("type", "")
    return t in NUMERIC_TYPES or t.endswith("IntegerItemType")


def unit_for(el: dict) -> str:
    return UNIT_FOR_TYPE.get(el.get("type", ""), "Pure")


def to_number(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip().replace(",", "").replace("₹", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d


def fmt_number(d: Decimal) -> str:
    if d == d.to_integral_value():
        return str(d.quantize(Decimal(1)))
    s = format(d.normalize(), "f")
    return s


def normalise(el: dict, value, scale: int = 1) -> str | None:
    """Return the XML text for a value, or None if it is empty/invalid."""
    if value is None:
        return None
    t = el.get("type", "")
    if is_numeric(el):
        d = to_number(value)
        if d is None:
            return None
        if t == "monetaryItemType" and scale != 1:
            d = d * scale
        return fmt_number(d)
    s = str(value).strip()
    if not s:
        return None
    if t == "booleanItemType":
        low = s.lower()
        if low in TRUE_WORDS:
            return "true"
        if low in FALSE_WORDS:
            return "false"
        return s
    if t == "dateItemType":
        d = parse_date(s)
        return d.isoformat() if d else s
    return s


def _ddmmyyyy(iso: str) -> str:
    d = parse_date(iso)
    return d.strftime("%d%m%Y") if d else iso


def _qname(eid: str) -> str:
    return eid.replace("_", ":", 1)


class Generator:
    def __init__(self, filing: Filing):
        self.f = filing
        self.f.ensure_periods()
        self.schema = load_schema(filing.taxonomy)
        self.period = PeriodResolver(self.schema)
        self.scale = AMOUNT_SCALE.get(getattr(filing, "amount_unit", "Rupees") or "Rupees", 1)
        self.contexts: dict[tuple, str] = {}
        self.ctx_xml: list[str] = []
        self.dim_ctx_xml: list[str] = []
        self.units: set[str] = set()
        self.facts: list[str] = []
        self.tbl_n = 0
        self.emitted: set = set()
        self.warnings: list[str] = []

    # ---------------------------------------------------------- contexts
    def _period(self, eid: str, pk: str):
        f = self.f
        kind = self.period(eid)
        if kind == "instant":
            if pk == "CY":
                return ("I", f.fy_start, f.fy_end, f.fy_end)
            if pk == "PY":
                return ("I", f.py_start, f.py_end, f.py_end)
            if pk == "OPEN":
                return ("O", "", "", f.open_date)
        else:
            if pk == "CY":
                return ("D", f.fy_start, f.fy_end, None)
            if pk == "PY":
                return ("D", f.py_start, f.py_end, None)
        return None

    def _base_id(self, p) -> str:
        kind, start, end, _ = p
        if kind == "O":
            return "I_Opening"
        return f"{kind}_{_ddmmyyyy(start)}_{_ddmmyyyy(end)}"

    def _period_xml(self, p) -> str:
        kind, start, end, inst = p
        if kind in ("I", "O"):
            return f"<period>\n<instant>{inst}</instant>\n</period>"
        return f"<period>\n<startDate>{start}</startDate>\n<endDate>{end}</endDate>\n</period>"

    def context(self, eid: str, pk: str, dims: dict | None, table: dict | None) -> str | None:
        p = self._period(eid, pk)
        if p is None:
            return None
        dims = dims or {}
        key = (p, tuple(sorted(dims.items())))
        if key in self.contexts:
            return self.contexts[key]
        base = self._base_id(p)
        ent = f'<entity>\n<identifier scheme="{CIN_SCHEME}">{escape(self.f.cin)}</identifier>\n</entity>'
        if not dims:
            cid = base
            self.ctx_xml.append(f'<context id="{cid}"  >\n{ent}\n{self._period_xml(p)}\n</context>')
        else:
            self.tbl_n += 1
            cid = f"{base}_TBL_{self.tbl_n}"
            axes = {a["id"]: a for a in (table or {}).get("axes", [])}
            members = []
            order = [a for a in (table or {}).get("axis_ids", []) if a in dims] + [a for a in dims if a not in axes]
            for ax in order:
                mem = dims[ax]
                info = axes.get(ax, {})
                if info.get("typedDomain"):
                    dom = _qname(info["typedDomain"])
                    members.append(f'<xbrldi:typedMember dimension="{_qname(ax)}">\n'
                                   f'<{dom}>{escape(str(mem))}</{dom}>\n</xbrldi:typedMember>\n')
                else:
                    members.append(f'<xbrldi:explicitMember dimension="{_qname(ax)}">{_qname(mem)}</xbrldi:explicitMember>\n')
            self.dim_ctx_xml.append(f'<context id="{cid}">\n{ent}\n{self._period_xml(p)}\n<scenario>\n'
                                    + "".join(members) + '\n</scenario>\n</context>')
        self.contexts[key] = cid
        return cid

    # ---------------------------------------------------------- facts
    def fact(self, eid: str, pk: str, value, dims=None, table=None, prefix="Elem"):
        el = self.schema.element(eid)
        if el is None:
            el = {"type": "stringItemType", "name": eid}
            self.warnings.append(f"{eid} is not in the template; written as text")
        if el.get("abstract") or el.get("table"):
            return
        txt = normalise(el, value, self.scale)
        if txt is None:
            return
        if is_numeric(el) and dims is None and False:
            pass
        cid = self.context(eid, pk, dims, table)
        if cid is None:
            self.warnings.append(f"{eid}: no {pk} period for this element; skipped")
            return
        if (eid, cid) in self.emitted:
            return  # an element/context pair may only be reported once
        self.emitted.add((eid, cid))
        n = len(self.facts) + 1
        q = _qname(eid)
        if is_numeric(el):
            u = unit_for(el)
            self.units.add(u)
            dec = self.f.decimals or "INF"
            self.facts.append(f'<{q}  id="{prefix}_{n}"   contextRef="{cid}"    decimals="{dec}"   unitRef="{u}">{txt}</{q}>')
        else:
            self.facts.append(f'<{q}  id="{prefix}_{n}"   contextRef="{cid}">{escape(txt)}</{q}>')

    # ---------------------------------------------------------- build
    def ordered_value_ids(self, values):
        seen = []
        done = set()
        for sec in self.schema.sections:
            for it in sec["items"]:
                if it["id"] in values and it["id"] not in done:
                    seen.append(it["id"])
                    done.add(it["id"])
        for eid in values:
            if eid not in done:
                seen.append(eid)
        return seen

    def build(self) -> str:
        f = self.f
        values = fill_totals(f, self.schema)
        for eid in self.ordered_value_ids(values):
            for pk in ("CY", "PY", "OPEN"):
                v = values.get(eid, {}).get(pk)
                if v not in (None, ""):
                    self.fact(eid, pk, v)
        for tid, rows in f.tables.items():
            table = self.schema.tables.get(tid)
            order = list(dict.fromkeys(it["id"] for it in table["items"])) if table else []
            for row in rows:
                dims = {k: v for k, v in row["dims"].items() if v not in (None, "")}
                if table:
                    # the default member of an axis is never written to a context
                    for a in table["axes"]:
                        if a.get("default") and dims.get(a["id"]) == a["default"]:
                            dims.pop(a["id"])
                ids = order + [e for e in row["values"] if e not in order]
                for eid in ids:
                    vals = row["values"].get(eid)
                    if not vals:
                        continue
                    for pk in ("CY", "PY", "OPEN"):
                        v = vals.get(pk)
                        if v not in (None, ""):
                            self.fact(eid, pk, v, dims or None, table, prefix="LI" if dims else "Elem")
        return self.render()

    def namespaces(self) -> dict:
        tax = dict(TAXONOMIES[self.f.taxonomy]["namespaces"])
        tax.update({k: v for k, v in cached_namespaces(self.f.taxonomy).items() if k in tax or k in self.used_prefixes()})
        return tax

    def used_prefixes(self) -> set:
        out = set()
        for line in self.facts + self.dim_ctx_xml:
            out.update(re.findall(r"<([a-z][a-z\-]*):", line))
            out.update(re.findall(r'dimension="([a-z][a-z\-]*):', line))
            out.update(re.findall(r">([a-z][a-z\-]*):[A-Z]", line))
        return out - {"xbrldi", "xbrli", "link"}

    def render(self) -> str:
        now = datetime.now()
        ns = self.namespaces()
        missing = self.used_prefixes() - set(ns)
        for p in missing:
            self.warnings.append(f"No namespace configured for prefix '{p}'")
        ns_lines = ['   xmlns="http://www.xbrl.org/2003/instance"']
        ns_lines = [f'<xbrl    xmlns:xbrli="{STANDARD_NS["xbrli"]}"'] + ns_lines
        for p, u in STANDARD_NS.items():
            if p == "xbrli":
                continue
            ns_lines.append(f'   xmlns:{p}="{u}"')
        for p, u in ns.items():
            ns_lines.append(f'   xmlns:{p}="{u}"')
        schema_ref = TAXONOMIES[self.f.taxonomy]["schemaRef"]
        units = [UNIT_XML[u] for u in ("INR", "INRPerShare", "Pure", "Shares") if u in self.units]
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!--Created by XBRL AOC-4 Generator (Python) -->",
            f"<!--Document Date : {now.strftime('%d %b %Y %H:%M:%S')} -->",
            "\n".join(ns_lines) + "\n >",
            f'<link:schemaRef xlink:type="simple" xlink:href="{schema_ref}" /> ',
            "<!--Context Begin -->",
            "\n".join(self.ctx_xml),
            "<!--Context End -->\n\n\n",
            "<!--Dimension Context Begin -->",
            "\n".join(self.dim_ctx_xml),
            "<!--Dimension Context End -->\n",
            "<!--Unit Begin -->",
            "\n".join(units),
            "<!--Unit End -->\n",
            "<!--Details Begin -->",
            "\n".join(self.facts),
            "<!--Details End -->",
            "</xbrl>",
            "",
        ]
        return "\r\n".join(p.replace("\r\n", "\n").replace("\n", "\r\n") for p in parts)


def generate(filing: Filing) -> tuple[str, list[str]]:
    g = Generator(filing)
    xml = g.build()
    return xml, g.warnings


def output_filename(filing: Filing) -> str:
    fs, fe = parse_date(filing.fy_start), parse_date(filing.fy_end)
    fy = f"FY[{fs.year if fs else ''}-{fe.year if fe else ''}]"
    nat = filing.nature or "Standalone"
    return f"{fy} {filing.cin} {nat}_BalanceSheet {datetime.now().strftime('%d-%m-%Y')}.xml"
