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


ROUNDING_UNITS = {"Thousands": 1000, "Lakhs": 100000, "Millions": 1000000, "Crores": 10000000, "Billions": 1000000000}
_NOT_ROUNDED = ("ShareCapital", "ValueOfShares", "SharesBoughtBack", "ReductionInCapital", "CSR",
                "CorporateSocialResponsibility")


def is_rounded(eid: str, schema) -> bool:
    """Amounts TaxPrint rounds to the statements' level: the financial statement and note
    amounts (including the revenue split of the company-law part). Share capital and CSR
    amounts stay to the rupee."""
    el = schema.element(eid)
    if not el or el.get("type") != "monetaryItemType":
        return False
    if not eid.startswith(("in-gaap_", "ind-as_", "in-ca_")):
        return False
    if eid.endswith("_CSRExpenditure"):
        return True  # the profit and loss line, unlike the CSR disclosure amounts
    return not any(w in eid for w in _NOT_ROUNDED)


def round_amount(v, unit: int) -> Decimal:
    from decimal import ROUND_HALF_UP
    d = _num(v)
    return (d / unit).quantize(Decimal(1), rounding=ROUND_HALF_UP) * unit


def round_to_unit(filing, schema, unit: int) -> list[str]:
    """Round amounts to the level the financial statements are presented in (e.g. whole
    lakhs), the way TaxPrint does, and keep every total equal to its components: line items
    are rounded, and a total that added up before rounding is re-added from the rounded lines."""
    from .generator import fmt_number
    if not unit or unit <= 1:
        return []
    exact = fill_totals(filing, schema)  # blank sub-totals too, so each total sees all its lines
    rel = relationships(schema)
    done: dict = {}
    changed = 0

    def ex(eid, pk):
        return _num(exact.get(eid, {}).get(pk))

    def value(eid, pk, stack=()):
        if (eid, pk) in done:
            return done[(eid, pk)]
        v = ex(eid, pk)
        out = v
        kids = rel.get(eid) if eid not in stack else None
        if kids:
            parts = [(c, w, ex(c, pk)) for c, w in kids]
            present = [(c, w, x) for c, w, x in parts if x is not None]
            if present and v is not None:
                exact_sum = sum((x * Decimal(str(w)) for _c, w, x in present), Decimal(0))
                if abs(exact_sum - v) <= max(1, len(present)):
                    out = sum((value(c, pk, stack + (eid,)) * Decimal(str(w)) for c, w, _x in present), Decimal(0))
                    done[(eid, pk)] = out
                    return out
        if v is not None and is_rounded(eid, schema):
            out = round_amount(v, unit)
        done[(eid, pk)] = out
        return out

    for eid, per in list(filing.values.items()):
        for pk, raw in list(per.items()):
            v = _num(raw)
            if v is None:
                continue
            new = value(eid, pk)
            if new is not None and new != v:
                filing.set_value(eid, pk, fmt_number(new))
                changed += 1
    changed += _balance_after_rounding(filing, schema, rel)
    unit_name = {v: k for k, v in ROUNDING_UNITS.items()}.get(unit, f"{unit:,}")
    return [f"{changed} amounts rounded to whole {unit_name.lower()} as in the financial statements; "
            "totals re-added from the rounded lines."] if changed else []


# where TaxPrint puts the difference left by amounts that are not rounded (share capital)
BALANCING_LINES = ("OtherCurrentLiabilitiesOthers", "OtherCurrentLiabilities", "OtherLongTermLiabilitiesOthers")


def _balance_after_rounding(filing, schema, rel) -> int:
    """Rounded assets and rounded equity and liabilities can differ by the part of share
    capital that is not rounded; move that difference into 'other current liabilities -
    others' (and the totals above it), as TaxPrint does."""
    from .generator import fmt_number
    pre = "in-gaap_" if filing.taxonomy == "AS" else "ind-as_"
    parents: dict = {}
    for tot, kids in rel.items():
        for c, w in kids:
            parents.setdefault(c, []).append((tot, w))
    n = 0
    for pk in ("CY", "PY"):
        vals = fill_totals(filing, schema)
        a = _num(vals.get(pre + "Assets", {}).get(pk))
        l_ = _num(vals.get(pre + "EquityAndLiabilities", {}).get(pk))
        if a is None or l_ is None or a == l_:
            continue
        plug = next((pre + x for x in BALANCING_LINES if _num(filing.values.get(pre + x, {}).get(pk)) is not None), None)
        if not plug:
            continue
        d = a - l_

        def bump(eid, amt, seen=()):
            nonlocal n
            cur = _num(filing.values.get(eid, {}).get(pk))
            if cur is not None:
                filing.set_value(eid, pk, fmt_number(cur + amt))
                n += 1
            for p, w in parents.get(eid, []):
                if p not in seen and p.startswith(pre) and p != pre + "Assets":
                    bump(p, amt * Decimal(str(w)), seen + (eid,))
        bump(plug, d)
    return n
