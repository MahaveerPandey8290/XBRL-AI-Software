"""Resolve each element's XBRL period type (instant or duration).

Order of preference:
1. the MCA taxonomy itself, when it has been downloaded (taxonomy.py cache);
2. period types learned from real instance documents (schema/periods_<KEY>.json);
3. a heuristic based on data type, section and element name.
"""
from __future__ import annotations

import re

from .taxonomy import cached_period_types

INSTANT_SECTIONS_AS = {"100100", "200300", "200400", "200500", "200600"}
INSTANT_SECTIONS_INDAS = {"110000", "400300", "400400", "400500", "401100"}

DURATION_WORDS = re.compile(
    r"(IncreaseDecrease|Increase|Decrease|Additions|Disposals|Deductions|Expense|Expenditure|Depreciation|Amortisation|"
    r"Amortization|Impairment|Revenue|Income|Profit|Loss|Proceeds|Payments|Paid|Received|Purchase|Sale|Spent|Spend|"
    r"Consumed|Remuneration|Transfers|Acquisitions|Written|Dividend|Interest|Contribution|Cost|Charge|Gain|Movement|"
    r"Recognised|Recognized|Utilised|Utilized|Reversal|DuringYear|DuringPeriod|ForYear|Transactions|CashFlows?|"
    r"Turnover|Sales|Earnings|Purchases|Redemption|Buyback|Bought|Conversion|Changes|Additional|Used|Reversed)", re.I)
INSTANT_WORDS = re.compile(
    r"(Outstanding|Balance|CarryingAmount|ClosingBalance|AtEnd|AtBeginning|Authorised|Subscribed|HeldBy|Held|"
    r"Receivables|Payables|Borrowings|Investments|Inventories|Reserves|Provisions|Deposits|AmountDue|Due|"
    r"Shares|Calls|Forfeited|Payable|Receivable|Commitments|Reserved|PaidUp|ShareholdingPercent|PercentageOfShareholding)", re.I)
STRONG_INSTANT = re.compile(r"(PaidUp|FullyPaid|ReservedForIssue|PayableRelatedParty|ReceivableRelatedParty|DueForRefund|ClaimedButNotPaid|Unclaimed|Unpaid|Accrued|AsAtEnd|AtEndOf|Outstanding|Contingent|Proposed|Unspent|ClaimsAgainst)", re.I)

STRONG_DURATION = re.compile(r"(SharesIssuedAs|SharesIssuedIn|SharesIssuedUnder|IssuesOfShares|BonusShares|NumberOfSharesOutstanding)", re.I)

NUMERIC_TYPES = {"monetaryItemType", "sharesItemType", "decimalItemType", "percentItemType", "pureItemType",
                 "perShareItemType", "integerItemType", "nonNegativeIntegerItemType"}


def heuristic(key: str, el: dict, table: dict | None = None) -> str:
    t = el.get("type", "")
    if el.get("labelRole", "").startswith("periodStart") or el.get("labelRole", "").startswith("periodEnd"):
        return "instant"
    if t not in NUMERIC_TYPES:
        return "duration"
    if t == "perShareItemType":
        return "duration"
    name = el.get("name", "")
    inst_secs = INSTANT_SECTIONS_AS if key == "AS" else INSTANT_SECTIONS_INDAS
    if el.get("section") in inst_secs:
        return "instant"
    if STRONG_DURATION.search(name):
        return "duration"
    if STRONG_INSTANT.search(name):
        return "instant"
    if INSTANT_WORDS.search(name) and not DURATION_WORDS.search(name):
        return "instant"
    return "duration"


class PeriodResolver:
    def __init__(self, schema):
        self.schema = schema
        self.tax = cached_period_types(schema.key)
        self.learned = schema.learned_periods
        # period types learned for one taxonomy usually hold for the same
        # local name in the other (in-ca is shared; ind-as reuses in-gaap names)
        self.by_local = {}
        from .schema_loader import load_schema
        for k in ("AS", "INDAS"):
            try:
                for eid, pt in load_schema(k).learned_periods.items():
                    self.by_local.setdefault(eid.split("_", 1)[-1], pt)
            except FileNotFoundError:
                pass

    def source(self, eid: str) -> str:
        if eid in self.tax:
            return "taxonomy"
        if eid in self.learned or eid.split("_", 1)[-1] in self.by_local:
            return "learned"
        return "heuristic"

    def __call__(self, eid: str) -> str:
        if eid in self.tax:
            return self.tax[eid]
        if eid in self.learned:
            return self.learned[eid]
        local = eid.split("_", 1)[-1]
        if local in self.by_local:
            return self.by_local[local]
        el = self.schema.element(eid) or {"name": eid.split("_", 1)[-1], "type": "monetaryItemType"}
        return heuristic(self.schema.key, el)
