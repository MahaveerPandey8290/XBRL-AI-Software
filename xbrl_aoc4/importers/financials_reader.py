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
    s = re.sub(r"^\s*(\(?[ivxlc]+\)|\(?[a-z]\)|\(?\d+[.)]|[ivxlc]+\.)\s*", "", s)
    s = re.sub(r"^\s*[-–•]+\s*", "", s)
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
    section = sub = ""
    for r in rows:
        cells = list(r)
        cap_i = next((i for i, v in enumerate(cells) if isinstance(v, str) and re.search(r"[A-Za-z]{3}", v)
                      and not re.fullmatch(r"\s*\(?[ivxlcIVXLC]+[.)]?\s*", v)), None)
        if cap_i is None:
            continue
        caption = cells[cap_i].strip()
        nums = [(i, _num(v)) for i, v in enumerate(cells) if i > cap_i and _num(v) is not None and not isinstance(v, str)]
        m = re.search(r"(operating|investing|financing)\s+activit", caption, re.I)
        if m and (not nums or re.match(r"^\W*(?:[a-c][.)]\s*)?cash\s*flows?\s+(from|used|in|arising)", caption, re.I)):
            section = m.group(1).lower()
            sub = "adj" if section == "operating" else ""
        if section == "operating" and re.search(r"operating profit before|before working capital", caption, re.I):
            sub = "wc"
        elif section == "operating" and re.search(r"cash (generated|used) (from|in) operations", caption, re.I):
            sub = "after"
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
        out.append((caption, nums[0][1], nums[1][1] if len(nums) > 1 else None, note,
                    section + (":" + sub if sub else "")))
    return out


# captions too generic to map on their own (they only make sense under a heading)
GENERIC = {"others", "other", "total", "sub total", "subtotal", "less", "add", "particulars", "", "net", "gross",
           "as per last balance sheet", "balance carried forward", "opening balance", "closing balance"}

