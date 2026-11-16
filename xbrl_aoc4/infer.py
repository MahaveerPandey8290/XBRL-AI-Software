"""Answer the AOC-4 questions from the uploaded documents, the way an auditor
reading them would.

Each rule looks for the wording companies and auditors use for a disclosure
(several phrasings for the same thing), sets the value only when it is still
blank, and records the evidence so the user can check it in the review grid.
"""
from __future__ import annotations

import html
import re
from datetime import date

from .model import Filing

YES, NO = "true", "false"


class Docs:
    """The uploaded documents' text, by kind."""

    def __init__(self):
        self.text: dict[str, list[str]] = {}
        self.html: dict[str, list[str]] = {}
        self.rows: list[dict] = []   # all rows read from statements / notes (mapped or not)
        self.sheets: list = []       # (file, sheet, visible, rows) of every workbook, for the note tables
        self.scale = 1

    def add(self, kind: str, text: str, body: str = ""):
        self.text.setdefault(kind or "other", []).append(text or "")
        if body:
            self.html.setdefault(kind or "other", []).append(body)

    def get(self, *kinds) -> str:
        return "\n".join(t for k in kinds for t in self.text.get(k, []))

    @property
    def all(self) -> str:
        return "\n".join(t for ts in self.text.values() for t in ts)


def _local(schema, local):
    for eid in schema.elements:
        if eid.split("_", 1)[-1] == local:
            return eid
    return None


def _near(text: str, rx: str, width=400) -> str:
    m = re.search(rx, text, re.I | re.S)
    return text[m.start(): m.end() + width] if m else ""


def _to_iso(d: str) -> str:
    m = re.match(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})$", d.strip())
    if not m:
        return ""
    dd, mm, yy = (int(x) for x in m.groups())
    if yy < 100:
        yy += 2000
    try:
        return date(yy, mm, dd).isoformat()
    except ValueError:
        return ""


