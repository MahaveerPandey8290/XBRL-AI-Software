"""Load the template-derived schema for a taxonomy (AS or INDAS)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import TAXONOMIES

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"


class Schema:
    def __init__(self, key: str):
        self.key = key
        self.tax = TAXONOMIES[key]
        data = json.loads((SCHEMA_DIR / f"{key}.json").read_text(encoding="utf-8"))
        self.sections = data["sections"]
        self.tables = data["tables"]
        self.rules = data["rules"]
        self.elements: dict[str, dict] = {}
        for sec in self.sections:
            for it in sec["items"]:
                it.setdefault("section", sec["code"])
                self.elements.setdefault(it["id"], it)
        for t in self.tables.values():
            for it in t["items"]:
                self.elements.setdefault(it["id"], dict(it, section=None))
            t["axis_ids"] = [a["id"] for a in t["axes"]]
            for a in t["axes"]:
                a["parents"] = sorted({m.get("parent") for m in a["members"] if m.get("parent")} | ({a["default"]} if a.get("default") else set()))
            t["item_ids"] = {it["id"] for it in t["items"]}
        # member labels
        self.member_label: dict[str, str] = {}
        for t in self.tables.values():
            for a in t["axes"]:
                for m in a["members"]:
                    self.member_label.setdefault(m["id"], m["label"])
        ptf = SCHEMA_DIR / f"periods_{key}.json"
        self.learned_periods = json.loads(ptf.read_text(encoding="utf-8")) if ptf.exists() else {}
        self.exempt_parents = set(data.get("exempt_parents", []))
        self._apply_package(key)
        self.section_by_code = {s["code"]: s for s in self.sections}

    def _apply_package(self, key: str):
        """Data types, period types and typed domains from the official taxonomy package,
        when it has been loaded (they replace the ones worked out from the rules workbook)."""
        from .taxonomy import package
        pkg = package(key)
        if not pkg:
            return
        types, periods, typed = pkg.get("types", {}), pkg.get("periods", {}), pkg.get("typedDomains", {})
        for eid, el in self.elements.items():
            t = types.get(eid)
            if t and not el.get("abstract") and (t.endswith("ItemType") or t in ("CINNumber", "PANNumber", "SRNNumber")):
                el["type"] = t
        for t in self.tables.values():
            for it in t["items"]:
                if types.get(it["id"], "").endswith("ItemType"):
                    it["type"] = types[it["id"]]
            for a in t["axes"]:
                if a["id"] in typed:
                    dom = typed[a["id"]]
                    a["typedDomain"] = dom if "_" in dom else a["id"].split("_", 1)[0] + "_" + dom
        self.learned_periods.update({k: v for k, v in periods.items() if k in self.elements})

    # ------------------------------------------------------------------
    def is_detail_column(self, tid: str, dims: dict) -> bool:
        """True when every axis has a leaf (non-aggregate) member or a typed value."""
        t = self.tables.get(tid)
        if not t:
            return True
        for a in t["axes"]:
            m = dims.get(a["id"])
            if not m:
                return False
            if not a.get("typedDomain") and m in a.get("parents", []):
                return False
        return True

    def element(self, eid: str) -> dict | None:
        return self.elements.get(eid)

    def find_table(self, axes: set[str], eid: str | None = None) -> str | None:
        """Find the table whose axes include ``axes`` (and that lists eid)."""
        best = None
        for tid, t in self.tables.items():
            if not axes.issubset(set(t["axis_ids"])):
                continue
            if eid and eid not in t["item_ids"]:
                continue
            if best is None or len(t["axis_ids"]) < len(self.tables[best]["axis_ids"]):
                best = tid
        return best


@lru_cache(maxsize=4)
def load_schema(key: str) -> Schema:
    return Schema(key)
