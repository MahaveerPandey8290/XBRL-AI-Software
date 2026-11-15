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
from .importers.template_reader import read_template
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


def process(files: list, header_json: str) -> str:
    """files: [(name, bytes)]; header: taxonomy, company, cin, fy_start, fy_end, nature, amount_unit."""
    h = json.loads(header_json or "{}")
    tax = h.get("taxonomy") or None
    f: Filing | None = None
    log: list[str] = []
    fin_files = []
    for name, data in files:
        p = _write(name, data)
        ext = p.suffix.lower()
        if ext == ".xml":
            f, w = read_instance(p, tax)
            log += [f"{name}: read {len(f.values)} items and {sum(len(r) for r in f.tables.values())} table columns"] + w[:20]
        elif ext in (".xls", ".xlsx"):
            # an InstantXBRL template has 'ValidSheet' sheets; anything else is a company workbook
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
    mapped = []
    from .schema_loader import load_schema
    schema = load_schema(f.taxonomy)
    filled: set = set()
    for name, p in fin_files:
        ext = p.suffix.lower()
        is_doc = ext not in (".xls", ".xlsx")
        try:
            data = read_financials(p, f.taxonomy) if ext not in (".txt", ".htm", ".html") else {"rows": [], "scale": 1}
            body = document_html(p) if is_doc else ""
        except Exception as e:  # noqa: BLE001
            log.append(f"{name}: could not be read ({e})")
            continue
        if is_doc and ext == ".pdf" and not html_to_text(body).strip():
            log.append(f"{name}: no text found; it may be a scanned PDF (needs a text PDF or the Word file)")
            continue
        kind = classify_document(name, html_to_text(body) if body else "")
        if kind and not data["rows"] and not is_doc:
            body = _sheets_html(p)
        if kind:
            eid = text_block_for(schema, kind)
            if eid and body:
                prev = f.get_value(eid, "CY") if eid in filled else ""
                main_first = prev and "annexure" not in name.lower()
                f.set_value(eid, "CY", (body + "\n" + prev) if main_first else (prev + "\n" + body) if prev else body)
                filled.add(eid)
                log.append(f"{name}: put into '{schema.element(eid)['label']}'")
        if not data["rows"]:
            if not kind:
                log.append(f"{name}: no balance sheet / profit and loss figures and not recognised as a report. "
                           "Name the file e.g. 'Directors Report', 'Auditors Report', 'Notes to accounts' or 'CSR'.")
            continue
        mapping = [{"element": r["best"], "cy": r["cy"], "py": r["py"]} for r in data["rows"] if r["best"]]
        n = apply_mapping(f, mapping, data["scale"])
        log.append(f"{name}: mapped {len(mapping)} of {len(data['rows'])} rows automatically (scale {data['scale']}), {n} values")
        mapped += [{"caption": r["caption"], "cy": r["cy"], "py": r["py"], "element": r["best"],
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
    res = validate(f)
    xml, warns = generate(f)
    return json.dumps({
        "taxonomy": f.taxonomy, "company": f.company, "cin": f.cin, "fy_start": f.fy_start, "fy_end": f.fy_end,
        "nature": f.nature, "errors": res.errors, "warnings": res.warnings, "log": log, "mapped": mapped,
        "xml": xml, "filename": output_filename(f), "facts": xml.count('contextRef="'), "notes": warns,
    })