class Inferrer:
    def __init__(self, f: Filing, schema, docs: Docs, sources: dict, address: str = "", phone: str = ""):
        self.f, self.schema, self.d, self.src = f, schema, docs, sources
        self.address, self.phone = address, phone
        self.notes: list[str] = []

    def put(self, local, value, why, pk="CY", force=False):
        eid = local if "_" in local else _local(self.schema, local)
        if not eid or value in (None, ""):
            return False
        if self.f.get_value(eid, pk) not in (None, "") and not force:
            return False
        self.f.set_value(eid, pk, str(value))
        self.src[(eid, pk)] = why
        shown = {"true": "Yes", "false": "No"}.get(str(value), str(value))
        self.notes.append(f"'{self.schema.element(eid)['label']}' = {shown[:60]} ({why}).")
        return True

    # ------------------------------------------------------------------ rules
    def run(self) -> list[str]:
        for rule in (self.company_type, self.group_companies, self.related_parties, self.arms_length,
                     self.shareholders, self.audit_report, self.cost_records, self.books_and_leases,
                     self.board_meeting_date, self.csr, self.investments, self.revenue_split,
                     self.foreign_currency, self.notes_text_blocks, self.csr_policy, self.servers, self.general, self.revenue_detail):
            try:
                rule()
            except Exception as e:  # noqa: BLE001 - one rule must never stop the others
                self.notes.append(f"(rule {rule.__name__} skipped: {e})")
        return self.notes

    def company_type(self):
        cin = (self.f.cin or "").upper()
        if "PTC" in cin or "OPC" in cin:
            self.put("WhetherMoneyRaisedFromPublicOfferingDuringYear", NO,
                     "a private company cannot raise money from the public")
            dr = self.d.get("directors", "annexure")
            if re.search(r"secretarial audit report|form\s*(no\.?)?\s*mr-?\s*3", dr, re.I):
                self.put("WhetherSecretarialAuditReportIsApplicableOnCompany", YES, "a secretarial audit report is annexed")
            else:
                self.put("WhetherSecretarialAuditReportIsApplicableOnCompany", NO,
                         "private company; no secretarial audit report in the board's report")

    def group_companies(self):
        t = self.d.get("directors", "policies", "annexure")
        none = re.search(r"(does not have|has no|not have any|no)\s+(any\s+)?(subsidiar(y|ies)|joint ventures?|associate)", t, re.I)
        if none:
            why = "the board's report says the company has no subsidiary, joint venture or associate"
            for local in ("WhetherCompanyHasSubsidiaryCompanies", "WhetherCompanyHasInvestedInAssociates",
                          "WhetherCompanyHasInvestedInJointVentures", "WhetherCompanyHasSubsidiaryCompaniesWhichAreYetToCommenceOperations",
                          "WhetherCompanyHasSubsidiaryCompaniesLiquidatedOrSoldDuringYear",
                          "WhetherCompanyHasAssociatesWhichAreYetToCommenceOperations",
                          "WhetherCompanyHasJointVenturesWhichAreYetToCommenceOperations",
                          "WhetherCompanyHasJointVenturesLiquidatedOrSoldDuringYear",
                          "WhetherCompanyHasAssociatesLiquidatedOrSoldDuringYear"):
                self.put(local, NO, why)
        if re.search(r"(is|being)\s+an?\s+(wholly[- ]owned\s+)?subsidiary\s+(company\s+)?of|our holding company|its holding company", self.d.all, re.I):
            self.put("WhetherCompanyIsSubsidiaryCompany", YES, "the documents name a holding company")
        else:
            self.put("WhetherCompanyIsSubsidiaryCompany", NO, "no holding company is mentioned in the documents")

    def related_parties(self):
        t = self.d.get("policies", "annexure", "directors")
        if re.search(r"related part(y|ies)[^.]{0,120}(transactions? (have|has) taken place|with whom transactions)", t, re.I) \
                or re.search(r"form\s*(no\.?)?\s*aoc\s*-?\s*2", t, re.I):
            self.put("WhetherThereAreAnyRelatedPartyTransactionsDuringYear", YES,
                     "related party transactions are disclosed in the notes / Form AOC-2")
        elif re.search(r"no related party transactions", t, re.I):
            self.put("WhetherThereAreAnyRelatedPartyTransactionsDuringYear", NO, "the documents say there were none")

    def arms_length(self):
        t = self.d.get("annexure", "directors")
        part1 = _near(t, r"not at arm.?s length basis", 300)
        part2 = _near(t, r"material contracts?\s+or\s+arrangements?\s+or\s+transactions?\s+at arm.?s length basis", 500)
        if part1:
            nil = re.search(r"\b(nil|none|not applicable|n\.?a\.?)\b", part1[:250], re.I)
            self.put("WhetherThereAreContractsArrangementsTransactionsNotAtArmsLengthBasis", NO if nil else YES,
                     "Form AOC-2 part 1 " + ("says Nil" if nil else "lists contracts"))
        if part2:
            nil = re.search(r"\b(nil|none|not applicable)\b", part2[:200], re.I) and not re.search(r"\d[\d,]{3,}", part2)
            self.put("WhetherThereAreMaterialContractsArrangementsTransactionsAtArmsLengthBasis", NO if nil else YES,
                     "Form AOC-2 part 2 " + ("says Nil" if nil else "lists contracts"))

    def shareholders(self):
        t = self.d.all
        if re.search(r"(more than|holding)\s*5\s*%|five per ?cent|5% shares", t, re.I):
            self.put("WhetherThereAreAnyShareholdersHoldingMoreThanFivePerCentSharesInCompany", YES,
                     "the share capital note lists shareholders holding more than 5%")
        elif "PTC" in (self.f.cin or "").upper():
            self.put("WhetherThereAreAnyShareholdersHoldingMoreThanFivePerCentSharesInCompany", YES,
                     "private company with few shareholders (assumed; check the share capital note)")

    def audit_report(self):
        t = self.d.get("auditors") or self.d.all
        if re.search(r"auditor.?s report\)? order,? 20\d\d|\bcaro\b", t, re.I):
            # 'clause 3(xii) of the Order is not applicable' is about one clause, not the whole Order
            na = re.search(r"(?<!of )(the (said )?order|caro)(,? 20\d\d)? (is|are) not applicable to the company", t, re.I) \
                and not re.search(r"clause\s*3\s*\(|paragraph\s*3\s*\(", t, re.I)
            self.put("WhetherCompaniesAuditorsReportOrderIsApplicableOnCompany", NO if na else YES,
                     "the auditor's report " + ("says CARO does not apply" if na else "gives the CARO 2020 statement"))
        qual = re.search(r"qualified opinion|adverse opinion|disclaimer of opinion|basis for qualified", t, re.I)
        clean = re.search(r"no (qualification|reservation|adverse remark)|do(es)? not contain any qualification", self.d.get("directors"), re.I)
        if qual:
            self.put("WhetherAuditorsReportHasBeenQualifiedOrHasAnyReservationsOrContainsAdverseRemarks", YES,
                     "the auditor's report has a qualified / adverse opinion")
        elif t.strip() or clean:
            self.put("WhetherAuditorsReportHasBeenQualifiedOrHasAnyReservationsOrContainsAdverseRemarks", NO,
                     "the auditor's report gives an unmodified opinion" if t.strip() else "the board's report says there is no qualification")

    def cost_records(self):
        t = _near(self.d.get("directors"), r"cost (audit|records)", 500)
        if t and re.search(r"not (applicable|required|mandated)", t, re.I):
            self.put("WhetherMaintenanceOfCostRecordsByCompanyHasBeenMandatedUnderCompaniesCostRecordsAndAuditRules2014", NO,
                     "the board's report says cost records / cost audit do not apply")
        elif t and re.search(r"(maintained|maintains) (the )?cost records|cost auditor", t, re.I):
            self.put("WhetherMaintenanceOfCostRecordsByCompanyHasBeenMandatedUnderCompaniesCostRecordsAndAuditRules2014", YES,
                     "the board's report says cost records are maintained")
        else:
            self.put("WhetherMaintenanceOfCostRecordsByCompanyHasBeenMandatedUnderCompaniesCostRecordsAndAuditRules2014", NO,
                     "no cost audit or cost records mentioned (service company)")

    def books_and_leases(self):
        self.put("WhetherCompanyIsMaintainingBooksOfAccountAndOtherRelevantBooksAndPapersInElectronicForm", YES,
                 "books are kept in accounting software, as for almost every company (check)")
        conv = re.search(r"(operating|finance) lease[^.]{0,80}converted", self.d.all, re.I)
        self.put("WhetherAnyOperatingLeaseHasBeenConvertedToFinancialLeaseOrViceVersa", YES if conv else NO,
                 "the notes " + ("mention a lease conversion" if conv else "mention no lease conversion"))

    def board_meeting_date(self):
        for kind in ("workbook", "directors", "auditors"):
            for m in re.finditer(r"(?:dated?|date of (?:the )?report)\s*[:\-]?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{2,4})", self.d.get(kind), re.I):
                iso = _to_iso(m.group(1))
                if iso and self.f.fy_end and iso > self.f.fy_end:
                    src = {"workbook": "the date the financial statements were signed",
                           "directors": "the date of the board's report", "auditors": "the date of the auditor's report"}[kind]
                    self.put("DateOfBoardMeetingWhenFinalAccountsWereApproved", iso, src)
                    return

    def _row_amount(self, rx):
        for r in self.d.rows:
            if r.get("cy") is not None and re.search(rx, r["caption"], re.I):
                return r
        return None

    def csr(self):
        t = self.d.get("csr", "annexure", "directors")
        if not t and not self.d.get("workbook"):
            return
        if re.search(r"csr policy|policy on corporate social responsibility", t, re.I):
            self.put("WhetherCompanyHasWrittenCSRPolicy", YES, "the CSR report describes the company's CSR policy")
        sc = self.d.scale or 1
        from .generator import AMOUNT_SCALE, fmt_number
        factor = sc / AMOUNT_SCALE.get(self.f.amount_unit or "Rupees", 1)

        from decimal import Decimal

        def money(x):
            v = Decimal(repr(float(x))) * Decimal(repr(factor))
            return fmt_number(v.quantize(Decimal(1)) if (self.f.amount_unit or "Rupees") == "Rupees" else v.normalize())

        req = self._row_amount(r"amount required to be spent|two per ?cent of average net profit|prescribed csr")
        if req:
            for pk, key in (("CY", "cy"),):  # MCA takes the CSR amounts for the current year only
                if req.get(key) is not None:
                    self.put("PrescribedCSRExpenditure", money(req[key]), f"CSR note: '{req['caption'][:50]}'", pk)
                    self.put("AmountCSRToBeSpentForFinancialYear", money(req[key]), f"CSR note: '{req['caption'][:50]}'", pk)
        short = self._row_amount(r"shortfall|unspent")
        if short:
            for pk, key in (("CY", "cy"),):
                if short.get(key) is not None:
                    self.put("AmountUnspentCSR", money(max(0.0, short[key])), f"CSR note: '{short['caption'][:50]}' (excess spent counts as 0 unspent)", pk)
        spent = self._row_amount(r"contribution made during the year|amount spent during the year|total csr spent")
        if spent:
            for pk, key in (("CY", "cy"),):
                if spent.get(key) is not None:
                    self.put("AmountCSRSpentForFinancialYear", money(spent[key]), f"CSR note: '{spent['caption'][:50]}'", pk)

    def investments(self):
        p = "in-gaap" if self.f.taxonomy == "AS" else "ind-as"
        for pk in ("CY", "PY"):
            nci = self.f.get_value(f"{p}_NoncurrentInvestments", pk)
            if nci in (None, ""):
                continue
            quoted = re.search(r"\bquoted\b(?! investments? *: *nil)", self.d.get("policies", "workbook"), re.I) and \
                not re.search(r"\bunquoted\b", self.d.get("policies", "workbook"), re.I)
            if quoted:
                continue  # can't tell the split: leave it for the user
            self.put("AggregateAmountOfUnquotedNoncurrentInvestments", nci, "no quoted investments are listed in the notes", pk)
            self.put("AggregateAmountOfQuotedNoncurrentInvestments", "0", "no quoted investments are listed in the notes", pk)
            self.put("AggregateProvisionForDiminutionInValueOfNoncurrentInvestments", "0",
                     "no provision for diminution is shown in the notes", pk)

    def revenue_split(self):
        p = "in-gaap" if self.f.taxonomy == "AS" else "ind-as"
        rx = r"sale of (goods|products)|sales? of (finished|traded) goods|sale of manufactured"
        # the statements' own captions (not the Schedule III checklist wording "goods traded: Not Applicable")
        goods = any(re.search(rx, r.get("caption", ""), re.I) and r.get("cy") for r in self.d.rows) or \
            re.search(rx, self.d.get("policies"), re.I)
        for pk in ("CY", "PY"):
            rev = self.f.get_value(f"{p}_RevenueFromOperations", pk)
            if rev in (None, ""):
                continue
            if self.f.get_value(f"{p}_RevenueFromSaleOfProducts", pk) or self.f.get_value(f"{p}_RevenueFromSaleOfServices", pk):
                continue
            if goods:
                continue  # trading / manufacturing: the split needs the revenue note
            why = "the company earns its revenue from services (no sale of goods in the documents)"
            self.put("RevenueFromSaleOfServices", rev, why, pk)
            self.put("RevenueFromSaleOfProducts", "0", why, pk)

    def foreign_currency(self):
        for body in self.d.html.get("policies", []):
            sec = _section(body, r"foreign (currency|exchange)")
            if not sec:
                continue
            for cells in re.findall(r"<tr>(.*?)</tr>", sec, re.S):
                tds = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td>(.*?)</td>", cells, re.S)]
                if len(tds) < 2:
                    continue
                nums = [x for x in tds[1:] if re.fullmatch(r"\(?-?[\d,]+(\.\d+)?\)?", x)]
                if not nums:
                    continue
                cap = tds[0].lower()
                local = "EarningsInForeignCurrency" if re.search(r"income|earning|receipt", cap) else \
                    "ExpenditureInForeignCurrency" if re.search(r"expense|expenditure|outgo|payment|remittance", cap) else None
                if not local:
                    continue
                from decimal import Decimal
                from .generator import fmt_number
                for pk, v in zip(("CY", "PY"), nums):
                    x = Decimal(v.replace(",", "").strip("()")) * Decimal(self.d.scale or 1)
                    self.put(local, fmt_number(x.quantize(Decimal(1))), f"notes, foreign currency table: '{tds[0][:40]}'", pk)

    def notes_text_blocks(self):
        targets = [
            (r"revenue recognition|recognition of (revenue|income)", r"^disclosure of revenue explanatory"),
            (r"retirement benefits?|employee benefits?|gratuity", r"^disclosure of employee benefits explanatory"),
            (r"^leases?$|lease", r"^disclosure of leases explanatory"),
            (r"related part", r"^disclosure of notes on related party explanatory"),
            (r"^investments?$", r"^accounting policy for determination of carrying amount of investments|^disclosure of notes on non-current investments"),
            (r"taxation|taxes on income|income tax", r"^disclosure of notes on income taxes explanatory"),
            (r"impairment", r"^disclosure of impairment of assets explanatory"),
            (r"provisions,? contingent", r"^disclosure of notes on other provisions, contingent liabilities"),
            (r"segment", r"^disclosure of enterprise.s reportable segments"),
            (r"earnings? per share", r"^disclosure of earnings per share explanatory"),
            (r"cash and cash equivalents|cash flow", r"^disclosure of cash flow statement explanatory"),
            (r"company overview|general information|corporate information", r"^disclosure of general information about company"),
        ]
        for body in self.d.html.get("policies", []):
            for head_rx, label_rx in targets:
                sec = _section(body, head_rx)
                if not sec:
                    continue
                for alt in label_rx.split("|"):
                    eid = _find_label(self.schema, alt)
                    if eid:
                        self.put(eid, sec, "from the notes to accounts")
                        break

    def csr_policy(self):
        t = self.d.get("csr", "annexure")
        m = re.search(r"brief outline (on|of) (the )?CSR policy[^:\n]*:?\s*(.+?)(?=\n\s*2\s*[.)]|\n\s*composition of csr|$)", t, re.I | re.S)
        if m and len(m.group(3).strip()) > 40:
            body = "".join(f"<p>{html.escape(x.strip())}</p>" for x in m.group(3).split("\n") if x.strip())
            self.put("DetailsCSRPolicyTextBlock", body, "the 'brief outline of CSR policy' in the CSR report")
            self.put("DisclosureOtherContentsCSRPolicy", re.sub(r"\s+", " ", m.group(3)).strip()[:1000], "the CSR report")
        w = re.search(r"\b(www\.[\w.-]+\.[a-z]{2,}|https?://[\w./-]+)", self.d.get("csr", "annexure", "directors"), re.I)
        if w:
            self.put("DisclosureWebLinkOfCompanyAtWhichCSRPolicyIsPlaced", w.group(1), "web address in the CSR report")

    def general(self):
        cin = (self.f.cin or "").upper()
        if cin[:1] == "U":
            self.put("WhetherCompanyIsListedCompany", NO, "the CIN starts with U (unlisted company)")
        elif cin[:1] == "L":
            self.put("WhetherCompanyIsListedCompany", YES, "the CIN starts with L (listed company)")
        brsr = r"sustainability report|business responsibility (and sustainability )?report|\bBRSR\b"
        m = re.search(brsr, self.d.all, re.I)
        near = self.d.all[max(0, m.start() - 300): m.end() + 300] if m else ""
        if m and re.search(r"annexed|forms? part of|published|attached|enclosed", near, re.I) and not re.search(r"not applicable|not required", near, re.I):
            self.put("WhetherCompanyHasPublishedSustainabilityReportForTheFinancialYear", YES,
                     "the directors' report says a sustainability / business responsibility report is annexed")
        else:
            self.put("WhetherCompanyHasPublishedSustainabilityReportForTheFinancialYear", NO,
                     "no sustainability / business responsibility report is published with the accounts"
                     if not m else "the documents say a business responsibility / sustainability report is not applicable")
        if any(r.get("kind") == "cf" and re.search(r"profit before tax|depreciation|working capital", r["caption"], re.I)
               for r in self.d.rows):
            self.put("TypeOfCashFlowStatement", "Indirect Method", "the cash flow statement starts from profit before tax")
        from .model import parse_date
        for pk, (a, b) in (("CY", (self.f.fy_start, self.f.fy_end)), ("PY", (self.f.py_start, self.f.py_end))):
            da, db = parse_date(a), parse_date(b)
            if da and db:
                self.put("PeriodCoveredByFinancialStatements", f"{da:%d-%m-%Y} To {db:%d-%m-%Y}", "the reporting period", pk)
        m = re.search(r"(?:total |number of |no\.? of )(?:permanent )?employees[^.\d]{0,60}?(\d[\d,]*)|(\d[\d,]*)\s+(?:permanent )?employees",
                      self.d.all, re.I)
        if m:
            n = (m.group(1) or m.group(2)).replace(",", "")
            if 0 < int(n) < 10 ** 7:
                self.put("NumberOfEmployeesInTheCompanyAtTheEndOfTheFinancialYear", n, "number of employees stated in the documents")
        # the CSR committee
        for body in self.d.html.get("csr", []) + self.d.html.get("annexure", []):
            sec = _block_between(body, r"composition of (the )?csr committee", r"^\s*\d+\s*[.)]\s|^\s*(provide|web.?link)")
            if sec:
                self.put("DisclosureOfCompositionOfCSRCommitteeTextBlock", sec, "the 'composition of CSR committee' in the CSR report")
                break

    def revenue_detail(self):
        p = "in-gaap" if self.f.taxonomy == "AS" else "ind-as"
        from decimal import Decimal
        from .generator import fmt_number
        for pk in ("CY", "PY"):
            sv = self.f.get_value(f"{p}_RevenueFromSaleOfServices", pk)
            if sv not in (None, ""):
                self.put("RevenueFromSaleOfServicesGross", sv, "no discounts or returns are shown, so gross = net", pk)
                fx = self.f.get_value(f"{p}_EarningsInForeignCurrency", pk)
                if fx not in (None, "") and Decimal(fx) <= Decimal(sv):
                    self.put("ExportRevenueServices", fx, "earnings in foreign currency (export of services)", pk)
                    self.put("DomesticRevenueServices", fmt_number(Decimal(sv) - Decimal(fx)), "services revenue less export revenue", pk)
                else:
                    self.put("DomesticRevenueServices", sv, "no foreign currency earnings are shown", pk)
                    self.put("ExportRevenueServices", "0", "no foreign currency earnings are shown", pk)
            gp = self.f.get_value(f"{p}_RevenueFromSaleOfProducts", pk)
            if gp not in (None, "") and Decimal(gp):
                self.put("RevenueFromSaleOfProductsGross", gp, "no discounts or returns are shown, so gross = net", pk)
            tr = self.f.get_value(f"{p}_TradeReceivables", pk)
            if tr not in (None, "") and self.f.get_value(f"{p}_AllowanceForBadAndDoubtfulDebts", pk) in (None, "", "0"):
                self.put("TradeReceivablesGross", tr, "no allowance for doubtful debts is shown", pk)
            dep = self.f.get_value(f"{p}_DepreciationDepletionAndAmortisationExpense", pk)
            if dep not in (None, "") and not any(self.f.get_value(f"{p}_{x}", pk) for x in
                                                 ("DepreciationExpense", "AmortisationExpense", "DepletionExpense")):
                self.put("DepreciationExpense", dep, "depreciation and amortisation shown as one line", pk)

    def servers(self):
        """Where the books are kept electronically: the registered office unless the documents say otherwise."""
        phone = self.phone
        addr = self.address or self.f.get_value("in-ca_AddressOfRegisteredOfficeOfCompany", "CY") or ""
        addr = re.sub(r"\s+", " ", addr).strip(" ,")
        if not addr:
            return
        why = "the registered office address (books are kept there)"
        self.put("AddressOfRegisteredOfficeOfCompany", addr, "the address you gave")
        self.put("CompletePostalAddressOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", addr, why)
        pin = re.search(r"\b(\d{3})\s?(\d{3})\b(?!.*\b\d{3}\s?\d{3}\b)", addr)
        if pin:
            code = pin.group(1) + pin.group(2)
            self.put("PinCodeOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", code, why)
            state = _state_for_pin(code)
            if state:
                self.put("NameOfStateUnionTerritoryOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", state, "from the PIN code")
            parts = [x.strip(" -,") for x in re.split(r",", addr[:pin.start()]) if x.strip(" -,")]
            if parts:
                city = re.sub(r"\s*[-–]\s*$", "", parts[-1]).strip()
                city = {"New Delhi": "Delhi"}.get(city, city)
                self.put("NameOfCityOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", city, why)
                if len(parts) > 1:
                    self.put("NameOfDistrictOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", parts[-2], why)
        self.put("NameOfCountryOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", "India", why)
        self.put("ISOCountryCodeOfPlaceOfMaintenanceOfComputerServersStoringAccountingData", "IN", "India")
        if not phone:
            m = re.search(r"(?:tel|phone|ph)\.?\s*(?:no\.?)?\s*[:\-]?\s*(\+?[\d][\d\s-]{7,15}\d)", self.d.all, re.I)
            phone = m.group(1) if m else ""
        if phone:
            self.put("PhoneWithSTDISDCodeOfPlaceOfMaintenanceOfComputerServersStoringAccountingData",
                     re.sub(r"[\s-]", "", phone), "the company's phone number")

    def nature_of_others(self, rel):
        """'Nature of ..., others' answers: the captions the company used for that note."""
        for eid, per in list(self.f.values.items()):
            local = eid.split("_", 1)[-1]
            nat = _local(self.schema, "NatureOf" + local) if local.endswith("Others") else None
            if not nat:
                continue
            parent = next((t for t, kids in rel.items() if eid in dict(kids)), None)
            for pk in ("CY", "PY"):
                try:
                    if float(per.get(pk) or 0) <= 0:
                        continue
                except ValueError:
                    continue
                caps = []
                src = self.src.get((eid, pk), "")
                if ": " in src and "the part of" not in src:
                    caps = [x.split(": ", 1)[1] for x in src.split("; ") if ": " in x]
                if not caps and parent:
                    words = re.findall(r"[a-z]+", self.schema.element(parent)["label"].lower())
                    head = r"\W*".join(re.escape(w) for w in words)
                    m = re.search(r"note\s*\d+\s*:?\s*" + head + r"\b(.*?)(?=\n\s*note\s*\d|$)", self.d.get("workbook", "policies"), re.I | re.S)
                    if m:
                        for line in m.group(1).split("\n"):
                            line = line.strip()
                            if re.search(r"consist|as at|^\(?₹|^total|lacs|lakhs|^\d|^$", line, re.I) or len(line) > 80:
                                continue
                            caps.append(line)
                if caps:
                    self.put(nat, ", ".join(dict.fromkeys(caps))[:200], "the captions in the company's note", pk)


