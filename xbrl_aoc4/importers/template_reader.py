"""Import a filled InstantXBRL / TaxPrint style Excel template (.xls or .xlsx).

Every data sheet lists element ids in column A and the Current Year /
Previous Year values in columns L / M. Table sheets carry one column pair per
member combination; the member labels are typed in the axis rows above the
header row. Text blocks point at cells of the hidden ``TextBlocks`` sheet.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..model import Filing
from ..schema_loader import load_schema

TB_REF = re.compile(r"^TextBlocks!R(\d+)C(\d+)$")


class _Sheet:
    """Uniform cell access over xlrd / openpyxl sheets (0-based)."""

    def __init__(self, name, getter, nrows, ncols):
        self.name, self._get, self.nrows, self.ncols = name, getter, nrows, ncols

    def v(self, r, c):
        if r >= self.nrows or c >= self.ncols:
            return ""
        val = self._get(r, c)
        if val is None:
            return ""
        if isinstance(val, float) and val.is_integer():
            return str(int(val))
        return str(val).strip() if not isinstance(val, (int, float)) else str(val)


def _open(path: Path):
    path = Path(path)
    if path.suffix.lower() == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path), on_demand=True)
        for sh in wb.sheets():
            yield _Sheet(sh.name, sh.cell_value, sh.nrows, sh.ncols)
    else:
        import openpyxl
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            ncols = max((len(r) for r in rows), default=0)
            yield _Sheet(ws.title, lambda r, c, rows=rows: rows[r][c] if c < len(rows[r]) else None, len(rows), ncols)


def _clean_member(label: str) -> str:
    return label.replace("[Total]", "").strip()


def read_template(path, taxonomy: str | None = None, filing: Filing | None = None) -> tuple[Filing, list[str]]:
    sheets = {s.name: s for s in _open(path)}
    warnings: list[str] = []
    if taxonomy is None:
        taxonomy = "INDAS" if any(n.startswith("110000") for n in sheets) else "AS"
    schema = load_schema(taxonomy)
    f = filing or Filing(taxonomy=taxonomy)
    tb = sheets.get("TextBlocks")

    def resolve(val: str) -> str:
        m = TB_REF.match(val or "")
        if m and tb is not None:
            return tb.v(int(m.group(1)) - 1, int(m.group(2)) - 1)
        if m:
            return ""
        return val

    def keys_for(sh, r):
        # the same element can sit on an opening-balance row and a closing row
        if sh.v(r, 2).rsplit("/", 1)[-1].startswith("periodStart"):
            return ("PY", "OPEN")
        return ("CY", "PY")

    n_vals = 0
    for name, sh in sheets.items():
        marker = sh.v(0, 0)
        if marker == "ValidSheet":
            for r in range(4, sh.nrows):
                eid = sh.v(r, 0)
                el = schema.element(eid)
                if not el or el.get("abstract") or el.get("table"):
                    continue
                k_cy, k_py = keys_for(sh, r)
                for col, pk in ((11, k_cy), (12, k_py)):
                    val = resolve(sh.v(r, col))
                    if val in ("", "Click Here"):
                        continue
                    f.set_value(eid, pk, val)
                    n_vals += 1
        elif marker == "ValidTable":
            tid = sh.v(1, 0)
            table = schema.tables.get(tid)
            if not table:
                warnings.append(f"Sheet '{name}': table {tid} is not in the {taxonomy} template")
                continue
            hdr = next((r for r in range(3, min(sh.nrows, 40)) if sh.v(r, 0) == "ElementID"), None)
            if hdr is None:
                continue
            axis_rows = [(r, sh.v(r, 0)) for r in range(3, hdr) if sh.v(r, 0)]
            axes = {a["id"]: a for a in table["axes"]}
            for k in range((sh.ncols - 11) // 2 + 1):
                c_cy, c_py = 11 + 2 * k, 12 + 2 * k
                vals = {}
                for r in range(hdr + 1, sh.nrows):
                    eid = sh.v(r, 0)
                    el = schema.element(eid)
                    if not el or el.get("abstract"):
                        continue
                    k_cy, k_py = keys_for(sh, r)
                    for col, pk in ((c_cy, k_cy), (c_py, k_py)):
                        val = resolve(sh.v(r, col))
                        if val in ("",):
                            continue
                        vals.setdefault(eid, {})[pk] = val
                # a column holding only zero formula totals was never filled in
                if all(str(v) in ("0", "0.0") for per in vals.values() for v in per.values()):
                    continue
                if not vals:
                    continue
                dims = {}
                for r, aid in axis_rows:
                    lab = sh.v(r, c_cy) or sh.v(r, c_py)
                    ax = axes.get(aid)
                    if not ax:
                        continue
                    if ax.get("typedDomain"):
                        dims[aid] = lab or f"Column {k + 1}"
                        continue
                    if not lab:
                        continue  # default (total) member
                    want = _clean_member(lab)
                    mem = next((m["id"] for m in ax["members"] if m["label"] == want), None)
                    if mem is None:
                        warnings.append(f"Sheet '{name}' column {k + 1}: member '{lab}' not found on {ax['label']}")
                        continue
                    if mem != ax.get("default"):
                        dims[aid] = mem
                row = f.table_row(tid, dims)
                for eid, per in vals.items():
                    row["values"].setdefault(eid, {}).update(per)
                    n_vals += len(per)
    # general information drives the filing header
    g = lambda n: f.get_value(f"in-ca_{n}", "CY")  # noqa: E731
    f.cin = f.cin or (g("CorporateIdentityNumber") or "")
    f.company = f.company or (g("NameOfCompany") or "")
    if g("DateOfStartOfReportingPeriod"):
        f.fy_start = g("DateOfStartOfReportingPeriod")
    if g("DateOfEndOfReportingPeriod"):
        f.fy_end = g("DateOfEndOfReportingPeriod")
    if f.get_value("in-ca_DateOfStartOfReportingPeriod", "PY"):
        f.py_start = f.get_value("in-ca_DateOfStartOfReportingPeriod", "PY")
    if f.get_value("in-ca_DateOfEndOfReportingPeriod", "PY"):
        f.py_end = f.get_value("in-ca_DateOfEndOfReportingPeriod", "PY")
    if g("NatureOfReportStandaloneConsolidated"):
        f.nature = g("NatureOfReportStandaloneConsolidated")
    f.ensure_periods()
    warnings.insert(0, f"Imported {n_vals} values from {Path(path).name}")
    return f, warnings
