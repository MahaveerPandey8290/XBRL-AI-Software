"""Download and cache the official MCA taxonomy (run on a machine with internet).

The cache gives authoritative period types, balance types, namespaces and
calculation relationships. Everything still works without it (the app falls
back to learned and heuristic period types), but downloading it once is
recommended, especially for Ind AS.

    python -m xbrl_aoc4.taxonomy AS
    python -m xbrl_aoc4.taxonomy INDAS
"""
from __future__ import annotations

import json
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

from lxml import etree

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


if __name__ == "__main__":
    for k in sys.argv[1:] or ["AS", "INDAS"]:
        try:
            download(k)
        except Exception as e:  # noqa: BLE001
            print(f"{k}: download failed: {e}")
