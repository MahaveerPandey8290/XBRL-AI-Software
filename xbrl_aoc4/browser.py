"""Entry point for the in-browser version (GitHub Pages, runs on Pyodide).

process() takes uploaded files and filing details and returns validation
results and the generated XML, using the same code as the desktop app.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .generator import generate, output_filename
from .importers.docs_reader import classify_document, document_html, html_to_text, text_block_for
from .importers.financials_reader import _sheets, apply_mapping, read_financials
from .importers.instance_reader import read_instance
from .importers.template_reader import looks_like_template, read_template
from .model import Filing
from .validator import validate


def _write(name: str, data: bytes) -> Path:
    p = Path(tempfile.mkdtemp()) / name
    p.write_bytes(bytes(data))
    return p


def _sheets_html(p: Path) -> str:
    """A workbook's visible sheets as HTML tables (for a report kept in Excel)."""
    import html
    out = []
    for _name, visible, rows in _sheets(p):
        if not visible:
            continue
        trs = "".join("<tr>" + "".join(f"<td>{html.escape('' if v is None else str(v))}</td>" for v in r) + "</tr>"
                      for r in rows if any(v not in (None, "") for v in r))
        if trs:
            out.append(f'<table border="1">{trs}</table>')
    return "\n".join(out)


NUMERIC_TYPES = ("monetaryItemType", "sharesItemType", "decimalItemType", "perShareItemType", "pureItemType",
                 "percentItemType", "nonNegativeIntegerItemType", "integerItemType")


def _kind(el: dict) -> str:
    t = el.get("type", "")
    if t == "textBlockItemType":
        return "textblock"
    if t == "booleanItemType":
        return "bool"
    if t == "dateItemType":
        return "date"
    if t in NUMERIC_TYPES or t.endswith("IntegerItemType"):
        return "number"
    if el.get("enum"):
        return "enum"
    return "text"


def _inputs(res, schema) -> list[dict]:
    """Questions for the web page: each value still missing, with the kind of answer it takes."""
    out, seen = [], set()
    for e in res.errors:
        if e["code"] not in ("MANDATORY", "DEPENDENCY") or e.get("table") or not e.get("element"):
            continue
        el = schema.element(e["element"])
        if not el:
            continue
        pk = e.get("period") or "CY"
        key = (e["element"], pk)
        if key in seen:
            continue
        seen.add(key)
        sec = schema.section_by_code.get(e.get("section", ""), {})
        out.append({"element": e["element"], "period": pk, "label": el["label"], "kind": _kind(el),
                    "options": el.get("enum") or [], "section": sec.get("title", ""), "message": e["message"]})
    return out


