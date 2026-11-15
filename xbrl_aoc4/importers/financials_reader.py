"""Smart import of a company's own financial statements workbook.

Reads sheets such as "BS", "PL", "Balance Sheet", "Profit and Loss", finds
rows that have a caption and current / previous year amounts, detects the
scale ("Rs in Lakhs", "(₹ Lacs)", "in thousands" ...) and suggests the XBRL
element for each caption by fuzzy-matching against the template labels and a
list of common Indian financial statement captions. The user reviews the
suggestions before they are applied.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from ..schema_loader import load_schema

SCALES = [
    (re.compile(r"crore", re.I), 10_000_000),
    (re.compile(r"\blakh|\blac", re.I), 100_000),
    (re.compile(r"million", re.I), 1_000_000),
    (re.compile(r"thousand|'000|\b000s?\b", re.I), 1_000),
]

STATEMENT_SECTIONS = {
    "AS": {"bs": ["100100", "200600"], "pl": ["100200", "300500", "300600"], "cf": ["100300", "100400"]},
    "INDAS": {"bs": ["110000", "401100"], "pl": ["210000", "500100", "500200"], "cf": ["310000", "320000"]},
}

# caption fragments -> element local name (both taxonomies use these names)
SYNONYMS = {
    "share capital": "ShareCapital", "equity share capital": "EquityShareCapital",
    "reserves & surplus": "ReservesAndSurplus", "reserves and surplus": "ReservesAndSurplus",
    "other equity": "OtherEquity",
    "long-term provisions": "LongTermProvisions", "long term provisions": "LongTermProvisions",
    "short-term provisions": "ShortTermProvisions", "short term provisions": "ShortTermProvisions",
    "long-term borrowings": "LongTermBorrowings", "short term borrowings": "ShortTermBorrowings",
    "short-term borrowings": "ShortTermBorrowings",
    "deferred tax liabilities": "DeferredTaxLiabilitiesNet", "deferred tax assets": "DeferredTaxAssetsNet",
    "trade payables": "TradePayables", "other current liabilities": "OtherCurrentLiabilities",
    "other long term liabilities": "OtherLongTermLiabilities",
    "property, plant & equipment": "TangibleAssets", "property, plant and equipment": "TangibleAssets",
    "tangible assets": "TangibleAssets", "intangible assets": "IntangibleAssets",
    "capital work-in-progress": "TangibleAssetsCapitalWorkInProgress",
    "non current investments": "NoncurrentInvestments", "non-current investments": "NoncurrentInvestments",
    "current investments": "CurrentInvestments",
    "long-term loans and advances": "LongTermLoansAndAdvances", "short-term loans and advances": "ShortTermLoansAndAdvances",
    "other non current assets": "OtherNoncurrentAssets", "other non-current assets": "OtherNoncurrentAssets",
    "inventories": "Inventories", "trade receivables": "TradeReceivables",
    "cash and cash equivalents": "CashAndBankBalances", "cash and bank balances": "CashAndBankBalances",
    "other current assets": "OtherCurrentAssets",
    "revenue from operations": "RevenueFromOperations", "other income": "OtherIncome", "total income": "Revenue",
    "total revenue": "Revenue", "cost of materials consumed": "CostOfMaterialsConsumed",
    "cost of services": "CostOfMaterialsConsumed",
    "purchases of stock-in-trade": "PurchasesOfStockInTrade",
    "changes in inventories": "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade",
    "employee benefit expense": "EmployeeBenefitExpense", "employee benefits expense": "EmployeeBenefitExpense",
    "finance costs": "FinanceCosts", "finance cost": "FinanceCosts",
    "depreciation and amortization expense": "DepreciationDepletionAndAmortisationExpense",
    "depreciation and amortisation expense": "DepreciationDepletionAndAmortisationExpense",
    "other expenses": "OtherExpenses", "total expenses": "Expenses",
    "profit before exceptional and extraordinary items and tax": "ProfitBeforeExceptionalItemsAndTax",
    "profit before tax": "ProfitBeforeTax", "current tax": "CurrentTax", "deferred tax": "DeferredTax",
    "profit for the year": "ProfitLoss", "profit (loss) for the period": "ProfitLoss",
    "profit/(loss) for the period": "ProfitLoss", "basic": "BasicEarningPerEquityShare",
    "diluted": "DilutedEarningsPerEquityShare", "total": "",
    "cost of operations": "CostOfMaterialsConsumed", "profit/(loss) before tax": "ProfitBeforeTax",
    "profit (loss) before tax": "ProfitBeforeTax", "profit/(loss) for the year": "ProfitLoss",
    "earning /(loss) per equity share - basic": "BasicEarningPerEquityShare",
    "earning per equity share - basic": "BasicEarningPerEquityShare",
    "earning /(loss) per equity share - diluted": "DilutedEarningsPerEquityShare",
    "depreciation": "DepreciationDepletionAndAmortisationExpense",
    "net cash provided by operating activities": "CashFlowsFromUsedInOperatingActivities",
    "net cash used in operating activities": "CashFlowsFromUsedInOperatingActivities",
    "net cash (used in / provided by) investing activities": "CashFlowsFromUsedInInvestingActivities",
    "net cash used in financing activities": "CashFlowsFromUsedInFinancingActivities",
    "cash and cash equivalents at the beginning": "CashAndCashEquivalentsCashFlowStatement@start",
    "cash and cash equivalents at the end": "CashAndCashEquivalentsCashFlowStatement",
}


def norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"^\s*(\(?[ivxlc]+\)|\(?[a-z]\)|\d+[.)]|[ivxlc]+\.)\s*", "", s)
    s = re.sub(r"\(note[^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9&/,()\- ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip(" -:")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    neg = s.startswith("(") and s.endswith(")")
    try:
        x = float(s.strip("()"))
    except ValueError:
        return None
    return -x if neg else x


def detect_scale(rows) -> int:
    for r in rows[:12]:
        for v in r:
            if isinstance(v, str):
                for rx, sc in SCALES:
                    if rx.search(v) and re.search(r"(rs|inr|₹|rupees|amount|figures|in )", v, re.I):
                        return sc
                    if rx.search(v) and len(v) < 25:
                        return sc
    return 1


def classify_sheet(name: str, rows) -> str | None:
    n = name.lower()
    text = " ".join(str(v) for r in rows[:8] for v in r if isinstance(v, str)).lower()
    if n in ("bs",) or "balance sheet" in n or "balance sheet" in text:
        return "bs"
    if n in ("pl", "p&l", "pnl") or "profit" in n or "profit and loss" in text or "statement of profit" in text:
        return "pl"
    if n in ("cfs", "cf") or "cash flow" in n or "cash flow" in text:
        return "cf"
    return None


def classify_statement_page(rows) -> str | None:
    """Stricter test for Word tables / PDF pages: a line near the top must start with
    the statement's title (a tax computation or a note that mentions 'profit and loss'
    in a sentence is not a statement)."""
    for r in rows[:6]:
        line = (str(r[0]) if r and isinstance(r[0], str) else "").strip().lower()
        if "notes" in line or "schedule" in line:
            continue
        if re.match(r"(audited\s+)?balance\s*sheet\s+as\s+(at|on)", line):
            return "bs"
        if re.match(r"(statement\s+of\s+profit|profit\s*(and|&)\s*loss\s+(statement|account))", line):
            return "pl"
        if re.match(r"(cash\s*flow\s+statement|statement\s+of\s+cash\s*flows?)", line):
            return "cf"
    return None


def extract_rows(rows, allow_single: bool = False):
    """Return [(caption, cy, py, note)] for rows with a caption and two amounts
    (or one amount, current year only, when allow_single: Word/PDF statements)."""
    out = []
    for r in rows:
        cells = list(r)
        cap_i = next((i for i, v in enumerate(cells) if isinstance(v, str) and re.search(r"[A-Za-z]{3}", v)), None)
        if cap_i is None:
            continue
        caption = cells[cap_i].strip()
        nums = [(i, _num(v)) for i, v in enumerate(cells) if i > cap_i and _num(v) is not None and not isinstance(v, str)]
        if len(nums) < (1 if allow_single else 2):
            continue
        note = ""
        for v in cells[cap_i + 1:]:
            if isinstance(v, str) and re.match(r"^\s*\d+(\s*\(?[a-z]\)?)?\s*$", v):
                note = v.strip()
                break
        # the first value may be the note number (small integer next to the caption)
        if len(nums) >= 3 and float(nums[0][1]).is_integer() and abs(nums[0][1]) < 100:
            note = note or str(int(nums[0][1]))
            nums = nums[1:]
        out.append((caption, nums[0][1], nums[1][1] if len(nums) > 1 else None, note))
    return out


class Matcher:
    def __init__(self, key: str):
        self.schema = load_schema(key)
        self.key = key

    def candidates(self, kind: str):
        secs = STATEMENT_SECTIONS[self.key].get(kind, [])
        out = []
        for code in secs:
            sec = self.schema.section_by_code.get(code)
            if not sec:
                continue
            for it in sec["items"]:
                if it.get("abstract") or it.get("table") or it["type"] not in ("monetaryItemType", "perShareItemType"):
                    continue
                key = it["id"] + ("@start" if it.get("labelRole", "").startswith("periodStart") else "")
                out.append(dict(it, key=key, kname=it["name"] + ("@start" if key.endswith("@start") else "")))
        return out

    def suggest(self, caption: str, kind: str, top=5):
        n = norm(caption)
        cands = self.candidates(kind)
        by_name = {}
        for c in cands:
            by_name.setdefault(c["kname"], c)
        scored = []
        for frag, local in SYNONYMS.items():
            if local and (n == frag or n.startswith(frag)) and local in by_name:
                scored.append((1.0 + len(frag) / 1000, by_name[local]))
        for c in cands:
            lab = norm(c["label"])
            r = SequenceMatcher(None, n, lab).ratio()
            a, b = set(n.split()), set(lab.split())
            j = len(a & b) / max(1, len(a | b))
            scored.append((0.6 * r + 0.4 * j, c))
        scored.sort(key=lambda x: -x[0])
        seen, res = set(), []
        for sc, c in scored:
            if c["key"] in seen:
                continue
            seen.add(c["key"])
            lab = c["label"] + (" (opening)" if c["key"].endswith("@start") else "")
            res.append({"id": c["key"], "label": lab, "score": round(min(sc, 1.0), 2)})
            if len(res) >= top:
                break
        return res


def _to_amounts(rows):
    """Turn amount-looking text cells ('1,23,456.00', '(5,000)', '-') into numbers,
    as they come from Word tables and PDF text."""
    out = []
    for r in rows:
        new = []
        for i, v in enumerate(r):
            if type(v).__name__ == "Note":
                new.append(str(v))
                continue
            if i and isinstance(v, str):
                t = v.strip().replace("\u20b9", "").replace("Rs.", "").strip()
                if t in ("-", "\u2013", "\u2014"):
                    new.append(0.0)
                    continue
                if re.fullmatch(r"\(?-?[\d,]+(?:\.\d+)?\)?", t) and re.search(r"\d", t):
                    new.append(_num(t))
                    continue
            new.append(v)
        out.append(new)
    return out


def _sheets(path: Path):
    """[(name, visible, rows)] from a workbook, a Word document's tables or a PDF's pages."""
    ext = path.suffix.lower()
    if ext == ".xls":
        import xlrd
        wb = xlrd.open_workbook(str(path))
        return [(s.name, s.visibility == 0, [s.row_values(r) for r in range(s.nrows)]) for s in wb.sheets()]
    if ext == ".docx":
        from .docs_reader import docx_tables
        return [(title or f"{path.stem} table {i + 1}", True, _to_amounts(rows))
                for i, (title, rows) in enumerate(docx_tables(path))]
    if ext == ".pdf":
        from .docs_reader import pdf_pages_rows
        return [(head or f"{path.stem} page {i + 1}", True, _to_amounts(rows))
                for i, (head, rows) in enumerate(pdf_pages_rows(path))]
    import openpyxl
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    return [(ws.title, ws.sheet_state == "visible", [list(r) for r in ws.iter_rows(values_only=True)])
            for ws in wb.worksheets]


def read_financials(path, key: str):
    """Return {'scale': int, 'rows': [{sheet, kind, caption, cy, py, note, suggestions}]}."""
    sheets = _sheets(Path(path))
    m = Matcher(key)
    out = []
    scale = 1
    for name, visible, rows in sheets:
        if not visible:
            continue
        is_doc = Path(path).suffix.lower() in (".pdf", ".docx")
        kind = classify_statement_page(([[name]] if Path(path).suffix.lower() == ".docx" else []) + list(rows)) if is_doc else classify_sheet(name, rows)
        if not kind:
            continue
        sc = detect_scale(rows)
        scale = max(scale, sc)
        for caption, cy, py, note in extract_rows(rows, is_doc):
            if norm(caption) in ("total", "", "particulars"):
                continue
            sug = m.suggest(caption, kind)
            out.append({"sheet": name, "kind": kind, "caption": caption, "cy": cy, "py": py, "note": note,
                        "scale": sc, "suggestions": sug,
                        "best": sug[0]["id"] if sug and sug[0]["score"] >= 0.62 else ""})
    return {"scale": scale, "rows": out}


def apply_mapping(filing, mapping, source_scale: int = 1):
    """mapping: [{'element': id or id@start, 'cy': num, 'py': num}]. Amounts are
    converted from the workbook's scale to the unit the filing is typed in.
    Several captions mapped to the same element are added together (e.g. MSME and
    other trade payables -> Trade payables)."""
    totals = {}
    for m in mapping:
        eid = m.get("element") or ""
        if not eid:
            continue
        keys = ("PY", "OPEN") if eid.endswith("@start") else ("CY", "PY")
        eid = eid.replace("@start", "")
        for src, pk in (("cy", keys[0]), ("py", keys[1])):
            v = m.get(src)
            if v in (None, ""):
                continue
            totals[(eid, pk)] = totals.get((eid, pk), 0.0) + float(v)
    from ..generator import AMOUNT_SCALE, fmt_number
    from decimal import Decimal
    factor = Decimal(source_scale) / Decimal(AMOUNT_SCALE.get(filing.amount_unit or "Rupees", 1))
    n = 0
    for (eid, pk), x in totals.items():
        is_money = not eid.endswith("EarningPerEquityShare") and not eid.endswith("EarningsPerEquityShare")
        if is_money:
            val = (Decimal(repr(x)) * factor)
            if (filing.amount_unit or "Rupees") == "Rupees":
                val = val.quantize(Decimal(1))
            else:
                val = val.quantize(Decimal("0.00001")).normalize()
            filing.set_value(eid, pk, fmt_number(val))
        else:
            filing.set_value(eid, pk, str(round(x, 2)))
        n += 1
    return n
