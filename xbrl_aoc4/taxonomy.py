"""Download and cache the official MCA taxonomy (run on a machine with internet).

The cache gives authoritative period types, balance types, namespaces and
calculation relationships. Everything still works without it (the app falls
back to learned and heuristic period types), but downloading it once is
recommended, especially for Ind AS.

    python -m xbrl_aoc4.taxonomy AS
    python -m xbrl_aoc4.taxonomy INDAS
    python -m xbrl_aoc4.taxonomy COST path/to/CostingTaxonomy2019.zip   (offline, from the package)
"""
from __future__ import annotations

import json
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

CACHE_ROOT = Path.home() / ".xbrl_aoc4" / "taxonomy"
XS = "http://www.w3.org/2001/XMLSchema"
LINK = "http://www.xbrl.org/2003/linkbase"
XLINK = "http://www.w3.org/1999/xlink"
XBRLI = "http://www.xbrl.org/2003/instance"
SKIP_HOSTS = ("www.xbrl.org", "xbrl.org", "www.w3.org")


def _fetch(url: str, cache: Path) -> bytes:
    local = cache / "files" / url.split("://", 1)[-1].replace("?", "_")
    if local.exists():
        return local.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "xbrl-aoc4-generator"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    return data


def download(key: str, log=print) -> dict:
    from .config import TAXONOMIES
    entry = TAXONOMIES[key]["schemaRef"]
    cache = CACHE_ROOT / key
    cache.mkdir(parents=True, exist_ok=True)
    todo, seen = [entry], set()
    periods, balance, ns_by_prefix, calc = {}, {}, {}, {}
    while todo:
        url = todo.pop()
        if url in seen or any(h in url.split("/")[2] for h in SKIP_HOSTS):
            continue
        seen.add(url)
        try:
            data = _fetch(url, cache)
        except Exception as e:  # noqa: BLE001
            log(f"  could not fetch {url}: {e}")
            if url == entry:
                raise
            continue
        from lxml import etree  # only needed when downloading the taxonomy
        try:
            root = etree.fromstring(data)
        except etree.XMLSyntaxError:
            continue
        if root.tag == f"{{{XS}}}schema":
            tns = root.get("targetNamespace", "")
            for imp in root.iter(f"{{{XS}}}import", f"{{{XS}}}include"):
                loc = imp.get("schemaLocation")
                if loc:
                    todo.append(urljoin(url, loc))
            for ref in root.iter(f"{{{LINK}}}linkbaseRef"):
                href = ref.get(f"{{{XLINK}}}href")
                if href:
                    todo.append(urljoin(url, href))
            for el in root.iter(f"{{{XS}}}element"):
                eid = el.get("id")
                if not eid:
                    continue
                pt = el.get(f"{{{XBRLI}}}periodType")
                if pt:
                    periods[eid] = pt
                bal = el.get(f"{{{XBRLI}}}balance")
                if bal:
                    balance[eid] = bal
                if "_" in eid:
                    ns_by_prefix.setdefault(eid.split("_", 1)[0], tns)
        elif root.tag == f"{{{LINK}}}linkbase":
            for ref in root.iter(f"{{{LINK}}}roleRef"):
                pass
            for cl in root.iter(f"{{{LINK}}}calculationLink"):
                locs = {}
                for loc in cl.iter(f"{{{LINK}}}loc"):
                    locs[loc.get(f"{{{XLINK}}}label")] = loc.get(f"{{{XLINK}}}href", "").split("#")[-1]
                for arc in cl.iter(f"{{{LINK}}}calculationArc"):
                    fr = locs.get(arc.get(f"{{{XLINK}}}from"))
                    to = locs.get(arc.get(f"{{{XLINK}}}to"))
                    if fr and to:
                        lst = calc.setdefault(fr, [])
                        pair = [to, float(arc.get("weight", "1"))]
                        if pair not in lst:
                            lst.append(pair)
    (cache / "periods.json").write_text(json.dumps(periods))
    (cache / "balance.json").write_text(json.dumps(balance))
    (cache / "calc.json").write_text(json.dumps(calc))
    (cache / "namespaces.json").write_text(json.dumps(ns_by_prefix, indent=1))
    cached_period_types.cache_clear()
    cached_calc.cache_clear()
    log(f"{key}: {len(seen)} files, {len(periods)} elements, {len(calc)} calculation totals cached in {cache}")
    return {"files": len(seen), "elements": len(periods), "calc": len(calc), "namespaces": ns_by_prefix}


def _load(key: str, name: str) -> dict:
    p = CACHE_ROOT / key / name
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:  # noqa: BLE001
            return {}
    return {}


@lru_cache(maxsize=4)
def cached_period_types(key: str) -> dict:
    return _load(key, "periods.json")


@lru_cache(maxsize=4)
def cached_calc(key: str) -> dict:
    return _load(key, "calc.json")