_PIN_STATE = {"11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab", "16": "Chandigarh",
              "17": "Himachal Pradesh", "18": "Jammu and Kashmir", "19": "Jammu and Kashmir", "20": "Uttar Pradesh",
              "21": "Uttar Pradesh", "22": "Uttar Pradesh", "23": "Uttar Pradesh", "24": "Uttarakhand", "25": "Uttar Pradesh",
              "26": "Uttarakhand", "27": "Uttar Pradesh", "28": "Uttar Pradesh", "30": "Rajasthan", "31": "Rajasthan",
              "32": "Rajasthan", "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat", "38": "Gujarat",
              "39": "Gujarat", "40": "Maharashtra", "41": "Maharashtra", "42": "Maharashtra", "43": "Maharashtra",
              "44": "Maharashtra", "45": "Madhya Pradesh", "46": "Madhya Pradesh", "47": "Madhya Pradesh",
              "48": "Madhya Pradesh", "49": "Chhattisgarh", "50": "Telangana", "51": "Andhra Pradesh", "52": "Andhra Pradesh",
              "53": "Andhra Pradesh", "56": "Karnataka", "57": "Karnataka", "58": "Karnataka", "59": "Karnataka",
              "60": "Tamil Nadu", "61": "Tamil Nadu", "62": "Tamil Nadu", "63": "Tamil Nadu", "64": "Tamil Nadu",
              "67": "Kerala", "68": "Kerala", "69": "Kerala", "70": "West Bengal", "71": "West Bengal", "72": "West Bengal",
              "73": "West Bengal", "74": "West Bengal", "75": "Odisha", "76": "Odisha", "77": "Odisha", "78": "Assam",
              "79": "Arunachal Pradesh", "80": "Bihar", "81": "Bihar", "82": "Jharkhand", "83": "Jharkhand", "84": "Bihar",
              "85": "Bihar"}