# regular expressions for captions that are worded in many ways; {sec} is the
# cash flow section the row is in (Operating / Investing / Financing)
PATTERNS = [
    # balance sheet notes
    (r"total outstanding dues of (micro|creditors other than micro)|^msme$|micro,? small", "TradePayables"),
    (r"in current accounts?|balances? with banks? in current", "OtherBalancesWithBanks"),
    (r"deposits?.*(not more than|less than|up ?to|within) (12|twelve) months|^fixed deposits?", "OtherDepositsWithBanks"),
    (r"deposits?.*more than (12|twelve) months", "BankDepositsWithMoreThanTwelveMonthsMaturity"),
    (r"^cash (in|on) hand", "CashOnHand"),
    (r"^cheques?,? drafts? on hand", "ChequesDraftsOnHand"),
    (r"^profit ?/? ?\(?(loss)?\)? for the (year|period)", "ProfitLossForPeriod"),
    (r"^deferred tax", "DeferredTax"),
    (r"^income tax paid|tax(es)? (of|for|relating to|pertaining to) (earlier|previous|prior) years?|short provision.*tax",
     "CurrentTaxPertainingToPreviousYears"),
    (r"^current tax", "CurrentTaxPertainingToCurrentYear"),
    (r"^current tax", "CurrentTax"),
    (r"^profit ?/? ?\(?(loss)?\)? for the (year|period)", "ProfitLoss"),
    # notes: expense and income breakdowns
    (r"staff establishment|^salar(y|ies)|wages", "SalariesAndWages"),
    (r"contribution to (provident|pf|esi)|provident fund", "ContributionToProvidentAndOtherFunds"),
    (r"staff welfare", "StaffWelfareExpense"),
    (r"directors?.? remuneration|remuneration to directors", "RemunerationToDirectors"),
    (r"directors?.? sitting fees", "DirectorsSittingFees"),
    (r"auditors?.? remuneration|payments? to (the )?auditors?", "PaymentsToAuditor"),
    (r"statutory audit fees|^audit fees|as auditor", "PaymentForAuditServices"),
    (r"^tax audit|taxation matters", "PaymentForTaxationMatters"),
    (r"company law matters", "PaymentForCompanyLawMatters"),
    (r"other services", "PaymentForOtherServices"),
    (r"conveyance|travelling", "TravellingConveyance"),
    (r"^power (and|&) fuel", "PowerAndFuel"),
    (r"electricity", "ElectricityExpenses"),
    (r"^rent\b", "Rent"),
    (r"rates (and|&) taxes", "RatesAndTaxesExcludingTaxesOnIncome"),
    (r"repairs?.*(building|office)|office repair", "RepairsToBuilding"),
    (r"repairs?.*(machinery|plant|equipment)", "RepairsToMachinery"),
    (r"^insurance", "Insurance"),
    (r"telephone|postage|internet|courier", "TelephonePostage"),
    (r"printing|stationery", "PrintingStationery"),
    (r"legal (and|&) professional|professional charges", "LegalProfessionalCharges"),
    (r"advertis", "AdvertisingPromotionalExpenses"),
    (r"corporate social responsibility|\bcsr\b", "CSRExpenditure"),
    (r"loss on (sale|disposal) of (fixed |tangible )?assets?", "LossOnDisposalDiscardDemolishmentAndDestructionOfDepreciableTangibleAsset"),
    (r"bad debts? written off", "BadDebtsWrittenOff"),
    (r"interest (on|from) (fdrs?|fixed deposits?|bank deposits?|deposits? with banks?)", "InterestOnFixedDepositsCurrentInvestments"),
    (r"exchange (rate )?fluctuation", "NetGainLossOnForeignCurrencyFluctuationsTreatedAsOtherIncome"),
    (r"^misc(ellaneous)?\.? expenses", "MiscellaneousExpenses"),
    (r"subscriptions?.*membership|membership fees", "SubscriptionsMembershipFees"),
    (r"bank charges", "BankCharges"),
    (r"filing fees|registration (and|&) filing", "RegistrationFilingFees"),
    # cash flow statement. A third item limits a pattern to part of the operating section:
    # 'adj' = adjustments to profit before working capital changes, 'wc' = working capital changes
    (r"interest (received|income)|dividend (received|income)", "OtherAdjustmentsToReconcileProfitLoss", "adj"),
    (r"interest (paid|expense)|finance costs?", "AdjustmentsForFinanceCosts", "adj"),
    (r"^provision|(profit|loss|\(profit\)).{0,12}(sale|disposal) of|written off|non[- ]?cash", "OtherAdjustmentsForNoncashItems", "adj"),
    (r"(increase|decrease).*(advances?|loans?|investments?|recoverable|deposits?|other (current |non[- ]current )?assets)",
     "AdjustmentsForDecreaseIncreaseInOtherCurrentAssets", "wc"),
    (r"buy ?back|bought back", "OtherInflowsOutflowsOfCashClassifiedAsFinancingActivities"),
    (r"interest received", "InterestReceivedClassifiedAs{sec}Activities"),
    (r"interest paid", "InterestPaidClassifiedAs{sec}Activities"),
    (r"dividends? paid", "DividendsPaidClassifiedAsFinancingActivities"),
    (r"(increase|decrease).*trade receivable", "AdjustmentsForDecreaseIncreaseInTradeReceivables"),
    (r"(increase|decrease).*trade payable", "AdjustmentsForIncreaseDecreaseInTradePayables"),
    (r"(increase|decrease).*inventor", "AdjustmentsForDecreaseIncreaseInInventories"),
    (r"(increase|decrease).*other (current )?(payable|liabilit)", "AdjustmentsForIncreaseDecreaseInOtherCurrentLiabilities"),
    (r"(increase|decrease).*other current asset", "AdjustmentsForDecreaseIncreaseInOtherCurrentAssets"),
    (r"^depreciation", "AdjustmentsForDepreciationAndAmortisationExpense"),
    (r"^(income )?tax(es)? (expenses?|paid)|^direct taxes paid", "IncomeTaxesPaidRefundClassifiedAsOperatingActivities"),
    (r"^(proceeds from )?sale of (fixed|tangible) assets?", "ProceedsFromSalesOfTangibleAssetsClassifiedAsInvestingActivities"),
    (r"^purchase of (fixed|tangible) assets?", "PurchaseOfTangibleAssetsClassifiedAsInvestingActivities"),
    (r"loans? taken|proceeds from borrowings", "ProceedsFromBorrowingsClassifiedAsFinancingActivities"),
    (r"loans? repaid|repayments? of borrowings", "RepaymentsOfBorrowingsClassifiedAsFinancingActivities"),
]


class Matcher:
    def __init__(self, key: str):
        self.schema = load_schema(key)
        self.key = key

    def candidates(self, kind: str):
        if not hasattr(self, "_cache"):
            self._cache = {}
        if kind not in self._cache:
            self._cache[kind] = self._candidates(kind)
        return self._cache[kind]

    def _candidates(self, kind: str):
        if kind == "note":  # notes to accounts: balance sheet and profit and loss sub-classifications
            secs = STATEMENT_SECTIONS[self.key]["bs"] + STATEMENT_SECTIONS[self.key]["pl"]
        else:
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

    def suggest(self, caption: str, kind: str, top=5, section: str = ""):
        n = norm(caption)
        if n in GENERIC or re.search(r"\bratio\b|in times|^of the companies act|^- for the purposes", n):
            return []
        cands = self.candidates(kind)
        by_name = {}
        for c in cands:
            by_name.setdefault(c["kname"], c)
        scored = []
        sec, _, sub = (section or "operating").partition(":")
        sec = sec.capitalize()
        for pat in PATTERNS:
            rx, local = pat[0], pat[1]
            if len(pat) > 2 and (kind != "cf" or pat[2] != sub):
                continue
            local = local.format(sec=sec)
            if re.search(rx, n) and local in by_name:
                scored.append((1.05, by_name[local]))
                break
        for frag, local in SYNONYMS.items():
            if local and (n == frag or n.startswith(frag)) and local in by_name:
                scored.append((1.0 + len(frag) / 1000, by_name[local]))
        sm = SequenceMatcher(None)
        sm.set_seq2(n)
        a = set(n.split())
        for c in cands:
            lab = c.get("_norm") or c.setdefault("_norm", norm(c["label"]))
            b = set(lab.split())
            j = len(a & b) / max(1, len(a | b))
            if not j:
                continue  # no word in common: can't reach a usable score
            sm.set_seq1(lab)
            scored.append((0.6 * sm.ratio() + 0.4 * j, c))
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