def cached_namespaces(key: str) -> dict:
    return _load(key, "namespaces.json")


def status(key: str) -> dict:
    return {"downloaded": bool(cached_period_types(key)), "path": str(CACHE_ROOT / key)}


def load_package(key: str, path: str, log=print) -> dict:
    """Read an official taxonomy package (the .zip from the MCA website, or its extracted
    folder) without going online, and save what the generator needs in
    schema/package_<KEY>.json: the entry point (schemaRef), the namespaces, and each
    element's data type, period type and typed-dimension domain."""
    import io
    import re
    import xml.etree.ElementTree as ET
    import zipfile

    files: dict[str, bytes] = {}
    p = Path(path)
    if p.is_dir():
        for f in p.rglob("*"):
            if f.suffix.lower() in (".xsd", ".xml"):
                files[str(f.relative_to(p)).replace("\\", "/")] = f.read_bytes()
    else:
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.lower().endswith((".xsd", ".xml")):
                    files[n] = z.read(n)
    XBRLDT = "http://xbrl.org/2005/xbrldt"
    types, periods, balance, typed, ns_by_prefix, abstract = {}, {}, {}, {}, {}, set()
    entry_points: list[str] = []
    rewrites: list[tuple[str, str]] = []
    for name, data in files.items():
        try:
            root = ET.parse(io.BytesIO(data)).getroot()
        except ET.ParseError:
            continue
        tag = root.tag.split("}")[-1]
        if tag == "schema":
            tns = root.get("targetNamespace", "")
            for el in root.iter(f"{{{XS}}}element"):
                eid = el.get("id")
                if not eid or "_" not in eid:
                    continue
                ns_by_prefix.setdefault(eid.split("_", 1)[0], tns)
                if el.get("type"):
                    types[eid] = el.get("type").split(":")[-1]
                if el.get(f"{{{XBRLI}}}periodType"):
                    periods[eid] = el.get(f"{{{XBRLI}}}periodType")
                if el.get(f"{{{XBRLI}}}balance"):
                    balance[eid] = el.get(f"{{{XBRLI}}}balance")
                if el.get("abstract") == "true":
                    abstract.add(eid)
                ref = el.get(f"{{{XBRLDT}}}typedDomainRef")
                if ref:
                    typed[eid] = ref.split("#")[-1]
        elif tag == "taxonomyPackage":
            for ep in root.iter():
                if ep.tag.endswith("entryPointDocument") and ep.get("href"):
                    entry_points.append(ep.get("href"))
        elif tag == "catalog":
            for rw in root.iter():
                if rw.tag.endswith("rewriteURI"):
                    rewrites.append((rw.get("uriStartString", ""), rw.get("rewritePrefix", "")))
    if not entry_points:  # no package metadata: the schema files at the top level
        tops = sorted(n for n in files if n.lower().endswith(".xsd") and "/" not in n.strip("/").split("/", 1)[-1])
        for n in tops:
            local = n.split("/")[-1]
            for start, prefix in rewrites:
                entry_points.append(start + local)
                break
            else:
                entry_points.append(local)
    want = {"COST": r"(car|aud|cost)", "AS": r"(ci|cnI)", "INDAS": r"(ind|ent)"}.get(key, ".")
    pick = sorted(entry_points, key=lambda u: (not re.search(want, u.split("/")[-1], re.I), "com" in u.split("/")[-1], u))
    out = {"source": p.name, "schemaRef": pick[0] if pick and pick[0].startswith("http") else "",
           "entryPoints": entry_points, "namespaces": ns_by_prefix, "types": types, "periods": periods,
           "balance": balance, "typedDomains": typed, "abstract": sorted(abstract)}
    target = Path(__file__).resolve().parent / "schema" / f"package_{key}.json"
    target.write_text(json.dumps(out, indent=0), encoding="utf-8")
    log(f"{key}: {len(types)} elements, {len(ns_by_prefix)} namespaces, entry point {out['schemaRef'] or '(not found)'}; "
        f"saved to {target}")
    if not out["schemaRef"]:
        log("  No web address for the entry point was found in the package; set it in settings.json "
            '(taxonomies -> %s -> schemaRef).' % key)
    return out


def package(key: str) -> dict:
    return _load_pkg(key)


@lru_cache(maxsize=4)
def _load_pkg(key: str) -> dict:
    p = Path(__file__).resolve().parent / "schema" / f"package_{key}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


if __name__ == "__main__":
    if len(sys.argv) == 3 and Path(sys.argv[2]).exists():
        load_package(sys.argv[1], sys.argv[2])
        sys.exit(0)
    for k in sys.argv[1:] or ["AS", "INDAS"]:
        try:
            download(k)
        except Exception as e:  # noqa: BLE001
            print(f"{k}: download failed: {e}")
