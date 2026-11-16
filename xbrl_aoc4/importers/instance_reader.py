"""Read an existing XBRL instance (e.g. last year's TaxPrint/InstantXBRL XML)
into a Filing, so it can be edited, rolled forward or regenerated."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

import io
import xml.etree.ElementTree as ET

from ..model import Filing
from ..schema_loader import load_schema

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
LINK = "http://www.xbrl.org/2003/linkbase"
XLINK = "http://www.w3.org/1999/xlink"


def _q(el, qname: str) -> str:
    """Convert 'prefix:Local' (using el's nsmap) to schema id 'prefix_Local'."""
    return qname.strip().replace(":", "_")


def read_contexts(root):
    ctxs = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        cid = c.get("id")
        ident = c.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}identifier")
        per = c.find(f"{{{XBRLI}}}period")
        inst = per.find(f"{{{XBRLI}}}instant")
        info = {"cin": ident.text.strip() if ident is not None and ident.text else ""}
        if inst is not None:
            info.update(kind="instant", date=inst.text.strip())
        else:
            info.update(kind="duration",
                        start=per.find(f"{{{XBRLI}}}startDate").text.strip(),
                        end=per.find(f"{{{XBRLI}}}endDate").text.strip())
        dims = {}
        for holder in (c.find(f"{{{XBRLI}}}scenario"), c.find(f"{{{XBRLI}}}entity/{{{XBRLI}}}segment")):
            if holder is None:
                continue
            for m in holder:
                if not isinstance(m.tag, str):
                    continue
                ax = _q(m, m.get("dimension"))
                if m.tag == f"{{{XBRLDI}}}explicitMember":
                    dims[ax] = _q(m, m.text)
                elif m.tag == f"{{{XBRLDI}}}typedMember":
                    child = next((x for x in m if isinstance(x.tag, str)), None)
                    dims[ax] = (child.text or "").strip() if child is not None else ""
        info["dims"] = dims
        ctxs[cid] = info
    return ctxs


def detect_taxonomy(root, prefixes: dict) -> str:
    for ref in root.iter(f"{{{LINK}}}schemaRef"):
        href = ref.get(f"{{{XLINK}}}href", "")
        if "2016" in href or "CnI" in href:
            return "AS"
        return "INDAS"
    return "INDAS" if "ind-as" in prefixes.values() else "AS"


def parse(path_or_bytes):
    """Parse with the standard library; return (root, {namespace_uri: prefix})."""
    data = bytes(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb").read()
    prefixes = {}
    for ev, (pfx, uri) in ET.iterparse(io.BytesIO(data), events=("start-ns",)):
        if pfx:
            prefixes.setdefault(uri, pfx)
    return ET.fromstring(data), prefixes


def read_instance(path_or_bytes, taxonomy: str | None = None) -> tuple[Filing, list[str]]:
    """Return (filing, warnings)."""
    root, prefixes = parse(path_or_bytes if isinstance(path_or_bytes, (bytes, bytearray)) else str(path_or_bytes))
    key = taxonomy or detect_taxonomy(root, prefixes)
    schema = load_schema(key)
    ctxs = read_contexts(root)
    warnings: list[str] = []

    # determine the reporting periods: the most used duration contexts
    durs = Counter((c["start"], c["end"]) for c in ctxs.values() if c["kind"] == "duration")
    periods = sorted({p for p in durs}, key=lambda p: p[1], reverse=True)
    f = Filing(taxonomy=key)
    if periods:
        f.fy_start, f.fy_end = periods[0]
    if len(periods) > 1:
        f.py_start, f.py_end = periods[1]
    f.ensure_periods()
    f.cin = next((c["cin"] for c in ctxs.values() if c["cin"]), "")

    def pkey(c):
        if c["kind"] == "duration":
            if c["end"] == f.fy_end:
                return "CY"
            if c["end"] == f.py_end:
                return "PY"
            return None
        if c["date"] == f.fy_end:
            return "CY"
        if c["date"] == f.py_end:
            return "PY"
        if c["date"] == f.open_date:
            return "OPEN"
        return None

    unknown = set()
    by_id: dict = {}
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") is None:
            continue
        ns, local = el.tag[1:].split("}")
        prefix = prefixes.get(ns)
        if not prefix:
            continue
        eid = f"{prefix}_{local}"
        if el.get("decimals") and el.get("decimals") != "INF":
            f.fact_decimals[eid] = el.get("decimals")
        c = ctxs.get(el.get("contextRef"))
        if not c:
            continue
        pk = pkey(c)
        if pk is None:
            warnings.append(f"{eid}: context {el.get('contextRef')} is outside the reporting periods; skipped")
            continue
        text = el.text if el.text is not None else ""
        if el.get(f"{{http://www.w3.org/2001/XMLSchema-instance}}nil") == "true":
            text = ""
        if schema.element(eid) is None:
            unknown.add(eid)
        if not c["dims"]:
            f.values.setdefault(eid, {})[pk] = text
            if el.get("id"):
                by_id[el.get("id")] = (f.footnotes, eid, pk)
        else:
            tid = schema.find_table(set(c["dims"]), eid) or schema.find_table(set(c["dims"]))
            if not tid:
                tid = "UNMAPPED|" + "|".join(sorted(c["dims"]))
                warnings.append(f"{eid}: no template table with axes {sorted(c['dims'])}")
            row = f.table_row(tid, c["dims"])
            row["values"].setdefault(eid, {})[pk] = text
            if el.get("id"):
                by_id[el.get("id")] = (row.setdefault("footnotes", {}), eid, pk)
    _read_footnotes(root, by_id)
    for rows in f.tables.values():
        for row in rows:
            if not row.get("footnotes"):
                row.pop("footnotes", None)
    if unknown:
        warnings.append(f"{len(unknown)} element(s) not in the {key} template: " + ", ".join(sorted(unknown)[:15]))
    nat = f.values.get(f"in-ca_NatureOfReportStandaloneConsolidated", {}).get("CY")
    if nat:
        f.nature = nat
    return f, warnings


def _read_footnotes(root, by_id: dict):
    """Attach each link:footnote to the fact its locator points at."""
    X = f"{{{XLINK}}}"
    for link in root.iter(f"{{{LINK}}}footnoteLink"):
        locs, notes = {}, {}
        for el in link:
            if el.tag == f"{{{LINK}}}loc":
                locs[el.get(X + "label")] = el.get(X + "href", "").split("#")[-1]
            elif el.tag == f"{{{LINK}}}footnote":
                notes[el.get(X + "label")] = "".join(el.itertext()).strip()
        for el in link:
            if el.tag == f"{{{LINK}}}footnoteArc":
                fid, text = locs.get(el.get(X + "from")), notes.get(el.get(X + "to"))
                if fid in by_id and text:
                    holder, eid, pk = by_id[fid]
                    holder.setdefault(eid, {})[pk] = text
