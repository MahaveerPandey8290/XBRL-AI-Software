"""Entry point for the in-browser version (GitHub Pages, runs on Pyodide).

process() takes uploaded files and filing details and returns validation
results and the generated XML, using the same code as the desktop app.
"""
from __future__ import annotations

import json
import re
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
    servers = [q for q in out if "ComputerServers" in q["element"]]
    if servers:
        out = [q for q in out if "ComputerServers" not in q["element"]]
        sec = servers[0]["section"]
        if any("Phone" in q["element"] for q in servers):
            out.insert(0, {"element": "__phone", "period": "CY", "kind": "text", "options": [], "section": sec,
                           "label": "Company phone number with STD code",
                           "message": "Phone of the place where the books are kept."})
        if any("Phone" not in q["element"] for q in servers):
            out.insert(0, {"element": "__address", "period": "CY", "kind": "text", "options": [], "section": sec,
                           "label": "Registered office address with PIN code (where the books / accounting server are kept)",
                           "message": "Used for the address, city, district, state, PIN and country of the accounting server."})
    return out


def _snapshot(f: Filing) -> dict:
    return {(eid, pk): v for eid, per in f.values.items() for pk, v in per.items()}


class _Sources:
    """Where each value came from, for the review grid."""

    def __init__(self, f: Filing):
        self.f, self.src, self.before = f, {}, _snapshot(f)

    def stage(self, why):
        """Credit every value added or changed since the last stage to `why` (a string,
        or a function of (element, period) returning one)."""
        now = _snapshot(self.f)
        for k, v in now.items():
            if self.before.get(k) != v:
                w = why(*k) if callable(why) else why
                if w:
                    self.src[k] = w
        self.before = now


def _preview(v: str, kind: str) -> str:
    if kind == "textblock":
        t = re.sub(r"\s+", " ", html_to_text(v)).strip()
        return t[:220] + ("..." if len(t) > 220 else "")
    return {"true": "Yes", "false": "No"}.get(v, v)


def _review(f: Filing, schema, sources: dict) -> list[dict]:
    """Every value going into the XML, by section, with where it came from."""
    from .calc import fill_totals
    vals = fill_totals(f, schema)
    out = []
    shown = set()
    for sec in schema.sections:
        rows = []
        for it in sec["items"]:
            eid = it["id"]
            if it.get("abstract") or eid in shown:
                continue
            per = vals.get(eid) or {}
            if not any(per.get(pk) not in (None, "") for pk in ("CY", "PY")):
                continue
            shown.add(eid)
            kind = _kind(it)
            row = {"element": eid, "label": it["label"], "kind": kind}
            for pk in ("CY", "PY"):
                v = per.get(pk)
                if v in (None, ""):
                    continue
                row[pk.lower()] = _preview(str(v), kind)
                row["src_" + pk.lower()] = sources.get((eid, pk)) or (
                    "worked out as the total of its lines" if f.get_value(eid, pk) in (None, "") else "from the uploaded files")
            rows.append(row)
        for tid in sec.get("tables", []):
            for col in f.tables.get(tid, []):
                dims = ", ".join(str(m).split("_", 1)[-1] for m in col.get("dims", {}).values())
                for eid, per in col.get("values", {}).items():
                    el = schema.element(eid) or {"label": eid}
                    row = {"element": eid, "label": f"{el['label']} [{dims}]", "kind": _kind(el)}
                    for pk in ("CY", "PY"):
                        if per.get(pk) not in (None, ""):
                            row[pk.lower()] = _preview(str(per[pk]), row["kind"])
                            row["src_" + pk.lower()] = col.get("src") or "from the uploaded files"
                    if "cy" in row or "py" in row:
                        rows.append(row)
        if rows:
            out.append({"code": sec["code"], "title": sec["title"], "rows": rows})
    return out


