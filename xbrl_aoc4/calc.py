"""Totals: compute blank total rows and check entered ones.

Relationships come from the SUM() formulas of the InstantXBRL template
(captured in the schema as ``item["sum"]``) and, when downloaded, from the
MCA taxonomy calculation linkbase.
"""
from __future__ import annotations

import copy
import re
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


# Summations MCA checks besides the ones in the templates (a total can belong to two
# calculation trees: cash and cash equivalents is checked against the cash flow
# statement and against its balance sheet breakdown).
EXTRA_RELATIONSHIPS = {
    "AS": [
        ("in-gaap_CashAndCashEquivalents", [("in-gaap_BalanceWithBanks", 1.0), ("in-gaap_ChequesDraftsOnHand", 1.0),
                                            ("in-gaap_CashOnHand", 1.0), ("in-gaap_Others", 1.0)]),
    ],
}


def all_relationships(schema) -> list[tuple[str, list[tuple[str, float]]]]:
    return list(relationships(schema).items()) + EXTRA_RELATIONSHIPS.get(schema.key, [])


def check_totals(filing, schema, tolerance=Decimal("0")):
    """Yield (total_id, pk, entered, computed) where they differ (MCA allows no difference)."""
    vals = fill_totals(filing, schema)  # entered totals are kept; blank sub-totals are computed
    for tot, kids in all_relationships(schema):
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


# lines that must equal another fact, so a rounding difference never goes into them
PROTECTED = re.compile(r"_(CashAndCashEquivalents|CashAndCashEquivalentsCashFlowStatement|CSRExpenditure|ProfitLoss\w*|"
                       r"ShareCapital|NoncurrentInvestments|TradeReceivables)$")


def _rounding_path(filing, schema, rel, kids, pk):
    """Lines to absorb a rounding difference, from the total's line down to a leaf: at each
    level the 'others' line if there is one, else the largest line. Every entered line on
    the path moves by the same amount, so all the totals on the way still add up."""
    from .autofill import OTHERS_COMPONENT
    vals = fill_totals(filing, schema)
    path = []
    for _ in range(6):
        cands = [(c, w) for c, w in kids if _num(vals.get(c, {}).get(pk)) is not None and w > 0 and not PROTECTED.search(c)]
        if not cands:
            break
        oth = [c for c, _w in cands if re.search(OTHERS_COMPONENT, c.split("_", 1)[-1])] or \
            [c for c, _w in cands if c.split("_", 1)[-1].startswith("Other")]
        target = oth[0] if oth else max(cands, key=lambda cw: abs(_num(vals[cw[0]][pk])))[0]
        path.append(target)
        if target not in rel:
            break
        kids = rel[target]
    return path


def reconcile_rounding(filing, schema, per_component=Decimal("1"), scale: int = 1) -> list[str]:
    """Amounts converted from lakhs / thousands are rounded to rupees one by one, so a
    printed total can be a few rupees off its lines, and MCA accepts no difference. The
    total is kept (it ties with the other statements) and the difference goes to the
    'others' line or the largest line of its breakdown. Returns notes."""
    from .generator import fmt_number
    notes = []
    rel = relationships(schema)
    for _ in range(6):  # inner totals first, then the totals built on them
        changed = False
        for tot, pk, entered, computed, kids in list(check_totals(filing, schema)):
            n = sum(1 for c, _w in kids if _num(fill_totals(filing, schema).get(c, {}).get(pk)) is not None)
            tol = max(per_component, Decimal(scale) / 1000) * max(n, 1)
            if scale >= 1000:
                # statements printed in lakhs / thousands: a difference smaller than half the printed
                # unit cannot be seen in them, so it is treated as rounding too
                tol = max(tol, Decimal(scale) / 2)
            diff = entered - computed
            if not diff or abs(diff) > tol:
                continue
            path = _rounding_path(filing, schema, rel, kids, pk)
            lab = schema.element(tot)["label"] if schema.element(tot) else tot
            moved = [t for t in path if filing.get_value(t, pk) not in (None, "")]
            if moved:
                for t in moved:
                    filing.set_value(t, pk, fmt_number((_num(filing.get_value(t, pk)) or Decimal(0)) + diff))
                notes.append(f"'{schema.element(moved[-1])['label']}' ({pk}) adjusted by {fmt_number(diff)} so the lines of "
                             f"'{lab}' add up exactly (rounding).")
            else:
                filing.set_value(tot, pk, fmt_number(computed))
                notes.append(f"'{lab}' ({pk}) adjusted by {fmt_number(-diff)} for rounding, to equal its components.")
            changed = True
        if not changed:
            break
    # total assets must equal total equity and liabilities exactly
    p = "in-gaap" if schema.key == "AS" else "ind-as"
    a, el = f"{p}_Assets", f"{p}_EquityAndLiabilities"
    for pk in ("CY", "PY"):
        vals = fill_totals(filing, schema)
        x, y = _num(vals.get(a, {}).get(pk)), _num(vals.get(el, {}).get(pk))
        if x is None or y is None or x == y or abs(x - y) > max(per_component * 10, Decimal(scale) / 2) or a not in rel:
            continue
        path = _rounding_path(filing, schema, rel, rel[a], pk)
        moved = [t for t in path if filing.get_value(t, pk) not in (None, "")]
        if moved:
            for t in moved + ([a] if filing.get_value(a, pk) not in (None, "") else []):
                filing.set_value(t, pk, fmt_number(_num(filing.get_value(t, pk)) + (y - x)))
            notes.append(f"'{schema.element(moved[-1])['label']}' ({pk}) adjusted by {fmt_number(y - x)} so total assets "
                         "equal total equity and liabilities (rounding in the statements).")
    return notes
