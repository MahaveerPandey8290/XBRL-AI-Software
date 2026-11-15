"""Build the JSON schema the app uses from an InstantXBRL Excel template.

The InstantXBRL templates (AS / C&I and Ind AS) describe every AOC-4 XBRL
section, element, table (hypercube), axis, member and MCA business rule.
This script reads one template and writes ``xbrl_aoc4/schema/<KEY>.json``.

Usage::

    python tools/extract_template.py AS    "E:/XBRL SOFTWARE/Templates/InstantXBRL_Template_2026.xls format.xls"
    python tools/extract_template.py INDAS "E:/XBRL SOFTWARE/Templates/InstantXBRL_INDAS_Template_2026.xls format.xls"

Optionally pass ``--formulas <converted.xlsx>`` (the same template saved as
.xlsx, e.g. with LibreOffice) to also capture the SUM() formulas behind total
rows, which become calculation checks in the validator.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import xlrd

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parent / "xbrl_aoc4" / "schema"

# column indexes in every data sheet
C_ID, C_ROLE, C_LABELROLE, C_TUPLE, C_TYPE, C_DEFAULT, C_CALC, C_HELP, C_DESC, C_CY, C_PY = 0, 1, 2, 3, 4, 5, 6, 7, 10, 11, 12


def s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def split_id(eid: str):
    """'in-gaap_ShareCapital' -> ('in-gaap', 'ShareCapital')."""
    m = re.match(r"^([a-z\-]+)_(.+)$", eid)
    if not m:
        return "", eid
    return m.group(1), m.group(2)


def item_from_row(sh, r):
    eid = s(sh.cell_value(r, C_ID))
    if not eid or eid == "ElementID" or "_" not in eid:
        return None
    prefix, name = split_id(eid)
    help_text = s(sh.cell_value(r, C_HELP)) if sh.ncols > C_HELP else ""
    dtype = s(sh.cell_value(r, C_TYPE))
    label_role = s(sh.cell_value(r, C_LABELROLE)).rsplit("/", 1)[-1]
    desc = s(sh.cell_value(r, C_DESC)) if sh.ncols > C_DESC else name
    abstract = desc.endswith("[Abstract]") or desc.endswith("[Line Items]") or name.endswith("Abstract") or name.endswith("LineItems")
    is_table = desc.endswith("[Table]") or name.endswith("Table")
    it = {
        "id": eid,
        "prefix": prefix,
        "name": name,
        "label": desc,
        "type": dtype,
        "labelRole": label_role,
        "row": r + 1,
    }
    if abstract:
        it["abstract"] = True
    if is_table:
        it["table"] = True
    if s(sh.cell_value(r, C_CALC)) == "1":
        it["calc"] = True
    hl = help_text.lower()
    if "data not required for previous year" in hl:
        it["noPY"] = True
    if "data not required for current year" in hl:
        it["noCY"] = True
    rule = ""
    if "Business Rule :" in help_text:
        rule = help_text.split("Business Rule :", 1)[1].strip().strip('"').strip()
    if rule:
        it["rule"] = rule
    tup = s(sh.cell_value(r, C_TUPLE))
    if tup:
        it["fixedDims"] = tup
    return it


def read_members(wb):
    """Return {table_id: {axis_id: {...}}} from the DomainMemberList sheet."""
    sh = wb.sheet_by_name("DomainMemberList")
    hdr = [s(sh.cell_value(0, c)) for c in range(sh.ncols)]
    col = {h: i for i, h in enumerate(hdr)}
    rows = []
    for r in range(1, sh.nrows):
        rows.append({h: s(sh.cell_value(r, i)) for h, i in col.items()})
    tables = {}
    # group rows by TableID; walk parent chain to find the axis of each member
    by_table = {}
    for row in rows:
        by_table.setdefault(row["TableID"], []).append(row)
    for tid, trows in by_table.items():
        axes = {}
        parent_of = {}
        for row in trows:
            pid = row["ParentID"].rsplit("/", 1)[-1]
            parent_of[row["ID"]] = pid
        for row in trows:
            if row["MemberType"] == "Axis":
                axes[row["ID"]] = {
                    "id": row["ID"],
                    "label": row["DisplayName"],
                    "typedDomain": row.get("TypedDomain", ""),
                    "default": "",
                    "members": [],
                }
        for row in trows:
            if row["MemberType"] not in ("Domain", "DomainMember"):
                continue
            # find axis
            cur, seen = row["ID"], set()
            ax = None
            while cur and cur not in seen:
                seen.add(cur)
                p = parent_of.get(cur, "")
                if p in axes:
                    ax = p
                    break
                cur = p
            if not ax:
                continue
            if row.get("DefaultDimension") == "1":
                axes[ax]["default"] = row["ID"]
            if row.get("Usable", "1") == "0":
                continue
            par = parent_of.get(row["ID"], "")
            axes[ax]["members"].append({"id": row["ID"], "label": row["DisplayName"],
                                        "parent": "" if par == ax else par})
        tables[tid] = axes
    return tables


def read_rules(wb):
    if "Specific Business rules" not in wb.sheet_names():
        return []
    sh = wb.sheet_by_name("Specific Business rules")
    out = []
    for r in range(3, sh.nrows):
        typ = s(sh.cell_value(r, 3))
        el = s(sh.cell_value(r, 2))
        if not typ or not el:
            continue
        out.append({
            "no": s(sh.cell_value(r, 0)),
            "src": s(sh.cell_value(r, 1)),
            "element": el.replace(":", "_"),
            "type": typ,
            "p1": s(sh.cell_value(r, 4)),
            "p2": s(sh.cell_value(r, 5)),
            "p3": s(sh.cell_value(r, 6)),
            "msg": s(sh.cell_value(r, 10)),
        })
    return out


def read_formulas(xlsx_path):
    """From the template saved as .xlsx, return
    ({sheet: {row: {"plus": [rows], "minus": [rows]}}}, {sheet: {row: [allowed values]}})."""
    import openpyxl
    wb = openpyxl.load_workbook(xlsx_path, read_only=False, data_only=False)
    sums, enums = {}, {}
    term = re.compile(r"\((-?\d+(?:\.\d+)?)\*L(\d+)\)")
    for ws in wb.worksheets:
        name = ws.title
        if not (re.match(r"^\d{6}", name) or name.startswith("Table-")):
            continue
        res = {}
        for r in range(1, ws.max_row + 1):
            v = ws.cell(r, 12).value
            if isinstance(v, str) and v.startswith("=") and "!" not in v:
                terms = term.findall(v)
                if terms:
                    res[r] = {"plus": [int(b) for a, b in terms if float(a) > 0],
                              "minus": [int(b) for a, b in terms if float(a) < 0]}
        if res:
            sums[name] = res
        ev = {}
        for dv in ws.data_validations.dataValidation:
            if dv.type != "list" or not dv.formula1 or not dv.formula1.startswith('"'):
                continue
            vals = [x for x in dv.formula1.strip('"').split(",") if x]
            for rng in str(dv.sqref).split():
                for row in re.findall(r"[A-Z]+(\d+)", rng):
                    ev[int(row)] = vals
        if ev:
            enums[name] = ev
    return sums, enums


def build(key: str, path: str, formulas: str | None = None):
    wb = xlrd.open_workbook(path, on_demand=True)
    members = read_members(wb)
    sections, tables = [], {}
    for sh in wb.sheets():
        if sh.nrows < 5:
            continue
        marker = s(sh.cell_value(0, 0))
        if marker == "ValidSheet":
            code_m = re.match(r"^(\d{6}[a-z]?)", sh.name)
            if not code_m:
                continue
            title = s(sh.cell_value(2, C_DESC)) or sh.name
            items = []
            for r in range(4, sh.nrows):
                it = item_from_row(sh, r)
                if it:
                    items.append(it)
            role = items[0]["id"] and s(sh.cell_value(4, C_ROLE)) if items else ""
            sections.append({
                "sheet": sh.name,
                "code": code_m.group(1),
                "title": title,
                "role": role,
                "hidden": sh.visibility != 0,
                "items": items,
            })
        elif marker == "ValidTable":
            tid = s(sh.cell_value(1, 0))
            title = s(sh.cell_value(2, C_DESC))
            role = s(sh.cell_value(2, 0))
            axes = []
            r = 3
            while r < sh.nrows and s(sh.cell_value(r, 0)) != "ElementID":
                aid = s(sh.cell_value(r, 0))
                if aid:
                    info = members.get(tid, {}).get(aid, {})
                    typed = s(sh.cell_value(r, 2)) or info.get("typedDomain", "")
                    dlabel = s(sh.cell_value(r, 3))
                    mems = info.get("members", [])
                    default = ""
                    if dlabel:
                        want = dlabel.replace("[Total]", "").strip()
                        default = next((m["id"] for m in mems if m["label"] == want), "") or info.get("default", "")
                        if not default:
                            # the default (domain) member is usually not 'usable'; record it by label
                            default = info.get("default", "")
                    axes.append({
                        "id": aid,
                        "label": s(sh.cell_value(r, C_DESC)),
                        "typedDomain": typed if typed.endswith("Domain") else "",
                        "defaultLabel": dlabel,
                        "default": default if dlabel else "",
                        "members": mems,
                    })
                r += 1
            items = []
            for rr in range(r + 1, sh.nrows):
                it = item_from_row(sh, rr)
                if it:
                    items.append(it)
            tables[tid] = {"sheet": sh.name, "id": tid, "title": title, "role": role, "axes": axes, "items": items}
    # attach tables to the section that lists them
    placed = set()
    for sec in sections:
        sec["tables"] = []
        for it in sec["items"]:
            if it.get("table") and it["id"] in tables:
                sec["tables"].append(it["id"])
                placed.add(it["id"])
    calc, enums = read_formulas(formulas) if formulas else ({}, {})
    for sec in sections:
        by_row = {it["row"]: it for it in sec["items"]}
        for row, spec in (calc.get(sec["sheet"]) or {}).items():
            tot = by_row.get(row)
            if not tot:
                continue
            plus = [by_row[x]["id"] for x in spec["plus"] if x in by_row and x != row]
            minus = [by_row[x]["id"] for x in spec["minus"] if x in by_row and x != row]
            if plus or minus:
                tot["sum"] = {"plus": plus, "minus": minus}
        for row, vals in (enums.get(sec["sheet"]) or {}).items():
            it = by_row.get(row)
            if it and it["type"] not in ("booleanItemType", "monetaryItemType", "decimalItemType", "sharesItemType", "percentItemType", "perShareItemType", "dateItemType"):
                it["enum"] = vals
    for t in tables.values():
        by_row = {it["row"]: it for it in t["items"]}
        for row, vals in (enums.get(t["sheet"]) or {}).items():
            it = by_row.get(row)
            if it and it["type"] not in ("booleanItemType", "monetaryItemType", "decimalItemType", "sharesItemType",
                                         "percentItemType", "perShareItemType", "dateItemType"):
                it["enum"] = vals
    schema = {
        "key": key,
        "source": Path(path).name,
        "sections": sections,
        "tables": tables,
        "rules": read_rules(wb),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{key}.json"
    out.write_text(json.dumps(schema, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n_items = sum(len(x["items"]) for x in sections) + sum(len(t["items"]) for t in tables.values())
    print(f"{key}: {len(sections)} sections, {len(tables)} tables, {n_items} rows, {len(schema['rules'])} rules -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("key", choices=["AS", "INDAS"])
    ap.add_argument("template")
    ap.add_argument("--formulas")
    a = ap.parse_args()
    build(a.key, a.template, a.formulas)