def _footnotes_for_others(f: Filing, schema, mapped: list) -> list[str]:
    """TaxPrint explains an 'other ...' amount with a footnote naming what it holds; use the
    workbook captions that were added into it."""
    from .calc import relationships
    totals = relationships(schema)
    caps: dict = {}
    for m in mapped:
        eid = (m.get("element") or "").replace("@start", "")
        local = eid.split("_", 1)[-1]
        el = schema.element(eid)
        if not el or el.get("type") != "monetaryItemType" or eid in totals:
            continue
        if not (local.startswith(("Other", "Miscellaneous")) or local.endswith("Others")):
            continue
        cap = re.sub(r"\s+", " ", re.sub(r"^[\s(]*[a-z0-9ivx]{1,4}[).]\s+", "", str(m.get("caption") or ""), flags=re.I)).strip(" :-")
        words = lambda t: set(re.findall(r"[a-z]{3,}", t.lower())) - {"other", "others", "the", "and"}  # noqa: E731
        if cap and words(cap) and not words(cap) <= words(el.get("label", "")) and not re.match(r"^(total|others?)\b", cap, re.I):
            caps.setdefault(eid, [])
            if cap.lower() not in (c.lower() for c in caps[eid]):
                caps[eid].append(cap)
    n = 0
    for eid, cs in caps.items():
        text = "; ".join(cs)[:500]
        for pk in ("CY", "PY"):
            if f.get_value(eid, pk) not in (None, "") and not f.footnotes.get(eid, {}).get(pk):
                f.footnotes.setdefault(eid, {})[pk] = text
                n += 1
    return [f"{n} footnotes added to 'other' amounts, naming what they hold."] if n else []