def _state_for_pin(pin: str) -> str:
    if pin.startswith("403"):
        return "Goa"
    if pin.startswith("605"):
        return "Puducherry"
    return _PIN_STATE.get(pin[:2], "")


def _block_between(body: str, start_rx: str, stop_rx: str) -> str:
    """HTML from the paragraph matching start_rx up to (not including) the next paragraph matching stop_rx."""
    blocks = re.findall(r"<p>.*?</p>|<table.*?</table>", body, re.S)
    out = []
    for b in blocks:
        t = html.unescape(re.sub(r"<[^>]+>", " ", b)).strip()
        if not out:
            if re.search(start_rx, t, re.I):
                out.append(b)
            continue
        if b.startswith("<p>") and re.search(stop_rx, t, re.I):
            break
        out.append(b)
    return "\n".join(out) if len(out) > 1 else ""


def _find_label(schema, rx):
    for sec in schema.sections:
        for it in sec["items"]:
            if it.get("type") == "textBlockItemType" and re.search(rx, it.get("label", ""), re.I):
                return it["id"]
    return None


def _section(body: str, head_rx: str) -> str:
    """HTML of the section of a notes document whose (short) heading matches."""
    blocks = re.findall(r"<p>.*?</p>|<table.*?</table>", body, re.S)
    out, on = [], False
    for b in blocks:
        t = html.unescape(re.sub(r"<[^>]+>", " ", b)).strip()
        is_head = b.startswith("<p>") and len(t) < 70 and not t.endswith((".", ":", ";")) and re.search(r"[A-Za-z]{3}", t) \
            and (t[:1].isupper() or t[:1].isdigit())
        if is_head and not on and re.search(head_rx, re.sub(r"\(.*?\)|^note\s*\d+\s*:?", "", t, flags=re.I).strip(), re.I):
            on = True
            out.append(b)
            continue
        if on:
            if is_head and len(out) > 1 and not b.startswith("<table") and t[:1].isupper() and len(t.split()) <= 6 \
                    and not re.search(r"[,–-]\s*$", t):
                break
            out.append(b)
    return "\n".join(out) if len(out) > 1 else ""