_SHEETS_CACHE: dict = {}


def _sheets(path: Path):
    """[(name, visible, rows)] from a workbook, a Word document's tables or a PDF's pages (cached per file)."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime, path.stat().st_size)
    except OSError:
        key = None
    if key and key in _SHEETS_CACHE:
        return _SHEETS_CACHE[key]
    out = _read_sheets(path)
    if key:
        _SHEETS_CACHE.clear()  if len(_SHEETS_CACHE) > 8 else None
        _SHEETS_CACHE[key] = out
    return out


def _read_sheets(path: Path):
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
    import openpyxl.reader.excel as _rx
    keep = _rx.apply_stylesheet
    _rx.apply_stylesheet = lambda archive, wb: wb  # cell styles aren't needed and are slow to load
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    finally:
        _rx.apply_stylesheet = keep
    # hidden sheets are only searched for text (PAN, address), so their first rows are enough
    return [(ws.title, ws.sheet_state == "visible",
             [list(r) for r in ws.iter_rows(values_only=True, max_row=None if ws.sheet_state == "visible" else 300)])
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
        if not is_doc and re.match(r"\s*notes?\b", name, re.I):
            kind = "note"
        if not kind:
            continue
        sc = detect_scale(rows)
        scale = max(scale, sc)
        for caption, cy, py, note, section in extract_rows(rows, is_doc):
            if norm(caption) in ("total", "", "particulars"):
                continue
            sug = m.suggest(caption, kind, section=section if kind == "cf" else "")
            out.append({"sheet": name, "kind": kind, "caption": caption, "cy": cy, "py": py, "note": note,
                        "scale": sc, "suggestions": sug,
                        # notes have many lines with no XBRL counterpart: only take sure matches there
                        "best": sug[0]["id"] if sug and sug[0]["score"] >= (0.8 if kind == "note" else 0.62) else ""})
    text = "\n".join(" ".join(str(v) for v in r if isinstance(v, str)) for _n, _v, rows in sheets for r in rows[:300])
    return {"scale": scale, "rows": out, "text": text}


def _is_per_share(eid: str) -> bool:
    return eid.endswith("EarningPerEquityShare") or eid.endswith("EarningsPerEquityShare")


def _negative_cash_flow_lines(key: str) -> set:
    from ..calc import relationships
    rel = relationships(load_schema(key))
    return {c for kids in rel.values() for c, w in kids if w < 0}


def apply_mapping(filing, mapping, source_scale: int = 1, round_to_unit: bool = False):
    """mapping: [{'element': id or id@start, 'cy': num, 'py': num, 'sheet': name}].
    Amounts are converted from the workbook's scale to the unit the filing is typed in.
    Several captions mapped to the same element on one sheet are added together
    (e.g. MSME and other trade payables -> Trade payables); when the same element
    is found on several sheets (the balance sheet and a note), the first sheet wins.
    round_to_unit rounds each amount to the workbook's unit (whole lakhs etc.),
    as the printed statements show them."""
    totals = {}
    owner = {}
    neg = _negative_cash_flow_lines(filing.taxonomy)
    for m in mapping:
        eid = m.get("element") or ""
        if not eid:
            continue
        start = eid.endswith("@start")
        keys = ("PY", "OPEN") if start else ("CY", "PY")
        eid = eid.replace("@start", "")
        sheet = m.get("sheet", "")
        for src, pk in (("cy", keys[0]), ("py", keys[1])):
            v = m.get(src)
            if v in (None, ""):
                continue
            # one sheet per value; and the closing figure of a year beats the next year's opening figure
            src_key = (sheet, start)
            prev = owner.setdefault((eid, pk), src_key)
            if prev != src_key:
                if prev[0] == sheet and prev[1] and not start:
                    owner[(eid, pk)] = src_key
                    totals.pop((eid, pk), None)
                else:
                    continue
            x = float(v)
            if m.get("kind") == "cf" and eid in neg:
                # a cash flow statement shows payments as negative figures; in XBRL the payment
                # is reported as a positive amount that its total subtracts (weight -1)
                x = -x
            if round_to_unit and source_scale > 1 and not _is_per_share(eid):
                x = float(round(x))
            totals[(eid, pk)] = totals.get((eid, pk), 0.0) + x
    from ..generator import AMOUNT_SCALE, fmt_number
    from decimal import Decimal
    factor = Decimal(source_scale) / Decimal(AMOUNT_SCALE.get(filing.amount_unit or "Rupees", 1))
    n = 0
    for (eid, pk), x in totals.items():
        is_money = not _is_per_share(eid)
        if not is_money and source_scale > 1 and abs(x) < 0.05:
            # EPS worked out in the workbook from amounts in lakhs: bring it to rupees per share
            x = x * source_scale
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
