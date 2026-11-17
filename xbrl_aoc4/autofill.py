"""Fill what can be worked out without asking the user.

- Board's report sections: the Directors' Report is split at its headings and
  each section goes into the matching text block (financial highlights, change
  in nature of business, deposits, internal financial controls, ...).
- Facts found in the documents: PAN, registered office address, number of board
  meetings, rounding level, presentation currency.
- Blank mandatory amounts are set to 0, as TaxPrint / InstantXBRL do for items
  a company doesn't have (inventories, unpaid dividends, ...).

Everything filled here is reported back, so the user can check it.
"""
from __future__ import annotations

import html
import re
from decimal import Decimal

from .model import Filing

# heading of a section of the board's report -> label of the text block(s) it fills
BOARD_SECTIONS = [
    (r"financial (highlights|results|summary|performance)", [r"financial summary or highlights"]),
    (r"change in (the )?nature of (the )?business", [r"change in nature of business"]),
    (r"directors?.{0,6}(and|&) key managerial|\bkmp\b|directors and key", [r"directors or key managerial personnels who were appointed"]),
    (r"subsidiar|joint ventures?|associate compan", [r"become or ceased to be its subsidiaries"]),
    (r"deposits", [r"deposits covered under chapter v", r"deposits which are not in compliance"]),
    (r"significant and material orders", [r"significant and material orders passed"]),
    (r"internal (financial )?control", [r"adequacy of internal financial controls"]),
    (r"risk management", [r"development and implementation of risk management policy"]),
    (r"corporate social responsibility|\bcsr\b", [r"policy development and implementation by company on corporate social"]),
    (r"loans?,? guarantees? or investments?|section 186", [r"loans guarantee investment under section 186"]),
    (r"related part", [r"contracts/arrangements with related parties under section 188"]),
    (r"annual return", [r"extract of annual return"]),
    (r"declaration (by|of|from) independent directors", [r"declaration given by independent directors"]),
    (r"policy on directors.{0,3} appointment|nomination and remuneration", [r"section 178\(1\)", r"appointment and remuneration of managerial personnels"]),
    (r"managerial remuneration|particulars of employees|remuneration of (directors|managerial)", [r"appointment and remuneration of managerial personnels"]),
    (r"conservation of energy", [r"^details regarding energy conservation", r"^details regarding technology absorption",
                                 r"^details regarding foreign exchange earnings and outgo"]),
    (r"directors?.{0,3} responsibility statement", [r"director.s responsibility statement"]),
    (r"state of (the )?compan(y|ies).{0,3}s?.? affairs", [r"state of company.?s affairs", r"state of compan(y|ies).{0,2} affair"]),
    (r"material changes and commitments?", [r"material changes and commitments", r"material changes and commitment occurred"]),
    (r"^dividend", [r"amount recommended for dividend|dividend.*explanatory", r"amount recommended to be paid as dividend"]),
    (r"^reserves?$|transfer to reserves", [r"amount proposed to be carried to reserves|transfer to reserves", r"proposed to carry to any reserves"]),
    (r"(statutory )?auditors?.{0,3} report|comments? on (the )?auditors", [r"explanations or comments by board on qualification"]),
    (r"secretarial audit", [r"secretarial audit"]),
]


def _paragraphs(doc_html: str) -> list[str]:
    """The document as a list of HTML blocks (<p> and <table>), in order."""
    return re.findall(r"<p>.*?</p>|<table.*?</table>", doc_html, flags=re.S)


