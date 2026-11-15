"""Local web interface for preparing AOC-4 XBRL instance documents.

Run with ``python run.py`` (opens http://127.0.0.1:5050 in the browser).
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import threading
import webbrowser
from pathlib import Path

from flask import (Flask, abort, jsonify, redirect, render_template, request, send_file, url_for)
from werkzeug.utils import secure_filename

from .. import storage
from ..calc import fill_totals
from ..config import TAXONOMIES
from ..generator import AMOUNT_SCALE, generate, output_filename
from ..importers.docs_reader import document_html
from ..importers.financials_reader import apply_mapping, read_financials
from ..importers.instance_reader import read_instance
from ..importers.template_reader import read_template
from ..model import Filing
from ..periods import PeriodResolver
from ..schema_loader import load_schema
from ..taxonomy import download as download_taxonomy
from ..taxonomy import status as taxonomy_status
from ..validator import validate
from .enums import ENUMS

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
_lock = threading.Lock()


# --------------------------------------------------------------------- helpers
def get_filing(fid):
    try:
        return storage.load(fid)
    except (FileNotFoundError, ValueError):
        abort(404)


def nav_for(schema):
    out = []
    for sec in schema.sections:
        out.append({"code": sec["code"], "title": sec["title"], "hidden": sec["hidden"],
                    "tables": [{"id": t, "title": schema.tables[t]["title"]} for t in sec["tables"]]})
    return out


def period_keys(it):
    return ("PY", "OPEN") if it.get("labelRole", "").startswith("periodStart") else ("CY", "PY")


def field_kind(it):
    t = it.get("type", "")
    if t == "textBlockItemType":
        return "textblock"
    if t == "booleanItemType":
        return "bool"
    if t == "dateItemType":
        return "date"
    if t in ("monetaryItemType", "sharesItemType", "decimalItemType", "percentItemType", "perShareItemType",
             "pureItemType") or t.endswith("IntegerItemType"):
        return "number"
    if t in ENUMS:
        return "enum"
    return "text"


@app.context_processor
def inject():
    return {"TAXONOMIES": TAXONOMIES, "ENUMS": ENUMS, "field_kind": field_kind, "period_keys": period_keys,
            "AMOUNT_UNITS": list(AMOUNT_SCALE)}


# --------------------------------------------------------------------- home
@app.route("/")
def home():
    return render_template("home.html", filings=storage.list_filings(),
                           tax_status={k: taxonomy_status(k) for k in TAXONOMIES})


@app.post("/new")
def new_filing():
    fm = request.form
    f = Filing(taxonomy=fm.get("taxonomy", "AS"), cin=fm.get("cin", "").strip().upper(),
               company=fm.get("company", "").strip(), fy_start=fm.get("fy_start", ""), fy_end=fm.get("fy_end", ""),
               nature=fm.get("nature", "Standalone"), amount_unit=fm.get("amount_unit", "Rupees"))
    f.ensure_periods()
    _sync_general(f)
    fid = storage.create(f)
    return redirect(url_for("editor", fid=fid))


def _sync_general(f: Filing):
    """Keep the general-information facts in line with the filing header."""
    if f.company:
        f.set_value("in-ca_NameOfCompany", "CY", f.company)
    if f.cin:
        f.set_value("in-ca_CorporateIdentityNumber", "CY", f.cin)
    if f.fy_start:
        f.set_value("in-ca_DateOfStartOfReportingPeriod", "CY", f.fy_start)
        f.set_value("in-ca_DateOfEndOfReportingPeriod", "CY", f.fy_end)
        f.set_value("in-ca_DateOfStartOfReportingPeriod", "PY", f.py_start)
        f.set_value("in-ca_DateOfEndOfReportingPeriod", "PY", f.py_end)
        fmt = lambda d: "-".join(reversed(d.split("-")))  # noqa: E731
        f.set_value("in-ca_PeriodCoveredByFinancialStatements", "CY", f"{fmt(f.fy_start)} To {fmt(f.fy_end)}")
        f.set_value("in-ca_PeriodCoveredByFinancialStatements", "PY", f"{fmt(f.py_start)} To {fmt(f.py_end)}")
    f.set_value("in-ca_NatureOfReportStandaloneConsolidated", "CY", f.nature)
    if not f.get_value("in-ca_DescriptionOfPresentationCurrency", "CY"):
        f.set_value("in-ca_DescriptionOfPresentationCurrency", "CY", "INR")


def _save_upload(file) -> Path:
    suffix = Path(secure_filename(file.filename or "upload")).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file.save(tmp.name)
    return Path(tmp.name)


@app.post("/import")
def import_file():
    file = request.files.get("file")
    if not file or not file.filename:
        return redirect(url_for("home"))
    kind = request.form.get("kind", "auto")
    tax = request.form.get("taxonomy") or None
    p = _save_upload(file)
    try:
        if p.suffix.lower() == ".xml" or kind == "xml":
            f, warns = read_instance(p, tax)
            if request.form.get("rollforward"):
                f = rollforward(f)
                warns.insert(0, "Rolled forward: last year's current-year figures are now the previous-year figures.")
        else:
            f, warns = read_template(p, tax)
    finally:
        p.unlink(missing_ok=True)
    f.ensure_periods()
    fid = storage.create(f)
    storage.save(fid, f)
    (storage._dir(fid) / "import_log.json").write_text(json.dumps(warns[:500], indent=1))
    return redirect(url_for("editor", fid=fid, imported=1))


def rollforward(old: Filing) -> Filing:
    """Create next year's filing from last year's instance: CY -> PY, PY -> OPEN-ish."""
    from datetime import date, timedelta
    f = Filing(taxonomy=old.taxonomy, cin=old.cin, company=old.company, nature=old.nature,
               decimals=old.decimals, amount_unit="Rupees")
    fs = date.fromisoformat(old.fy_end) + timedelta(days=1)
    f.fy_start = fs.isoformat()
    f.fy_end = fs.replace(year=fs.year + 1).replace(day=1).isoformat()
    fe = date.fromisoformat(old.fy_end)
    f.fy_end = fe.replace(year=fe.year + 1).isoformat()
    f.ensure_periods()
    schema = load_schema(old.taxonomy)
    per = PeriodResolver(schema)
    for eid, vals in old.values.items():
        el = schema.element(eid) or {}
        if "CY" in vals and field_kind(el) in ("number",):
            f.set_value(eid, "PY", vals["CY"])
            if per(eid) == "instant" and "PY" in vals:
                f.set_value(eid, "OPEN", vals["PY"])
        elif "CY" in vals and field_kind(el) in ("text", "enum", "bool", "textblock"):
            f.set_value(eid, "CY", vals["CY"])
    for tid, rows in old.tables.items():
        for row in rows:
            nr = f.table_row(tid, row["dims"])
            for eid, vals in row["values"].items():
                el = schema.element(eid) or {}
                if "CY" in vals:
                    if field_kind(el) == "number":
                        nr["values"].setdefault(eid, {})["PY"] = vals["CY"]
                    else:
                        nr["values"].setdefault(eid, {})["CY"] = vals["CY"]
    _sync_general(f)
    return f


@app.post("/f/<fid>/delete")
def delete_filing(fid):
    storage.delete(fid)
    return redirect(url_for("home"))


# --------------------------------------------------------------------- editor
@app.route("/f/<fid>")
def editor(fid):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    log = []
    lp = storage._dir(fid) / "import_log.json"
    if request.args.get("imported") and lp.exists():
        log = json.loads(lp.read_text())
    return render_template("editor.html", fid=fid, f=f, nav=nav_for(schema), log=log, schema=schema,
                           filled=_filled_counts(f, schema))


def _filled_counts(f, schema):
    out = {}
    for sec in schema.sections:
        out[sec["code"]] = sum(1 for it in sec["items"] if it["id"] in f.values)
    for tid in schema.tables:
        out[tid] = len(f.tables.get(tid, []))
    return out


@app.route("/f/<fid>/s/<code>")
def section(fid, code):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    sec = schema.section_by_code.get(code) or abort(404)
    computed = fill_totals(f, schema)
    return render_template("section.html", fid=fid, f=f, sec=sec, schema=schema, computed=computed,
                           nav=nav_for(schema), docs=_docs(fid), filled=_filled_counts(f, schema))


@app.route("/f/<fid>/t/<tid>")
def table(fid, tid):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    t = schema.tables.get(tid) or abort(404)
    rows = f.tables.get(tid, [])
    sec_code = next((s["code"] for s in schema.sections if tid in s["tables"]), "")
    return render_template("table.html", fid=fid, f=f, t=t, tid=tid, rows=rows, schema=schema, sec_code=sec_code,
                           nav=nav_for(schema), filled=_filled_counts(f, schema), docs=_docs(fid))


@app.route("/f/<fid>/header", methods=["GET", "POST"])
def header(fid):
    f = get_filing(fid)
    if request.method == "POST":
        fm = request.form
        f.company = fm.get("company", "").strip()
        f.cin = fm.get("cin", "").strip().upper()
        f.fy_start, f.fy_end = fm.get("fy_start", ""), fm.get("fy_end", "")
        f.py_start, f.py_end = fm.get("py_start", ""), fm.get("py_end", "")
        f.nature = fm.get("nature", "Standalone")
        f.amount_unit = fm.get("amount_unit", "Rupees")
        f.decimals = fm.get("decimals", "INF") or "INF"
        f.ensure_periods()
        _sync_general(f)
        storage.save(fid, f)
        return redirect(url_for("editor", fid=fid))
    schema = load_schema(f.taxonomy)
    return render_template("header.html", fid=fid, f=f, nav=nav_for(schema), filled=_filled_counts(f, schema))


# --------------------------------------------------------------------- api
@app.post("/api/f/<fid>/value")
def api_value(fid):
    d = request.get_json(force=True)
    with _lock:
        f = get_filing(fid)
        eid, pk, val = d["eid"], d["pk"], d.get("value")
        tid = d.get("tid")
        if tid:
            rows = f.tables.setdefault(tid, [])
            i = int(d["col"])
            if i >= len(rows):
                abort(400)
            per = rows[i]["values"].setdefault(eid, {})
            if val in (None, ""):
                per.pop(pk, None)
                if not per:
                    rows[i]["values"].pop(eid, None)
            else:
                per[pk] = val
        else:
            f.set_value(eid, pk, val)
        storage.save(fid, f)
    return jsonify(ok=True)


@app.post("/api/f/<fid>/table/<tid>/add")
def api_table_add(fid, tid):
    d = request.get_json(force=True)
    with _lock:
        f = get_filing(fid)
        schema = load_schema(f.taxonomy)
        t = schema.tables.get(tid) or abort(404)
        dims = {}
        for a in t["axes"]:
            m = (d.get("dims") or {}).get(a["id"], "")
            if m and m != a.get("default"):
                dims[a["id"]] = m
        rows = f.tables.setdefault(tid, [])
        if any(r["dims"] == dims for r in rows):
            return jsonify(ok=False, error="A column with the same member(s) already exists."), 400
        rows.append({"dims": dims, "values": {}})
        storage.save(fid, f)
    return jsonify(ok=True)


@app.post("/api/f/<fid>/table/<tid>/remove")
def api_table_remove(fid, tid):
    d = request.get_json(force=True)
    with _lock:
        f = get_filing(fid)
        rows = f.tables.get(tid, [])
        i = int(d["col"])
        if 0 <= i < len(rows):
            rows.pop(i)
        if not rows:
            f.tables.pop(tid, None)
        storage.save(fid, f)
    return jsonify(ok=True)


# --------------------------------------------------------------------- documents
def _docs(fid):
    d = storage.docs_dir(fid)
    return sorted(p.name for p in d.iterdir() if p.is_file())


@app.route("/f/<fid>/documents", methods=["GET", "POST"])
def documents(fid):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    if request.method == "POST":
        for file in request.files.getlist("files"):
            if file and file.filename:
                name = secure_filename(file.filename) or "document"
                file.save(storage.docs_dir(fid) / name)
        return redirect(url_for("documents", fid=fid))
    textblocks = [it for sec in schema.sections for it in sec["items"] if it["type"] == "textBlockItemType"]
    return render_template("documents.html", fid=fid, f=f, docs=_docs(fid), textblocks=textblocks,
                           nav=nav_for(schema), filled=_filled_counts(f, schema))


@app.post("/f/<fid>/documents/<name>/delete")
def delete_doc(fid, name):
    p = storage.docs_dir(fid) / secure_filename(name)
    p.unlink(missing_ok=True)
    return redirect(url_for("documents", fid=fid))


@app.route("/api/f/<fid>/doc/<name>")
def api_doc_html(fid, name):
    p = storage.docs_dir(fid) / secure_filename(name)
    if not p.exists():
        abort(404)
    return jsonify(html=document_html(p))


@app.post("/f/<fid>/documents/assign")
def assign_doc(fid):
    f = get_filing(fid)
    name = request.form.get("doc", "")
    eid = request.form.get("element", "")
    pk = request.form.get("pk", "CY")
    p = storage.docs_dir(fid) / secure_filename(name)
    if p.exists() and eid:
        try:
            f.set_value(eid, pk, document_html(p))
        except ValueError as e:  # e.g. an old .doc file
            return redirect(url_for("documents", fid=fid, error=str(e)))
        storage.save(fid, f)
    return redirect(request.form.get("back") or url_for("documents", fid=fid))


@app.route("/f/<fid>/financials/<name>", methods=["GET", "POST"])
def financials(fid, name):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    p = storage.docs_dir(fid) / secure_filename(name)
    if not p.exists():
        abort(404)
    data = read_financials(p, f.taxonomy)
    if request.method == "POST":
        scale = int(request.form.get("scale") or 1)
        mapping = []
        for i, row in enumerate(data["rows"]):
            el = request.form.get(f"el_{i}", "")
            if el and request.form.get(f"use_{i}"):
                mapping.append({"element": el, "cy": row["cy"], "py": row["py"], "sheet": row["sheet"]})
        n = apply_mapping(f, mapping, scale)
        from ..autofill import balance_with_others
        from ..calc import reconcile_rounding
        reconcile_rounding(f, schema, scale=scale)
        balance_with_others(f, schema)
        storage.save(fid, f)
        return redirect(url_for("editor", fid=fid, applied=n))
    options = {}
    from ..importers.financials_reader import Matcher
    m = Matcher(f.taxonomy)
    for kind in ("bs", "pl", "cf", "note"):
        options[kind] = [{"id": c["key"], "label": c["label"] + (" (opening)" if c["key"].endswith("@start") else "")}
                         for c in m.candidates(kind)]
    return render_template("financials.html", fid=fid, f=f, name=name, data=data, options=options,
                           nav=nav_for(schema), filled=_filled_counts(f, schema))


# --------------------------------------------------------------------- validate / generate
@app.route("/f/<fid>/validate")
def validate_view(fid):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    res = validate(f)
    return render_template("validate.html", fid=fid, f=f, res=res, schema=schema, nav=nav_for(schema),
                           filled=_filled_counts(f, schema))


@app.route("/f/<fid>/generate", methods=["GET", "POST"])
def generate_view(fid):
    f = get_filing(fid)
    schema = load_schema(f.taxonomy)
    res = validate(f)
    force = request.values.get("force") == "1"
    if res.errors and not force:
        return render_template("validate.html", fid=fid, f=f, res=res, schema=schema, nav=nav_for(schema),
                               filled=_filled_counts(f, schema), blocked=True)
    xml, warns = generate(f)
    name = output_filename(f)
    out = storage.output_dir(fid) / name
    out.write_text(xml, encoding="utf-8", newline="")
    return render_template("generated.html", fid=fid, f=f, name=name, path=str(out), warns=warns,
                           n_facts=xml.count('contextRef="'), res=res, nav=nav_for(schema),
                           filled=_filled_counts(f, schema), forced=bool(res.errors))


@app.route("/f/<fid>/download/<name>")
def download(fid, name):
    p = storage.output_dir(fid) / secure_filename(name)
    if not p.exists():
        # secure_filename strips brackets; fall back to exact match in the folder
        cands = [x for x in storage.output_dir(fid).iterdir() if x.name == name]
        if not cands:
            abort(404)
        p = cands[0]
    return send_file(p, as_attachment=True, download_name=p.name, mimetype="application/xml")


@app.route("/f/<fid>/export.json")
def export_json(fid):
    f = get_filing(fid)
    buf = io.BytesIO(json.dumps(f.to_dict(), ensure_ascii=False, indent=1).encode("utf-8"))
    return send_file(buf, as_attachment=True, download_name=f"{fid}.json", mimetype="application/json")


# --------------------------------------------------------------------- settings
@app.route("/settings", methods=["GET", "POST"])
def settings():
    msg = ""
    if request.method == "POST":
        key = request.form.get("key")
        try:
            info = download_taxonomy(key, log=lambda *_: None)
            msg = f"{key}: downloaded {info['elements']} elements and {info['calc']} calculation totals."
        except Exception as e:  # noqa: BLE001
            msg = f"{key}: download failed ({e}). Check the internet connection or the schemaRef in settings.json."
    return render_template("settings.html", msg=msg, tax_status={k: taxonomy_status(k) for k in TAXONOMIES})


def run(host="127.0.0.1", port=5050, open_browser=True):
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}/")).start()
    app.run(host=host, port=port, debug=False)
