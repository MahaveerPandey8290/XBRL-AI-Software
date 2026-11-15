"""Totals: compute blank total rows and check entered ones.

Relationships come from the SUM() formulas of the InstantXBRL template
(captured in the schema as ``item["sum"]``) and, when downloaded, from the
MCA taxonomy calculation linkbase.
"""
from __future__ import annotations

import copy
from decimal import Decimal

from .taxonomy import cached_calc


def relationships(schema) -> dict[str, list[tuple[str, float]]]:
    rel: dict[str, list[tuple[str, float]]] = {}
    for sec in schema.sections:
        for it in sec["items"]:
            s = it.get("sum")
            if s:
                rel[it["id"]] = [(c, 1.0) for c in s["plus"]] + [(c, -1.0) for c in s["minus"]]
    for tot, kids in cached_calc(schema.key).items():
        if tot in schema.elements and tot not in rel:
            rel[tot] = [(c, w) for c, w in kids]
    return rel


def _num(v):
    from .generator import to_number
    return to_number(v)


def fill_totals(filing, schema) -> dict:
    """Return a copy of filing.values with blank totals computed bottom-up."""
    vals = copy.deepcopy(filing.values)
    rel = relationships(schema)
    mandatory = {r["element"] for r in schema.rules if r["type"].startswith("Mandatory")}
    for _ in range(6):  # nested totals
        changed = False
        for tot, kids in rel.items():
            for pk in ("CY", "PY"):
                if vals.get(tot, {}).get(pk) not in (None, ""):
                    continue
                parts = [(_num(vals.get(c, {}).get(pk)), w) for c, w in kids]
                if not any(p is not None for p, _ in parts):
                    continue
                if tot not in mandatory and not any(p for p, _ in parts if p is not None):
                    continue  # all components zero: leave the optional total out, as InstantXBRL does
                total = sum((p * Decimal(str(w)) for p, w in parts if p is not None), Decimal(0))
                from .generator import fmt_number
                vals.setdefault(tot, {})[pk] = fmt_number(total)
                changed = True
        if not changed:
            break
    return vals


def check_totals(filing, schema, tolerance=Decimal("1")):
    """Yield (total_id, pk, entered, computed) where they differ."""
    rel = relationships(schema)
    vals = fill_totals(filing, schema)  # entered totals are kept; blank sub-totals are computed
    for tot, kids in rel.items():
        if tot not in filing.values:
            continue
        for pk in ("CY", "PY"):
            entered = _num(vals.get(tot, {}).get(pk))
            if entered is None:
                continue
            parts = [(_num(vals.get(c, {}).get(pk)), w) for c, w in kids]
            if not any(p is not None for p, _ in parts):
                continue
            computed = sum((p * Decimal(str(w)) for p, w in parts if p is not None), Decimal(0))
            if abs(computed - entered) > tolerance:
                yield tot, pk, entered, computed, kids