def _text(block: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", block)).strip()


def _is_heading(t: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", t)
    if not (3 <= len(letters) and len(t) <= 130):
        return False
    upper = sum(1 for c in letters if c.isupper()) / len(letters)
    return upper > 0.8 and not t.rstrip().endswith((".", ";"))


def board_report_sections(doc_html: str) -> list[tuple[str, str]]:
    """[(heading, section html)] from a Directors' Report."""
    out: list[tuple[str, list[str]]] = []
    prev_heading = False
    for b in _paragraphs(doc_html):
        t = _text(b)
        if b.startswith("<p>") and _is_heading(t) and not re.fullmatch(r"[-\d\s]+", t):
            heading = re.sub(r"^\s*\d+[.)]?\s*", "", t)
            wrapped = re.search(r"\b(or|and|of|under|section|with|the|on|in|to|for|by|related|foreign)$|[&,]$",
                                out[-1][0].strip(), re.I) if out else None
            if prev_heading and out and len(out[-1][1]) == 1 and (wrapped or re.fullmatch(r"\d+", heading)):
                # heading wrapped over two lines ("... BY THE REGULATORS OR" / "COURTS OR TRIBUNALS")
                out[-1] = (out[-1][0] + " " + heading, [out[-1][1][0] + b])
            else:
                out.append((heading, [b]))
            prev_heading = True
            continue
        prev_heading = False
        if out:
            out[-1][1].append(b)
    return [(h, "\n".join(bs)) for h, bs in out]


def _find(schema, rx: str, types=("textBlockItemType", "stringItemType")) -> str | None:
    for sec in schema.sections:
        for it in sec["items"]:
            if it.get("type") in types and re.search(rx, it.get("label", ""), re.I):
                return it["id"]
    return None


def _local(schema, local: str) -> str | None:
    for eid in schema.elements:
        if eid.split("_", 1)[-1] == local:
            return eid
    return None


def _strip_heading(text: str, heading: str) -> str:
    """A plain-text item holds the section's words without its numbered heading."""
    t = text.strip()
    h = re.escape(heading.strip()[:40])
    t = re.sub(r"^\W*\d*[.)]?\s*" + h + r"\S*\s*", "", t, count=1, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()[:4000]


def fill_board_report(f: Filing, schema, doc_html: str) -> list[str]:
    notes = []
    for heading, body in board_report_sections(doc_html):
        h = heading.lower()
        for rx, targets in BOARD_SECTIONS:
            if not re.search(rx, h):
                continue
            for trx in targets:
                eid = _find(schema, trx)
                if eid and not f.get_value(eid, "CY"):
                    el = schema.element(eid)
                    val = body if el["type"] == "textBlockItemType" else _strip_heading(_text(body), heading)
                    f.set_value(eid, "CY", val)
                    notes.append(f"'{el['label']}' filled from the Directors' Report section '{heading.title()}'.")
            break
        if re.search(r"^meetings?\b|meetings of (the )?board", h):
            n = len(re.findall(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", _text(body)))
            eid = _local(schema, "NumberOfMeetingsOfBoard")
            if n and eid and not f.get_value(eid, "CY"):
                f.set_value(eid, "CY", str(n))
                notes.append(f"'Number of meetings of board' set to {n} (dates listed in the Directors' Report).")
    return notes


ROUNDING = {1: "Actual", 1000: "Thousands", 100000: "Lakhs", 1000000: "Millions", 10000000: "Crores"}


def fill_known_facts(f: Filing, schema, texts: list[str], scale: int = 1, has_cash_flow: bool = False,
                     has_csr_doc: bool = False) -> list[str]:
    """Facts that are certain or written in the documents."""
    notes = []
    blob = "\n".join(texts)

    def put(local, value, why):
        eid = _local(schema, local)
        if eid and value and not f.get_value(eid, "CY"):
            f.set_value(eid, "CY", value)
            notes.append(f"'{schema.element(eid)['label']}' set to '{value[:80]}' ({why}).")

    put("DescriptionOfPresentationCurrency", "INR", "amounts are in Indian rupees")
    put("ContentOfReport", "Financial Statements", "the only option")
    if scale in ROUNDING:
        put("LevelOfRoundingUsedInFinancialStatements", ROUNDING[scale], "from the scale of the financial statements")
    if f.taxonomy == "AS":
        put("TypeOfIndustry", "Commercial and Industrial", "C&I taxonomy")
    m = re.search(r"Permanent Account Number[^A-Z]{0,20}([A-Z]{5}[0-9]{4}[A-Z])", blob) or \
        re.search(r"\bPAN\b\s*(?:No\.?|Number)?\s*[:\-]?\s*([A-Z]{5}[0-9]{4}[A-Z])", blob)
    if m:
        put("PermanentAccountNumberOfEntity", m.group(1), "found in the documents")
    m = re.search(r"Reg(?:d|istered)?\.?\s*Off(?:ice)?\.?\s*[:\-–]+\s*(.+?)(?:\n\s*CIN|\n\s*E-?mail|\n\s*Tel|\n\s*Ph)", blob, re.I | re.S)
    if m:
        addr = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
        if 10 < len(addr) < 300:
            put("AddressOfRegisteredOfficeOfCompany", addr, "from the Directors' Report letterhead")
    if has_cash_flow:
        put("WhetherCashFlowStatementIsApplicableOnCompany", "true", "a cash flow statement was uploaded")
    if has_csr_doc:
        put("WhetherProvisionsOfCorporateSocialResponsibilityAreApplicableOnCompany", "true", "a CSR report was uploaded")
    return notes


NUMERIC = ("monetaryItemType", "sharesItemType", "decimalItemType", "perShareItemType", "pureItemType",
           "nonNegativeIntegerItemType", "integerItemType")


def zero_blank_mandatory_amounts(f: Filing, schema, result) -> list[str]:
    """Set blank mandatory amounts to 0 (from a validation result's MANDATORY errors).
    An amount that is part of a total the company does report (e.g. 'Salaries and wages'
    under employee benefit expense) is left for the user, since 0 would break the total."""
    from .calc import fill_totals, relationships, _num
    rel = relationships(schema)
    parents: dict = {}
    for tot, kids in rel.items():
        for c, _w in kids:
            parents.setdefault(c, set()).add(tot)
    vals = fill_totals(f, schema)

    def leaves_gap(eid, pk, depth=0):
        """True when a total the company reported still needs this part to add up."""
        for t in parents.get(eid, ()):
            entered = _num(f.get_value(t, pk))
            if entered is None:
                if depth < 6 and leaves_gap(t, pk, depth + 1):  # a computed sub-total: look further up
                    return True
                continue
            if entered == 0:
                continue
            known = sum((_num(vals.get(c, {}).get(pk)) or 0) * Decimal(str(w)) for c, w in rel[t])
            if abs(known - entered) > 1:
                return True
        return False

    done = []
    for e in result.errors:
        if e["code"] == "TABLE" and e.get("table") in schema.tables:
            el = schema.element(e["element"])
            if not el or el.get("type") not in NUMERIC:
                continue
            for row in f.tables.get(e["table"], []):
                if not schema.is_detail_column(e["table"], row["dims"]) or row["values"].get(e["element"]):
                    continue
                pks = sorted({pk for per in row["values"].values() for pk in per if pk in ("CY", "PY")}) or ["CY"]
                row["values"][e["element"]] = {pk: "0" for pk in pks}
                done.append(el["label"])
            continue
        if e["code"] != "MANDATORY" or e.get("table") or "NumberOfEmployees" in e["element"]:
            continue  # a head count is never assumed to be 0
        el = schema.element(e["element"])
        if not el or el.get("type") not in NUMERIC:
            continue
        if not f.get_value(e["element"], e["period"]) and not leaves_gap(e["element"], e["period"]):
            f.set_value(e["element"], e["period"], "0")
            done.append(el["label"])
    if done:
        uniq = sorted(set(done))
        return [f"{len(uniq)} mandatory amount(s) the documents don't show were set to 0, as TaxPrint does "
                f"(check any the company does have): " + ", ".join(uniq[:40]) + (" ..." if len(uniq) > 40 else "")]
    return []


OTHERS_COMPONENT = r"^Miscellaneous|(OtherNonoperatingIncome|PaymentForOtherServices|OtherEmployeeRelatedExpenses|" \
                   r"OtherCurrentLiabilitiesOthers|OtherCurrentAssetsOthers|EarningsOnOtherIncome|ExpenditureOnOtherMatters|Others)$"


def balance_with_others(f: Filing, schema) -> list[str]:
    """When the notes give only part of a reported total's breakdown, put the rest into
    its 'others / miscellaneous' line so the breakdown adds up (as one would by hand),
    and say so. CSR spending that the company shows inside 'Other expenses' is moved
    out, because the taxonomy reports it beside other expenses."""
    from .calc import fill_totals, relationships, _num
    from .generator import fmt_number
    notes = []
    rel = relationships(schema)
    p = "in-gaap" if f.taxonomy == "AS" else "ind-as"
    exp, oth, csr = f"{p}_Expenses", f"{p}_OtherExpenses", f"{p}_CSRExpenditure"
    for pk in ("CY", "PY"):
        e, o, c = (_num(f.get_value(x, pk)) for x in (exp, oth, csr))
        if None not in (e, o, c) and c and exp in rel and csr in dict(rel[exp]) and oth in dict(rel[exp]):
            vals = fill_totals(f, schema)
            computed = sum((_num(vals.get(k, {}).get(pk)) or 0) * Decimal(str(w)) for k, w in rel[exp])
            if abs(computed - e - c) <= 1:
                f.set_value(oth, pk, fmt_number(o - c))
                notes.append(f"CSR expenditure ({fmt_number(c)}) taken out of 'Other expenses' for {pk}: "
                             "the taxonomy shows it as a separate expense line.")
    mandatory = {r["element"] for r in schema.rules if r["type"].startswith("Mandatory")}
    # deposits with more than 12 months' maturity that the company shows inside cash and
    # cash equivalents belong in 'other deposits with banks' too (the >12 months line is a disclosure)
    from .calc import all_relationships
    cce, d12, odb = f"{p}_CashAndCashEquivalents", f"{p}_BankDepositsWithMoreThanTwelveMonthsMaturity", f"{p}_OtherDepositsWithBanks"
    extra = dict(all_relationships(schema)[len(rel):])
    if cce in extra:
        for pk in ("CY", "PY"):
            vals = fill_totals(f, schema)
            ent, dep = _num(f.get_value(cce, pk)), _num(f.get_value(d12, pk))
            if ent is None or not dep:
                continue
            known = sum((_num(vals.get(k, {}).get(pk)) or 0) * Decimal(str(w)) for k, w in extra[cce])
            if ent - known >= dep - 1:
                f.set_value(odb, pk, fmt_number((_num(f.get_value(odb, pk)) or 0) + dep))
                notes.append(f"Deposits with more than 12 months' maturity ({fmt_number(dep)}, {pk}) added to 'Other deposits "
                             "with banks': the company shows them inside cash and cash equivalents.")
    for tot, kids in all_relationships(schema):
        others = [k for k, w in kids if re.search(OTHERS_COMPONENT, k.split("_", 1)[-1]) and w > 0]
        for pk in ("CY", "PY"):
            entered = _num(f.get_value(tot, pk))
            if entered in (None, 0):
                continue
            vals = fill_totals(f, schema)
            parts = [(_num(vals.get(k, {}).get(pk)), w) for k, w in kids]
            known = sum((v or 0) * Decimal(str(w)) for v, w in parts)
            rest = entered - known
            if not others:
                # no 'others' line: when exactly one required part is still blank, it is the remainder
                # (e.g. Finance costs with only the interest expense line missing)
                blank = [k for (v, w), (k, _) in zip(parts, kids) if v is None and w > 0 and k in mandatory
                         and re.search(r"InterestExpense$", k)]
                if len(blank) == 1 and rest > 1:
                    f.set_value(blank[0], pk, fmt_number(rest))
                    notes.append(f"'{schema.element(blank[0])['label']}' ({pk}) = {fmt_number(rest)}: the rest of "
                                 f"'{schema.element(tot)['label']}' after its other lines.")
                continue
            if not any(v is not None for v, _ in parts):
                # no breakdown at all: the whole amount goes to 'others', as TaxPrint does
                if not re.search(OTHERS_COMPONENT, others[0].split("_", 1)[-1]):
                    continue
            target = others[0]
            for _ in range(4):  # an 'other income' line can itself be a total: go down to its own 'others'
                sub = [k for k, w in rel.get(target, []) if re.search(OTHERS_COMPONENT, k.split("_", 1)[-1]) and w > 0]
                if not sub or _num(f.get_value(target, pk)) is not None:
                    break
                target = sub[0]
            if rest > 1:
                have = _num(f.get_value(target, pk)) or 0
                f.set_value(target, pk, fmt_number(have + rest))
                notes.append(f"'{schema.element(target)['label']}' ({pk}) given {fmt_number(rest)} more: the part of "
                             f"'{schema.element(tot)['label']}' the notes don't break down. Move it to the right lines if you can.")
    return notes


def fill_both_years(f: Filing, schema) -> list[str]:
    """MCA wants some items for the previous year as well (Yes/No answers, reporting
    dates, proposed dividend ...) and others for the current year only (CSR amounts).
    Which is which comes from an accepted filing (schema/years_<taxonomy>.json)."""
    import json
    from pathlib import Path
    p = Path(__file__).parent / "schema" / f"years_{schema.key}.json"
    if not p.exists():
        return []
    years = json.loads(p.read_text(encoding="utf-8"))
    notes, zeroed = [], []
    for eid in years.get("current_year_only", []):
        if f.get_value(eid, "PY") not in (None, ""):
            f.set_value(eid, "PY", None)
    for eid in years.get("both_years", []):
        cy = f.get_value(eid, "CY")
        if cy in (None, "") or f.get_value(eid, "PY") not in (None, ""):
            continue
        el = schema.element(eid)
        if not el:
            continue
        local = eid.split("_", 1)[-1]
        if local == "DateOfStartOfReportingPeriod":
            f.set_value(eid, "PY", f.py_start)
        elif local == "DateOfEndOfReportingPeriod":
            f.set_value(eid, "PY", f.py_end)
        elif el["type"] == "booleanItemType":
            f.set_value(eid, "PY", cy)
            notes.append(f"'{el['label']}' (previous year) = {'Yes' if cy == 'true' else 'No'}, as for the current year.")
        elif el["type"] in NUMERIC:
            f.set_value(eid, "PY", "0")
            zeroed.append(el["label"])
    if zeroed:
        notes.append("Previous-year amounts MCA needs but the documents don't give were set to 0: " + ", ".join(zeroed[:20]))
    return notes


# ---------------------------------------------------------------- TaxPrint-style zeros
CLASS_AXES = ("ClassesOfTangibleAssetsAxis", "ClassesOfIntangibleAssetsAxis")
NEVER_ZERO = ("NumberOfEmployees",)
NO_PY_ZERO = ("ChangesInTangibleAssets", "ChangesInIntangibleAssets")  # last year's movement comes from last year's XML
# last year's figures for these tables come from last year's XML; a 0 would hide that they are missing
NO_PY_ZERO_TABLES = ("DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable",)


def row_signature(schema, tid: str, dims: dict, wildcard_classes: bool = False) -> str:
    """A table row's members, with typed values (Column 1 ...) shown as '*'."""
    t = schema.tables.get(tid) or {}
    typed = {a["id"] for a in t.get("axes", []) if a.get("typedDomain")}
    parts = []
    for ax in sorted(dims):
        m = dims[ax]
        if ax in typed or (wildcard_classes and ax.endswith(CLASS_AXES)):
            m = "*"
        parts.append(f"{ax}={m}")
    return "|".join(parts)


def _zero_map(key: str) -> dict:
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parent / "schema" / f"zerofill_{key}.json"
    if not p.exists():
        return {"values": {}, "tables": {}}
    return json.loads(p.read_text(encoding="utf-8"))


def zero_fill_like_taxprint(f: Filing, schema) -> list[str]:
    """Write 0 for the blank amounts TaxPrint writes as 0 (learned from its output by
    tools/learn_zero_fill.py): in both years, and in every table row the filing has.
    A blank total that its lines add up to something other than 0 is left to be worked out."""
    from .calc import fill_totals, _num
    zmap = _zero_map(f.taxonomy)
    computed = fill_totals(f, schema)
    n = 0

    def ok(eid, pk):
        el = schema.element(eid)
        if not el or el.get("type") not in NUMERIC or el.get("abstract"):
            return False
        local = eid.split("_", 1)[-1]
        if any(w in local for w in NEVER_ZERO):
            return False
        return not (pk == "PY" and local in NO_PY_ZERO)

    has_py = bool(f.py_end)
    items = {it["id"]: it for sec in schema.sections for it in sec["items"]}
    for eid, pks in zmap.get("values", {}).items():
        if "CY" in pks and "PY" not in pks and not items.get(eid, {}).get("noPY"):
            pks = pks + ["PY"]  # MCA wants both years once one is given
        for pk in pks:
            if pk == "PY" and not has_py or not ok(eid, pk):
                continue
            if f.get_value(eid, pk) not in (None, ""):
                continue
            c = _num(computed.get(eid, {}).get(pk))
            if c is not None and c != 0:
                continue  # a total its lines give a value to
            f.set_value(eid, pk, "0")
            n += 1
    for tid, rows in f.tables.items():
        sigs = zmap.get("tables", {}).get(tid)
        if not sigs:
            continue
        for row in rows:
            spec = sigs.get(row_signature(schema, tid, row["dims"])) or sigs.get(
                row_signature(schema, tid, row["dims"], wildcard_classes=True))
            if not spec:
                continue
            for eid, pks in spec.items():
                for pk in pks:
                    if not ok(eid, pk) or (pk == "PY" and (not has_py or tid.endswith(NO_PY_ZERO_TABLES))):
                        continue
                    if (row["values"].get(eid) or {}).get(pk) not in (None, ""):
                        continue
                    row["values"].setdefault(eid, {})[pk] = "0"
                    n += 1
    return [f"{n} blank amounts written as 0 for both years and every table row, as TaxPrint does."] if n else []