def process(files: list, header_json: str) -> str:
    """files: [(name, bytes)]; header: taxonomy, company, cin, fy_start, fy_end, nature, amount_unit,
    values ({element: {period: value}} typed by the user on the page), zero_fill (default true)."""
    from .autofill import balance_with_others, fill_board_report, fill_known_facts, zero_blank_mandatory_amounts
    from .calc import reconcile_rounding
    from .infer import Docs, Inferrer
    from .schema_loader import load_schema

    h = json.loads(header_json or "{}")
    tax = h.get("taxonomy") or None
    f: Filing | None = None
    prior: Filing | None = None
    log: list[str] = []
    notes: list[str] = []
    fin_files = []
    for name, data in files:
        p = _write(name, data)
        ext = p.suffix.lower()
        if ext == ".xml":
            fx, w = read_instance(p, tax)
            if h.get("fy_end") and fx.fy_end and str(fx.fy_end) < str(h["fy_end"]):
                # last year's AOC-4: used only for the previous-year movements and opening balances
                prior = fx
                log.append(f"{name}: last year's filing ({fx.fy_start} to {fx.fy_end}), used for previous-year figures")
                continue
            f = fx
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
    S = _Sources(f)
    S.before = {}
    S.stage("from the uploaded XBRL file / template")
    mapped = []
    filled: set = set()
    texts: list[str] = []
    docs = Docs()
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
        if not is_doc:
            try:
                docs.sheets += [(name, sn, vis, rows) for sn, vis, rows in _sheets(p)]
            except Exception:  # noqa: BLE001
                pass
        has_csr = has_csr or kind == "csr"
        dkind = "annexure" if kind == "directors" and "annexure" in name.lower() else kind
        docs.add(dkind or ("workbook" if not is_doc else "other"), text, body)
        docs.rows += data["rows"]
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
        S.stage(f"read from {name}")
        if not data["rows"]:
            if not kind:
                log.append(f"{name}: no balance sheet / profit and loss figures and not recognised as a report. "
                           "Name the file e.g. 'Directors Report', 'Auditors Report', 'Notes to accounts' or 'CSR'.")
            continue
        if not is_doc:  # the workbook's own words (PAN, address ...) help fill general information
            texts.append(data.get("text", ""))
            docs.add("workbook", data.get("text", ""))
        has_cf = has_cf or any(r["kind"] == "cf" for r in data["rows"])
        scale = max(scale, data["scale"])
        docs.scale = max(docs.scale, data["scale"])
        mapping = [{"element": r["best"], "cy": r["cy"], "py": r["py"], "sheet": r["sheet"], "caption": r["caption"], "kind": r["kind"]}
                   for r in data["rows"] if r["best"]]
        n = apply_mapping(f, mapping, data["scale"])
        by_el: dict = {}
        for m in mapping:
            e = m["element"].replace("@start", "")
            by_el.setdefault(e, []).append(f"{m['sheet']}: {m['caption']}")
        S.stage(lambda eid, pk, by_el=by_el, name=name: (
            "; ".join(dict.fromkeys(by_el[eid]))[:160] if eid in by_el else f"read from {name}"))
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
    S.stage("from the filing details you entered")
    if fin_files:
        notes += fill_known_facts(f, schema, texts, scale or 1, has_cf, has_csr)
        S.stage("general information found in the documents")
        extra = h.get("values") or {}
        inf = Inferrer(f, schema, docs, S.src, address=(extra.get("__address") or {}).get("CY") or h.get("address") or "",
                       phone=(extra.get("__phone") or {}).get("CY") or h.get("phone") or "")
        notes += inf.run()
        S.before = _snapshot(f)
    else:
        inf = None
    # answers typed on the page win over anything read from the files
    for eid, per in (h.get("values") or {}).items():
        if schema.element(eid) is None:
            continue
        for pk, v in per.items():
            if v not in (None, ""):
                f.set_value(eid, pk, str(v))
    S.stage("your answer / correction")
    from .autofill import fill_both_years
    notes += fill_both_years(f, schema)
    S.stage(lambda eid, pk: "same answer as the current year" if pk == "PY" else None)
    if mapped:
        notes += reconcile_rounding(f, schema, scale=scale or 1)
        S.stage("adjusted for rounding so the lines add up to the total")
        notes += balance_with_others(f, schema)
        S.stage("the part of the total the notes don't break down")
        notes += reconcile_rounding(f, schema, scale=scale or 1)
        S.stage("adjusted for rounding so the lines add up to the total")
        from .calc import relationships
        if inf:
            inf.nature_of_others(relationships(schema))
        S.stage(None)
    unit = 1
    if mapped and h.get("round_amounts", True) and (scale or 1) > 1:
        # present the amounts as the statements do (e.g. whole lakhs), like TaxPrint
        from .calc import round_to_unit
        unit = scale
        notes += round_to_unit(f, schema, unit)
        S.stage("rounded to the level used in the financial statements")
    missing = []
    if fin_files:
        from .tables import Tables, apply_table_answers
        tb = Tables(f, schema, docs, S.src, round_unit=unit, prior=prior)
        notes += tb.run()
        S.stage("from the notes to accounts (note tables)")
        apply_table_answers(f, schema, h.get("values") or {})
        answered = {k for k, per in (h.get("values") or {}).items() if any(v not in (None, "") for v in (per or {}).values())}
        missing = [q for q in tb.missing if q["element"] not in answered]
    if mapped:
        notes += _footnotes_for_others(f, schema, mapped)
    res = validate(f)
    if h.get("zero_fill", True):
        z = zero_blank_mandatory_amounts(f, schema, res)
        from .tables import py_zero_counterparts
        if py_zero_counterparts(f, schema, S.src):
            z = z or ["Amounts that are zero this year set to zero for last year too (MCA needs both years)."]
        if z:
            notes += z
            S.stage("required by MCA but not in the documents: set to 0")
            res = validate(f)
    xml, warns = generate(f)
    return json.dumps({
        "taxonomy": f.taxonomy, "company": f.company, "cin": f.cin, "fy_start": f.fy_start, "fy_end": f.fy_end,
        "nature": f.nature, "errors": res.errors, "warnings": res.warnings, "log": log, "mapped": mapped,
        "xml": xml, "filename": output_filename(f), "facts": xml.count('contextRef="'), "notes": notes + warns,
        "inputs": _inputs(res, schema) + missing, "review": _review(f, schema, S.src),
    })