def process(files: list, header_json: str) -> str:
    """files: [(name, bytes)]; header: taxonomy, company, cin, fy_start, fy_end, nature, amount_unit,
    values ({element: {period: value}} typed by the user on the page), zero_fill (default true)."""
    from .autofill import balance_with_others, fill_board_report, fill_known_facts, zero_blank_mandatory_amounts
    from .calc import reconcile_rounding
    from .schema_loader import load_schema

    h = json.loads(header_json or "{}")
    tax = h.get("taxonomy") or None
    f: Filing | None = None
    log: list[str] = []
    notes: list[str] = []
    fin_files = []
    for name, data in files:
        p = _write(name, data)
        ext = p.suffix.lower()
        if ext == ".xml":
            f, w = read_instance(p, tax)
            log += [f"{name}: read {len(f.values)} items and {sum(len(r) for r in f.tables.values())} table columns"] + w[:20]
        elif ext in (".xls", ".xlsx"):
            # an InstantXBRL template has 'ValidSheet' sheets; anything else is a company workbook
            n = 0
            if looks_like_template(p):
                try:
                    f2, w = read_template(p, tax, f)
                    n = int(w[0].split()[1]) if w and w[0].startswith("Imported") else 0
                except Exception:  # noqa: BLE001
                    n = 0
            if n > 0:
                f = f2
                log += [f"{name}: {w[0]}"] + w[1:20]
            else:
                fin_files.append((name, p))
        elif ext in (".docx", ".pdf", ".doc", ".txt", ".htm", ".html"):
            fin_files.append((name, p))
        else:
            log.append(f"{name}: this file type is not supported (use .xml, .xls, .xlsx, .docx, .pdf)")
    if f is None:
        f = Filing(taxonomy=tax or "AS")
    for k in ("company", "cin", "fy_start", "fy_end", "nature", "amount_unit"):
        if h.get(k):
            setattr(f, k, h[k].strip().upper() if k == "cin" else h[k])
    if h.get("taxonomy") and not files:
        f.taxonomy = h["taxonomy"]
    f.ensure_periods()
    schema = load_schema(f.taxonomy)
    mapped = []
    filled: set = set()
    texts: list[str] = []
    scale = 0
    has_cf = has_csr = False
    for name, p in fin_files:
        ext = p.suffix.lower()
        is_doc = ext not in (".xls", ".xlsx")
        try:
            data = read_financials(p, f.taxonomy) if ext not in (".txt", ".htm", ".html") else {"rows": [], "scale": 1}
            body = document_html(p) if is_doc else ""
        except Exception as e:  # noqa: BLE001
            log.append(f"{name}: could not be read ({e})")
            continue
        text = html_to_text(body) if body else ""
        if is_doc and ext == ".pdf" and not text.strip():
            log.append(f"{name}: no text found; it may be a scanned PDF (needs a text PDF or the Word file)")
            continue
        kind = classify_document(name, text)
        if kind and not data["rows"] and not is_doc:
            body = _sheets_html(p)
            text = html_to_text(body)
        texts.append(text)
        has_csr = has_csr or kind == "csr"
        if kind:
            eid = text_block_for(schema, kind)
            if eid and body:
                prev = f.get_value(eid, "CY") if eid in filled else ""
                main_first = prev and "annexure" not in name.lower()
                f.set_value(eid, "CY", (body + "\n" + prev) if main_first else (prev + "\n" + body) if prev else body)
                filled.add(eid)
                log.append(f"{name}: put into '{schema.element(eid)['label']}'")
            if kind == "directors" and body and "annexure" not in name.lower():
                notes += fill_board_report(f, schema, body)
        if not data["rows"]:
            if not kind:
                log.append(f"{name}: no balance sheet / profit and loss figures and not recognised as a report. "
                           "Name the file e.g. 'Directors Report', 'Auditors Report', 'Notes to accounts' or 'CSR'.")
            continue
        if not is_doc:  # the workbook's own words (PAN, address ...) help fill general information
            texts.append(data.get("text", ""))
        has_cf = has_cf or any(r["kind"] == "cf" for r in data["rows"])
        scale = max(scale, data["scale"])
        mapping = [{"element": r["best"], "cy": r["cy"], "py": r["py"], "sheet": r["sheet"]} for r in data["rows"] if r["best"]]
        n = apply_mapping(f, mapping, data["scale"])
        log.append(f"{name}: mapped {len(mapping)} of {len(data['rows'])} rows automatically (scale {data['scale']}), {n} values")
        mapped += [{"caption": r["caption"], "cy": r["cy"], "py": r["py"], "element": r["best"], "sheet": r["sheet"],
                    "label": (r["suggestions"][0]["label"] if r["best"] and r["suggestions"] else "")} for r in data["rows"]]
    # header facts
    if f.company:
        f.set_value("in-ca_NameOfCompany", "CY", f.company)
    if f.cin:
        f.set_value("in-ca_CorporateIdentityNumber", "CY", f.cin)
    if f.fy_start and not f.get_value("in-ca_DateOfStartOfReportingPeriod", "CY"):
        f.set_value("in-ca_DateOfStartOfReportingPeriod", "CY", f.fy_start)
        f.set_value("in-ca_DateOfEndOfReportingPeriod", "CY", f.fy_end)
    f.set_value("in-ca_NatureOfReportStandaloneConsolidated", "CY", f.nature)
    if fin_files:
        notes += fill_known_facts(f, schema, texts, scale or 1, has_cf, has_csr)
    # answers typed on the page win over anything read from the files
    for eid, per in (h.get("values") or {}).items():
        if schema.element(eid) is None:
            continue
        for pk, v in per.items():
            if v not in (None, ""):
                f.set_value(eid, pk, str(v))
    if mapped:
        notes += reconcile_rounding(f, schema, scale=scale or 1)
        notes += balance_with_others(f, schema)
    res = validate(f)
    if h.get("zero_fill", True):
        z = zero_blank_mandatory_amounts(f, schema, res)
        if z:
            notes += z
            res = validate(f)
    xml, warns = generate(f)
    return json.dumps({
        "taxonomy": f.taxonomy, "company": f.company, "cin": f.cin, "fy_start": f.fy_start, "fy_end": f.fy_end,
        "nature": f.nature, "errors": res.errors, "warnings": res.warnings, "log": log, "mapped": mapped,
        "xml": xml, "filename": output_filename(f), "facts": xml.count('contextRef="'), "notes": notes + warns,
        "inputs": _inputs(res, schema),
    })
