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
    """Return a copy of filing.values with blank totals computed bottom-up
    (a total is only worked out once every sub-total under it has been)."""
    from .generator import fmt_number
    vals = copy.deepcopy(filing.values)
    rel = relationships(schema)
    mandatory = {r["element"] for r in schema.rules if r["type"].startswith("Mandatory")}

    def compute(tot, pk, stack=()):
        cur = vals.get(tot, {}).get(pk)
        if cur not in (None, "") or tot not in rel or tot in stack:
            return
        for c, _w in rel[tot]:
            if c in rel and vals.get(c, {}).get(pk) in (None, ""):
                compute(c, pk, stack + (tot,))
        parts = [(_num(vals.get(c, {}).get(pk)), w) for c, w in rel[tot]]
        if not any(p is not None for p, _ in parts):
            return
        if tot not in mandatory and not any(p for p, _ in parts if p is not None):
            return  # all components zero: leave the optional total out, as InstantXBRL does
        total = sum((p * Decimal(str(w)) for p, w in parts if p is not None), Decimal(0))
        vals.setdefault(tot, {})[pk] = fmt_number(total)

    for tot in rel:
        for pk in ("CY", "PY"):
            compute(tot, pk)
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


def reconcile_rounding(filing, schema, per_component=Decimal("1"), scale: int = 1) -> list[str]:
    """Make entered totals equal the sum of their components when they differ only
    by rounding (amounts converted from lakhs / thousands are rounded to rupees one
    by one, so a printed total can be a few rupees off its parts). Returns notes."""
    from .generator import fmt_number
    notes = []
    for _ in range(4):  # inner totals first, then the totals built on them
        changed = False
        for tot, pk, entered, computed, kids in list(check_totals(filing, schema)):
            n = sum(1 for c, _w in kids if _num(fill_totals(filing, schema).get(c, {}).get(pk)) is not None)
            # amounts converted from lakhs carry tiny float noise: allow 1/1000 of the workbook unit per part
            tol = max(per_component, Decimal(scale) / 1000) * max(n, 1)
            if abs(computed - entered) <= tol:
                filing.set_value(tot, pk, fmt_number(computed))
                lab = schema.element(tot)["label"] if schema.element(tot) else tot
                notes.append(f"'{lab}' ({pk}) adjusted by {computed - entered} for rounding, to equal its components.")
                changed = True
        if not changed:
            break
    return notes
