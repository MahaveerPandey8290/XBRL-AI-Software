"""Build the AOC-4 note tables (the dimensional "tables" MCA checks) from the
uploaded documents, the way an auditor fills TaxPrint's grids:

* fixed asset schedule  -> tangible / intangible assets tables (+ useful lives)
* share capital note    -> classes of share capital, shareholders over 5 %
* reserves note         -> statement of changes in reserves
* provisions, receivables, loans, borrowings, investments notes
* signatures            -> directors signing the accounts and the board report, auditors
* related party sheets  -> key managerial personnel, related party transactions
* CSR annexure / working-> CSR spending, net profits for three years
* CARO annexure         -> auditor's remarks by clause
* directors' report     -> principal product / service (ITC code)

Every table is built only from what the documents say. Amounts are fitted to
the face of the balance sheet so the MCA "summation" checks pass. Facts the
documents don't contain (an auditor's PAN, a director's date of birth...) are
listed in ``missing`` so the page can ask for them.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP

from .model import Filing

G, C = "in-gaap_", "in-ca_"
PAN_RX = r"\b[A-Z]{3}[PCHFATBLJG][A-Z]\d{4}[A-Z]\b"
DIN_RX = r"\bDIN\s*(?:No\.?)?\s*[:\-–]*\s*(\d{8})\b"


def _txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _isnum(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    return bool(re.fullmatch(r"\(?-?[\d,]+(\.\d+)?\)?", _txt(v)))


def _num(v) -> Decimal:
    s = _txt(v).replace(",", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()") or "0"
    try:
        d = Decimal(s)
    except Exception:  # noqa: BLE001
        return Decimal(0)
    return -d if neg else d


def _amt(v, scale) -> int:
    return int((_num(v) * Decimal(scale)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _clean_name(s: str) -> str:
    s = re.sub(r"^\s*(capt|mr|mrs|ms|dr|shri|smt|late|m/s)\.?\s+", "", _txt(s), flags=re.I)
    s = re.sub(r"\s+", " ", s.replace("\n", " ")).strip(" .,:-")
    return s


def _key(name: str) -> str:
    """A loose key for matching the same person / company written differently."""
    s = _clean_name(name).lower()
    s = re.sub(r"\b(private|pvt|limited|ltd|llp|the|and|&)\b\.?", " ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return " ".join(w for w in s.split() if len(w) > 1)


def _same(a: str, b: str) -> bool:
    ka, kb = _key(a).split(), _key(b).split()
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    # first and last word match (Anil K. Mohindru ~ Anil Kumar Mohindru), or the shorter is a prefix
    if ka[0] == kb[0] and ka[-1] == kb[-1]:
        return True
    n = min(len(ka), len(kb))
    return n >= 2 and ka[:n] == kb[:n]


def _iso(d: str) -> str:
    m = re.search(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})", d or "")
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9}),?\s+(\d{4})", d or "")
    if m:
        months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
        mo = m.group(2)[:3].lower()
        if mo in months:
            return f"{m.group(3)}-{months.index(mo) + 1:02d}-{int(m.group(1)):02d}"
    return ""


def _split_name(name: str):
    parts = _clean_name(name).upper().split()
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def html_tables(body: str) -> list[list[list[str]]]:
    """Cell text of every <table> in a document's HTML."""
    import html as _h
    out = []
    for t in re.findall(r"<table\b.*?</table>", body or "", re.S | re.I):
        rows = []
        for tr in re.findall(r"<tr\b.*?</tr>", t, re.S | re.I):
            cells = [re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", c))).strip()
                     for c in re.findall(r"<t[dh]\b.*?>(.*?)</t[dh]>", tr, re.S | re.I)]
            rows.append(cells)
        out.append(rows)
    return out


# ---------------------------------------------------------------- classification of note lines
TANGIBLE_CLASSES = [
    (r"lease\s*hold\s*improve", "LeaseholdImprovementsMember"),
    (r"\bland\b", "LandMember"),
    (r"building|premises|flat\b|office\s+space", "BuildingsMember"),
    (r"plant|machinery|equipment(?!s?\s*office)(?<!office equipment)", "PlantAndEquipmentMember"),
    (r"furniture|fixture", "FurnitureAndFixturesMember"),
    (r"vehicle|motor|car\b|cars\b|scooter|cycle|tempo|truck|bus\b|trailer|lorry", "VehiclesMember"),
    (r"computer|laptop|printer|server|peripheral", "ComputerEquipmentsMember"),
    (r"office\s*equip|air\s*condition|mobile|telephone|fax|electrical|electric fitting", "OfficeEquipmentMember"),
]
INTANGIBLE_CLASSES = [
    (r"goodwill", "GoodwillMember"),
    (r"software", "ComputerSoftwareMember"),
    (r"trade\s*mark|brand", "BrandsAndTradeMarksMember"),
    (r"licen[cs]e|franchise", "LicensesAndFranchiseMember"),
    (r"patent|copyright", "CopyrightsPatentsAndOtherOperatingRightsMember"),
]
# Schedule II useful lives (years), used when the notes don't list them
SCHEDULE_II = {"BuildingsMember": 60, "PlantAndEquipmentMember": 15, "FurnitureAndFixturesMember": 10,
               "VehiclesMember": 8, "OfficeEquipmentMember": 5, "ComputerEquipmentsMember": 3,
               "LeaseholdImprovementsMember": 10, "OtherTangibleAssetsMember": 10,
               "ComputerSoftwareMember": 3, "OtherIntangibleAssetsMember": 5}

# ITC service / product codes for common businesses: (keyword, 4-digit, category, 8-digit)
ITC_CODES = [
    (r"custom house agent|clearing and forwarding|freight forward|logistic|cargo|shipping agen", "9967",
     "SUPPORTING TRANSPORT SERVICES", "99679900"),
    (r"goods transport|transport of goods|road transport", "9965", "GOODS TRANSPORT SERVICES", "99651100"),
    (r"software|information technology|it services|it enabled", "9983",
     "OTHER PROFESSIONAL, TECHNICAL AND BUSINESS SERVICES", "99831300"),
    (r"construction|civil work|contractor", "9954", "CONSTRUCTION SERVICES", "99541000"),
    (r"consult", "9983", "OTHER PROFESSIONAL, TECHNICAL AND BUSINESS SERVICES", "99831100"),
    (r"hotel|restaurant|catering", "9963", "ACCOMMODATION, FOOD AND BEVERAGE SERVICES", "99633000"),
    (r"real estate|rent(al)? of (immovable|property)", "9972", "REAL ESTATE SERVICES", "99721100"),
]


def _inv_kind(caption: str, what: str) -> str:
    """MCA's wording for an investment in shares (TypeOfNoncurrentInvestments is a closed list)."""
    c = caption.lower()
    if "subsidiar" in c:
        return f"Investment in subsidiaries {what}"
    if re.search(r"public sector|psu|government compan", c):
        return f"Investment in public sector {what}"
    return f"Investment in other Indian companies {what}"


def _classify(caption: str, classes, default: str) -> str:
    c = caption.lower()
    for rx, m in classes:
        if re.search(rx, c):
            return m
    return default


class Tables:
    def __init__(self, f: Filing, schema, docs, sources: dict | None = None, round_unit: int = 1, prior: Filing | None = None):
        self.f, self.schema, self.d = f, schema, docs
        self.prior = prior  # last year's AOC-4 (an uploaded XML), for previous-year movements
        # amounts read from the notes are rounded to this many rupees (100000 = whole lakhs),
        # the level the statements are presented in, so the tables match the rounded face
        self.round_unit = max(1, int(round_unit or 1))
        self.src = sources if sources is not None else {}
        self.notes: list[str] = []
        self.missing: list[dict] = []
        self.scale = max(1, int(getattr(docs, "scale", 1) or 1))
        self.sheets = getattr(docs, "sheets", [])
        self._pan = None

    # ------------------------------------------------------------ plumbing
    def _rs(self, x, scale) -> int:
        """An amount from the notes (in the note's scale) in rupees, rounded to round_unit."""
        u = self.round_unit if scale > 1 else 1
        return int((x * Decimal(scale) / u).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * u

    def run(self) -> list[str]:
        for rule in (self.fixed_assets, self.share_capital, self.shareholders, self.reserves, self.provisions,
                     self.receivables, self.loans, self.borrowings, self.investments, self.signatories,
                     self.auditors, self.kmp, self.related_parties, self.csr_net_profits, self.csr_spending,
                     self.principal_products, self.caro):
            try:
                rule()
            except Exception as e:  # noqa: BLE001  one bad note must not stop the rest
                self.notes.append(f"Could not build {rule.__name__.replace('_', ' ')} from the documents ({e}).")
        self.prior_facts()
        self.py_counterparts()
        return self.notes

    PRIOR_FACTS = r"Phone|PlaceOfMaintenanceOfComputerServers|ServiceProvider|BackUp|IPAddress"

    def prior_facts(self):
        """Facts that rarely change (where the accounting data's server is, its phone ...) from last year's filing."""
        if self.prior is None:
            return
        n = 0
        for eid, per in self.prior.values.items():
            if re.search(self.PRIOR_FACTS, eid) and per.get("CY") not in (None, "") and self.f.get_value(eid, "CY") in (None, "") \
                    and self.schema.element(eid):
                self.f.set_value(eid, "CY", per["CY"])
                self.src[(eid, "CY")] = "last year's filing"
                n += 1
        if n:
            self.notes.append(f"{n} facts about the accounting server (phone, address ...) taken from last year's filing.")

    def py_counterparts(self):
        n = py_zero_counterparts(self.f, self.schema, self.src)
        if n:
            self.notes.append(f"{n} amounts that are zero this year set to zero for last year too (MCA needs both years).")

    def tid(self, name):
        for p in (G, C):
            if p + name in self.schema.tables:
                return p + name
        return None

    def face(self, local, pk="CY") -> int | None:
        for p in (G, C):
            v = self.f.get_value(p + local, pk)
            if v not in (None, ""):
                try:
                    return int(Decimal(str(v)))
                except Exception:  # noqa: BLE001
                    return None
        return None

    def has_table(self, name) -> bool:
        t = self.tid(name)
        return bool(t and self.f.tables.get(t))

    def put_row(self, table, dims: dict, values: dict, why: str):
        """values: {local or full id: {pk: value}}; dims: {axis local: member local or typed text}."""
        tid = self.tid(table)
        if not tid:
            return None
        t = self.schema.tables[tid]
        full = {}
        for ax, m in dims.items():
            aid = next((a for a in t["axis_ids"] if a.endswith("_" + ax)), None)
            if not aid:
                continue
            a = next(x for x in t["axes"] if x["id"] == aid)
            if a.get("typedDomain"):
                full[aid] = str(m)
            else:
                mid = next((x["id"] for x in a["members"] if x["id"].endswith("_" + m)), None)
                if mid is None and a.get("default", "").endswith("_" + m):
                    mid = a["default"]
                if mid is None and not a["members"]:  # the template didn't list this axis's members
                    mid = aid.split("_", 1)[0] + "_" + m
                if mid is None:
                    return None
                full[aid] = mid
        row = self.f.table_row(tid, full)
        for local, per in values.items():
            eid = local if "_" in local else next((i for i in t["item_ids"] if i.endswith("_" + local)), None)
            if not eid:
                continue
            for pk, v in per.items():
                if v is None or v == "":
                    continue
                row["values"].setdefault(eid, {})[pk] = str(v)
        if why:
            row["src"] = why
        return row

    def prior_rows(self, table):
        """Rows of the same table in last year's filing (if one was uploaded): {dims: values}."""
        tid = self.tid(table)
        if not tid or self.prior is None:
            return {}
        return {json.dumps(r["dims"], sort_keys=True): r["values"] for r in self.prior.tables.get(tid, [])}

    def prior_py(self, table, stock):
        """Last year's movement becomes this filing's previous-year movement, last year's opening our opening."""
        prev = self.prior_rows(table)
        tid = self.tid(table)
        if not prev or not tid:
            return 0
        n = 0
        for row in self.f.tables.get(tid, []):
            pv = prev.get(json.dumps(row["dims"], sort_keys=True))
            if not pv:
                continue
            for eid, per in pv.items():
                name = eid.split("_", 1)[1]
                if name == stock:
                    if per.get("PY") not in (None, ""):
                        row["values"].setdefault(eid, {})["OPEN"] = per["PY"]
                elif per.get("CY") not in (None, "") and row["values"].get(eid, {}).get("PY") in (None, ""):
                    el = self.schema.element(eid) or {}
                    if el.get("type") in ("monetaryItemType", "sharesItemType", "decimalItemType") or table.startswith("DisclosureOf"):
                        row["values"].setdefault(eid, {})["PY"] = per["CY"]
            # keep opening + movement = closing even if last year's figures were rounded differently
            st = next((e for e in row["values"] if e.endswith("_" + stock)), None)
            ch = next((e for e in row["values"] if e.endswith("_ChangesIn" + stock)), None)
            if st and ch:
                try:
                    row["values"][st]["OPEN"] = str(int(Decimal(row["values"][st]["PY"]) - Decimal(row["values"][ch]["PY"])))
                except Exception:  # noqa: BLE001
                    pass
            n += 1
        if n:
            self.notes.append(f"Previous year's movement in '{self.schema.tables[tid]['title']}' taken from last year's filing ({n} rows).")
        return n

    def prior_same_person(self, table, name_el, fields):
        """Fill identity facts (DIN, PAN, date of birth ...) a person or firm already had in last year's filing."""
        tid = self.tid(table)
        prev = self.prior_rows(table)
        if not tid or not prev:
            return 0
        by_name = {}
        for v in prev.values():
            nm = next((per.get("CY") for e, per in v.items() if e.endswith("_" + name_el)), None)
            if nm:
                by_name[_key(nm)] = v
        n = 0
        for row in self.f.tables.get(tid, []):
            nm = next((per.get("CY") for e, per in row["values"].items() if e.endswith("_" + name_el)), None)
            if not nm:
                continue
            pv = next((v for k, v in by_name.items() if _same(k, _key(nm)) or k.startswith(_key(nm)[:10]) or _key(nm).startswith(k[:10])), None)
            if not pv:
                continue
            for eid, per in pv.items():
                if eid.split("_", 1)[1] in fields and per.get("CY") not in (None, "") and row["values"].get(eid, {}).get("CY") in (None, ""):
                    row["values"].setdefault(eid, {})["CY"] = per["CY"]
                    n += 1
        return n

    def zeros(self, table, rx, pks=("CY", "PY"), skip=()) -> dict:
        """0 for every amount line of a table whose name matches rx (TaxPrint fills these; MCA requires several)."""
        tid = self.tid(table)
        out = {}
        for eid in self.schema.tables[tid]["item_ids"] if tid else []:
            name = eid.split("_", 1)[1]
            el = self.schema.element(eid) or {}
            if re.search(rx, name) and name not in skip and el.get("type") in ("monetaryItemType", "sharesItemType", "decimalItemType") \
                    and not el.get("abstract"):
                out[name] = {pk: 0 for pk in pks}
        return out

    def ask(self, table, dims, local, label, why=""):
        """Record a fact the documents don't give, for the page to ask."""
        tid = self.tid(table)
        if not tid:
            return
        eid = next((i for i in self.schema.tables[tid]["item_ids"] if i.endswith("_" + local)), None)
        if not eid:
            return
        row = self.put_row(table, dims, {}, "")
        full = row["dims"] if row else {}
        if row is not None and row["values"].get(eid, {}).get("CY"):
            return
        el = self.schema.element(eid) or {}
        self.missing.append({"element": f"__t~{tid}~{json.dumps(full, sort_keys=True)}~{eid}", "period": "CY",
                             "label": label, "kind": "date" if el.get("type") == "dateItemType" else "text",
                             "options": el.get("enum") or [], "section": self.schema.tables[tid].get("title", ""),
                             "message": why or "Not given in the uploaded documents."})

    # ------------------------------------------------------------ reading the workbook notes
    def note(self, title_rx: str, scale=None):
        """Lines of the note whose heading matches title_rx: [(caption, [numbers])], plus the raw rows."""
        for fname, sheet, visible, rows in self.sheets:
            if not visible:
                continue
            for i, r in enumerate(rows):
                head = " ".join(_txt(v) for v in r if _txt(v))
                if re.search(r"^\s*note\b[^:]{0,12}[:.\-]?\s*" + title_rx, head, re.I) or (
                        re.search(r"^\s*" + title_rx, head, re.I) and not any(_isnum(v) for v in r)
                        and len(head) < 80 and re.search(r"note", sheet, re.I)):
                    out = []
                    prev_caption = ""
                    for r2 in rows[i + 1:]:
                        h2 = " ".join(_txt(v) for v in r2 if _txt(v))
                        if re.match(r"^\s*note\b", h2, re.I):
                            break
                        cells = [v for v in r2 if _txt(v)]
                        if not cells:
                            continue
                        cap = " ".join(_txt(v) for v in r2 if _txt(v) and not _isnum(v))
                        nums = [v for v in r2 if _isnum(v)]
                        if re.search(r"(consist|comprise)s? of|^\(?\s*(₹|rs\.?|in)\b.*(lacs|lakhs|thousand|crore|₹)|as at \d|31st march|march \d", cap, re.I) and len(nums) <= 2 and not re.search(r"[a-z]{4,}.*[a-z]{4,}.*[a-z]{4,}.*[a-z]{4,}", re.sub(r"(consist|comprise)s? of the following|as at|31st march|march|lacs|lakhs", "", cap, flags=re.I)):
                            continue
                        if nums and cap and cap[:1].islower() and prev_caption:
                            cap = prev_caption + " " + cap
                        out.append((cap, [_num(v) for v in nums]))
                        prev_caption = cap if not nums else ""
                    return out, (scale or self.scale)
        return [], (scale or self.scale)

    def lines(self, title_rx):
        """Amount lines of a note: [(caption, cy, py)] in rupees, without heading and total lines."""
        rows, scale = self.note(title_rx)
        out = []
        for cap, nums in rows:
            if not cap or len(nums) < 1 or re.match(r"^\s*(total|grand total|sub[- ]?total)\b", cap, re.I):
                continue
            cy = self._rs(nums[0], scale)
            py = self._rs(nums[1], scale) if len(nums) > 1 else 0
            out.append((cap, cy, py))
        return out

    def all_rows(self, visible_only=False):
        for fname, sheet, visible, rows in self.sheets:
            if visible_only and not visible:
                continue
            for r in rows:
                yield fname, sheet, r

    def pans(self) -> dict:
        """name key -> PAN, from any sheet listing names with PANs (salary, TDS returns ...)."""
        if self._pan is None:
            self._pan = {}
            for _, sheet, r in self.all_rows():
                cells = [_txt(v) for v in r]
                pan = next((c for c in cells if re.fullmatch(PAN_RX, c)), None)
                if not pan:
                    continue
                name = next((c for c in cells if re.fullmatch(r"[A-Za-z][A-Za-z .]{3,}", c) and c != pan
                             and not re.fullmatch(r"[A-Z]", c)), None)
                if name:
                    self._pan.setdefault(_key(name), pan)
        return self._pan

    def pan_of(self, name) -> str:
        k = _key(name)
        p = self.pans()
        if k in p:
            return p[k]
        for kk, v in p.items():
            if _same(kk, k):
                return v
        return ""

    @staticmethod
    def fit(parts: list, target: int | None):
        """Make integer parts add up to target by moving the difference into the largest part."""
        if target is None or not parts:
            return parts
        diff = target - sum(parts)
        if diff:
            i = max(range(len(parts)), key=lambda j: abs(parts[j]))
            parts[i] += diff
        return parts

    # ------------------------------------------------------------ fixed assets
    def fixed_assets(self):
        for fname, sheet, visible, rows in self.sheets:
            if not visible:
                continue
            hi = next((i for i, r in enumerate(rows[:40]) if any(re.search(r"gross\s*(block|carrying)", _txt(v), re.I) for v in r)
                       and any(re.search(r"net\s*(block|carrying)", _txt(v), re.I) for v in r)), None)
            if hi is None:
                continue
            head = [_txt(v).lower() for v in rows[hi]]
            gcol = next(i for i, v in enumerate(head) if re.search(r"gross", v))
            dcol = next((i for i, v in enumerate(head) if re.search(r"depreciation|amorti", v) and i > gcol), None)
            ncol = next(i for i, v in enumerate(head) if re.search(r"net", v))
            if dcol is None or not gcol < dcol < ncol:
                continue
            self._schedule(rows[hi + 1:], gcol, dcol, ncol, f"{fname}: {sheet} (fixed asset schedule)")
            return

    def _schedule(self, rows, gcol, dcol, ncol, why):
        part = "tangible"
        acc = {"tangible": {}, "intangible": {}}
        for r in rows:
            cap = _txt(r[0]) if r else ""
            if not cap:
                cap = next((_txt(v) for v in r[:gcol] if _txt(v) and not _isnum(v)), "")
            if re.search(r"intangible", cap, re.I) and not any(_isnum(v) for v in r[gcol:ncol + 2]):
                part = "intangible"
                continue
            if re.search(r"^\s*(tangible|property)", cap, re.I) and not any(_isnum(v) for v in r[gcol:ncol + 2]):
                part = "tangible"
                continue
            if re.search(r"previous year|grand total", cap, re.I):
                break
            if not cap or re.match(r"^\s*(total|sub[- ]?total)\b", cap, re.I) or re.search(r"capital work|under development", cap, re.I):
                continue
            g = [_num(v) for v in r[gcol:dcol]]
            dp = [_num(v) for v in r[dcol:ncol]]
            if len(g) < 3 or len(dp) < 3 or not any(g + dp):
                continue
            s = self.scale
            q = lambda x: self._rs(x, s)  # noqa: E731
            go, ga, gc, gd = q(g[0]), q(g[1]), q(g[-1]), q(sum(g[2:-1], Decimal(0)))
            do, dy, dc, dd = q(dp[0]), q(dp[1]), q(dp[-1]), q(sum(dp[2:-1], Decimal(0)))
            if self.round_unit > 1:
                # rounded one by one, the closing balance can be a unit off opening + movements
                gc, dc = go + ga - gd, do + dy - dd
            if part == "tangible":
                m = _classify(cap, TANGIBLE_CLASSES, "OtherTangibleAssetsMember")
            else:
                m = _classify(cap, INTANGIBLE_CLASSES, "OtherIntangibleAssetsMember")
            a = acc[part].setdefault(m, [0] * 8)
            for i, v in enumerate((go, ga, gd, gc, do, dy, dd, dc)):
                a[i] += v
        lives = self._lives()
        method = self._method()
        dep_total = 0
        for part, classes in acc.items():
            if not classes:
                continue
            tang = part == "tangible"
            face_cy = self.face("TangibleAssets" if tang else "IntangibleAssets", "CY")
            face_py = self.face("TangibleAssets" if tang else "IntangibleAssets", "PY")
            # fit the carrying amounts to the balance sheet (rounding in the schedule)
            ms = list(classes)
            net_cy = self.fit([classes[m][3] - classes[m][7] for m in ms], face_cy)
            net_py = self.fit([classes[m][0] - classes[m][4] for m in ms], face_py)
            for m, ncy, npy in zip(ms, net_cy, net_py):
                a = classes[m]
                a[7] = a[3] - ncy
                a[4] = a[0] - npy
            if tang:
                T, SubAx, Sub, CAx = "DisclosureOfTangibleAssetsTable", "SubClassesOfTangibleAssetsAxis", "OwnedAssetsMember", "CarryingAmountAccumulatedDepreciationAndGrossCarryingAmountAxis"
                ClsAx, Acc, X, Dep, Add = "ClassesOfTangibleAssetsAxis", "AccumulatedDepreciationAndImpairmentMember", "TangibleAssets", "DepreciationTangibleAssets", ["AdditionsOtherThanThroughBusinessCombinationsTangibleAssets"]
                Disp = ["DisposalsTangibleAssetsOthers", "DisposalsTangibleAssets"]
            else:
                T, SubAx, Sub, CAx = "DisclosureOfIntangibleAssetsTable", "SubClassesOfIntangibleAssetsAxis", "IntangibleAssetsOtherThanInternallyGeneratedMember", "CarryingAmountAccumulatedAmortizationAndImpairmentAndGrossCarryingAmountAxis"
                ClsAx, Acc, X, Dep, Add = "ClassesOfIntangibleAssetsAxis", "AccumulatedAmortizationAndImpairmentMember", "IntangibleAssets", "AmortizationIntangibleAssets", ["AdditionsOtherThanThroughBusinessCombinationsIntangibleAssets", "AdditionsToIntangibleAssets"]
                Disp = ["DisposalsIntangibleAssetsOthers", "DisposalsIntangibleAssets"]
            tot = [sum(a[i] for a in classes.values()) for i in range(8)]
            for m, a in list(classes.items()) + [("", tot)]:
                go, ga, gd, gc, do, dy, dd, dc = a
                base = {ClsAx: m} if m else {}
                # changes are closing - opening, so gross - accumulated = carrying holds for the movement too;
                # anything additions/disposals/depreciation don't explain goes to other adjustments
                adj_g, adj_d = (gc - go) - (ga - gd), (dc - do) - (dy - dd)
                Adj = ["OtherAdjustments" + X + "Others", "OtherAdjustments" + X]
                gross = {X: {"CY": gc, "PY": go}, "ChangesIn" + X: {"CY": gc - go}}
                for e in Add:
                    gross[e] = {"CY": ga}
                for e in Disp:
                    gross[e] = {"CY": gd}
                accd = {X: {"CY": dc, "PY": do}, Dep: {"CY": dy}, "ChangesIn" + X: {"CY": dc - do}}
                for e in Disp:
                    accd[e] = {"CY": dd}
                net = {X: {"CY": gc - dc, "PY": go - do}, Dep: {"CY": -dy}, "ChangesIn" + X: {"CY": (gc - dc) - (go - do)}}
                for e in Add:
                    net[e] = {"CY": ga}
                for e in Disp:
                    net[e] = {"CY": gd - dd}
                for e in Adj:
                    gross[e], accd[e], net[e] = {"CY": adj_g}, {"CY": adj_d}, {"CY": adj_g - adj_d}
                self.put_row(T, dict(base, **{SubAx: Sub, CAx: "GrossCarryingAmountMember"}), gross, self._why)
                self.put_row(T, dict(base, **{SubAx: Sub, CAx: Acc}), accd, self._why)
                self.put_row(T, dict(base, **{SubAx: Sub}), net, self._why)
                if not m:  # totals without the owned/leased split too
                    self.put_row(T, {CAx: "GrossCarryingAmountMember"}, gross, self._why)
                    self.put_row(T, {CAx: Acc}, accd, self._why)
            # useful lives and method
            info = "DisclosureOfAdditionalInformationTangibleAssetsTable" if tang else "DisclosureOfAdditionalInformationIntangibleAssetsTable"
            for m in classes:
                life = lives.get(m) or SCHEDULE_II.get(m)
                vals = {"DepreciationMethodTangibleAssets" if tang else "DescriptionOfAmortizationMethodUsed": {"CY": method, "PY": method}}
                if life:
                    vals["UsefulLivesOrDepreciationRatesTangibleAssets" if tang else "UsefulLivesOrAmortizationRatesIntangibleAssets"] = {"CY": f"{life} YEARS", "PY": f"{life} YEARS"}
                self.put_row(info, {ClsAx: m, SubAx: Sub}, vals, "notes: depreciation policy and useful lives")
            ref = "REFER TO CHILD MEMBER"
            if len(classes) > 1:
                self.put_row(info, {SubAx: Sub}, {
                    "DepreciationMethodTangibleAssets" if tang else "DescriptionOfAmortizationMethodUsed": {"CY": method, "PY": method},
                    "UsefulLivesOrDepreciationRatesTangibleAssets" if tang else "UsefulLivesOrAmortizationRatesIntangibleAssets": {"CY": ref, "PY": ref}},
                    "notes: depreciation policy and useful lives")
            else:
                m = next(iter(classes))
                life = lives.get(m) or SCHEDULE_II.get(m)
                self.put_row(info, {SubAx: Sub}, {
                    "DepreciationMethodTangibleAssets" if tang else "DescriptionOfAmortizationMethodUsed": {"CY": method, "PY": method},
                    "UsefulLivesOrDepreciationRatesTangibleAssets" if tang else "UsefulLivesOrAmortizationRatesIntangibleAssets": {"CY": f"{life} YEARS", "PY": f"{life} YEARS"}},
                    "notes: depreciation policy and useful lives")
            if tang:
                dep_total = sum(a[5] for a in classes.values())
            self.notes.append(f"{'Tangible' if tang else 'Intangible'} assets table built from the fixed asset schedule "
                              f"({len(classes)} classes), carrying amounts matched to the balance sheet.")
        for t, x in (("DisclosureOfTangibleAssetsTable", "TangibleAssets"), ("DisclosureOfIntangibleAssetsTable", "IntangibleAssets")):
            self.prior_py(t, x)
        # split the P&L depreciation line into depreciation and amortisation
        dda = self.face("DepreciationDepletionAndAmortisationExpense")
        if dda is not None and acc["tangible"]:
            dep = min(dep_total, dda)
            for local, v in (("DepreciationExpense", dep), ("AmortisationExpense", dda - dep)):
                eid = G + local
                if self.schema.element(eid):
                    self.f.set_value(eid, "CY", str(v))
                    self.src[(eid, "CY")] = "split of depreciation and amortisation from the fixed asset schedule"

    _why = "fixed asset schedule"

    def _policy_text(self) -> str:
        return self.d.get("policies", "other", "notes") if hasattr(self.d, "get") else ""

    def _method(self) -> str:
        t = (self._policy_text() + "\n" + self.d.all).lower()
        i = t.find("depreciation")
        seg = t[i:i + 3000] if i >= 0 else t
        if "written down" in seg or "wdv" in seg:
            return "Written Down Value Method"
        if "straight line" in seg or "slm" in seg:
            return "Straight Line Method"
        return "Written Down Value Method"

    def _lives(self) -> dict:
        t = self.d.all
        out = {}
        m = re.search(r"(life of assets|useful li[fv]es?)[^\n]*\n(.{0,800})", t, re.I | re.S)
        if not m:
            return out
        for line in m.group(2).splitlines():
            mm = re.match(r"\s*([A-Za-z][A-Za-z &,./()-]+?)\s+(\d{1,2})\s*(years?)?\s*$", line)
            if not mm:
                continue
            cap, n = mm.group(1), int(mm.group(2))
            if re.search(r"software", cap, re.I):
                out.setdefault("ComputerSoftwareMember", n)
                continue
            out.setdefault(_classify(cap, TANGIBLE_CLASSES, "OtherTangibleAssetsMember"), n)
        return out

    # ------------------------------------------------------------ share capital
    def _capital_text(self) -> str:
        rows, _ = self.note(r"share\s*capital")
        return "\n".join(c + " " + " ".join(str(n) for n in nums) for c, nums in rows)

    def share_capital(self):
        cy, py = self.face("ShareCapital", "CY"), self.face("ShareCapital", "PY")
        if not cy and not py:
            return
        t = self._capital_text() or self.d.all
        par = Decimal(10)
        auth_n = None
        m = re.search(r"authori[sz]ed[^\n]*\n?[^\n]*?([\d,]{3,})\s*(?:\(\s*[\d,]+\s*\))?\s*equity\s+shares?\s+of\s+(?:rs\.?|₹|inr)\s*([\d.]+)", t, re.I)
        if m:
            auth_n = int(m.group(1).replace(",", ""))
            par = Decimal(m.group(2).rstrip("."))
        else:
            m = re.search(r"equity\s+shares?\s+of\s+(?:rs\.?|₹|inr)\s*([\d.]+)", t, re.I)
            if m:
                par = Decimal(m.group(1).rstrip("."))
        pr = int(par) if par == int(par) else par
        rows = {}
        for pk, v in (("CY", cy), ("PY", py)):
            if not v:
                continue
            n = int(Decimal(v) / par)
            rows[pk] = n
        vals = {}
        for pk, n in rows.items():
            v = cy if pk == "CY" else py
            for e in ("NumberOfSharesIssued", "NumberOfSharesSubscribedAndFullyPaid", "NumberOfSharesSubscribed", "NumberOfSharesPaidUp"):
                vals.setdefault(e, {})[pk] = n
            for e in ("ValueOfSharesIssued", "ValueOfSharesSubscribedAndFullyPaid", "ValueOfSharesSubscribed", "ValueOfSharesCalled",
                      "ValueOfSharesPaidUp", "ShareCapital"):
                vals.setdefault(e, {})[pk] = v
            if auth_n:
                vals.setdefault("NumberOfSharesAuthorised", {})[pk] = auth_n
                vals.setdefault("ValueOfSharesAuthorised", {})[pk] = int(auth_n * par)
            for e in ("NumberOfSharesSubscribedButNotFullyPaid", "ValueOfSharesSubscribedButNotFullyPaid", "CallsUnpaid",
                      "CallsUnpaidByDirectors", "CallsUnpaidByOfficers", "CallsUnpaidByDirectorsAndOfficers", "CallsUnpaidByOthers",
                      "ForfeitedShares", "ForfeitedSharesReissued",
                      "ApplicationMoneyReceivedForAllotmentOfSecuritiesAndDueForRefundAndInterestAccruedThereon",
                      "ApplicationMoneyReceivedForAllotmentOfSecuritiesAndDueForRefundPrincipal",
                      "ApplicationMoneyReceivedForAllotmentOfSecuritiesAndDueForRefundInterestAccrued"):
                vals.setdefault(e, {})[pk] = 0
        # movement in the number of shares (MCA needs it for both years): opening from the reconciliation
        T = "DisclosureOfClassesOfShareCapitalTable"
        recon = self._capital_text()
        py_open = None
        m = re.search(r"opening balance[^\n]*?\s([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s*$", recon, re.I | re.M)
        if m and float(m.group(2)) >= 1000:
            py_open = int(float(m.group(2)))
        cy_n, py_n = rows.get("CY"), rows.get("PY")
        if py_n is not None and py_open is None:
            py_open = py_n
        mv = self.zeros(T, r"DuringPeriod$|DuringLastFiveYears$|^NumberOfShares(IssuedIn|IssuedAs|Allotted|IssuedUnder|BoughtBack|Redeemed)|"
                           r"^NumberOfOtherIssue|^DecreaseIn|^OtherDecrease|^IncreaseDecreaseIn(NumberOfSharesOutstanding|ShareCapital)$")
        bought = re.search(r"buy[- ]?back|bought back|deletions?", recon + " " + self.d.all[:200000], re.I)
        for pk, close, open_ in (("CY", cy_n, py_n), ("PY", py_n, py_open)):
            if close is None or open_ is None:
                continue
            ch = close - open_
            amt = int(ch * par)
            mv["IncreaseDecreaseInNumberOfSharesOutstanding"][pk] = ch
            mv["IncreaseDecreaseInShareCapital"][pk] = amt
            if ch < 0:
                n_el, a_el = ("NumberOfSharesBoughtBack", "DecreaseInAmountOfSharesBoughtBack") if bought else ("OtherDecreaseInNumberOfShares", "OtherDecreaseInAmountOfShares")
                for e, v in ((n_el, -ch), (a_el, -amt), ("DecreaseInShareCapitalDuringPeriod", -amt), ("DecreaseInNumberOfSharesDuringPeriod", -ch)):
                    if e in mv:
                        mv[e][pk] = v
            elif ch > 0:
                for e, v in (("NumberOfOtherIssuesOfShares", ch), ("AmountOfOtherIssuesDuringPeriod", amt),
                             ("AggregateNumberOfSharesIssuedDuringPeriod", ch), ("AggregateAmountOfIncreaseInShareCapitalDuringPeriod", amt)):
                    if e in mv:
                        mv[e][pk] = v
        bb = sum(mv.get("NumberOfSharesBoughtBack", {}).values())
        if bb and "AggregateNumberOfSharesBoughtBackDuringLastFiveYears" in mv:
            mv["AggregateNumberOfSharesBoughtBackDuringLastFiveYears"] = {"CY": bb, "PY": mv["NumberOfSharesBoughtBack"].get("PY", 0)}
        vals.update(mv)
        out = {pk: n for pk, n in (("CY", cy_n), ("PY", py_n), ("OPEN", py_open)) if n is not None}
        vals["NumberOfSharesOutstanding"] = dict(out)
        if "OPEN" in out:
            vals.setdefault("ShareCapital", {})["OPEN"] = int(out["OPEN"] * par)
        why = "share capital note"
        self.put_row("DisclosureOfClassesOfShareCapitalTable", {"ClassesOfShareCapitalAxis": "EquitySharesMember"}, vals, why)
        v1 = dict(vals)
        v1["TypeOfShare"] = {pk: "EQUITY" for pk in rows}
        v1["ParValuePerShare"] = {pk: pr for pk in rows}
        v1["AmountPerShareCalledInCaseSharesNotFullyCalled"] = {pk: 0 for pk in rows}
        self.put_row("DisclosureOfClassesOfShareCapitalTable", {"ClassesOfShareCapitalAxis": "EquityShares1Member"}, v1, why)
        self.shares = rows
        for pk in ("CY", "PY"):
            eid = G + "ApplicationMoneyReceivedForAllotmentOfSecuritiesAndDueForRefundAndInterestAccruedThereon"
            if self.schema.element(eid) and self.f.get_value(eid, pk) in (None, ""):
                self.f.set_value(eid, pk, "0")
        self.notes.append(f"Share capital table: {rows.get('CY') or rows.get('PY'):,} equity shares of Rs {pr} each"
                          + (f", {auth_n:,} authorised." if auth_n else "."))

    def shareholders(self):
        rows, _ = self.note(r"share\s*capital")
        if not rows:
            return
        holders = []
        on = False
        for cap, nums in rows:
            if re.search(r"more than\s*5\s*%|holding more than|5\s*%\s*shares", cap, re.I):
                on = True
                continue
            if on and re.match(r"^\s*\(?[d-z]\)|^\s*details of promoter", cap, re.I):
                break
            if on and cap and nums and not re.search(r"equity shares|total|number of shares", cap, re.I):
                n_cy = int(nums[0])
                n_py = int(nums[2]) if len(nums) > 2 else n_cy
                holders.append((cap, n_cy, n_py))
        if not holders:
            return
        total = getattr(self, "shares", {})
        why = "share capital note: shareholders holding more than 5%"
        for i, (name, ncy, npy) in enumerate(holders[:20], 1):
            sh = f"Shareholder{i}Member"
            nm = _clean_name(name).upper()
            pan = self.pan_of(name)
            vals = {"TypeOfShare": {"CY": "EQUITY", "PY": "EQUITY"}, "NameOfShareholder": {"CY": nm, "PY": nm},
                    "CountryOfIncorporationOrResidenceOfShareholder": {"CY": "INDIA", "PY": "INDIA"},
                    "NumberOfSharesHeldInCompany": {"CY": ncy, "PY": npy}}
            if pan:
                vals["PANOfShareholder"] = {"CY": pan, "PY": pan}
            pct = {}
            for pk, n in (("CY", ncy), ("PY", npy)):
                if total.get(pk):
                    pct[pk] = str((Decimal(n) / Decimal(total[pk])).quantize(Decimal("0.0001")))
            if pct:
                vals["PercentageOfShareholdingInCompany"] = pct
            dims = {"ClassesOfShareCapitalAxis": "EquityShares1Member", "NameOfShareholderAxis": sh}
            self.put_row("DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable", dims, vals, why)
            nums = {"NumberOfSharesHeldInCompany": {"CY": ncy, "PY": npy}}
            self.put_row("DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable",
                         {"ClassesOfShareCapitalAxis": "EquitySharesMember", "NameOfShareholderAxis": sh}, nums, why)
            self.put_row("DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable", {"NameOfShareholderAxis": sh}, nums, why)
            if not pan:
                self.ask("DisclosureOfShareholdingMoreThanFivePerCentInCompanyTable", dims, "PANOfShareholder",
                         f"PAN of shareholder {nm}")
        for pk in ("CY", "PY"):
            eid = G + "WhetherThereAreAnyShareholdersHoldingMoreThanFivePerCentSharesInCompany"
            if self.schema.element(eid):
                self.f.set_value(eid, pk, "true")
        self.notes.append(f"Shareholders over 5%: {', '.join(_clean_name(h[0]) for h in holders)}.")

    # ------------------------------------------------------------ reserves
    def reserves(self):
        rows, scale = self.note(r"reserves?\s*(and|&)\s*surplus")
        if not rows or not (self.face("ReservesAndSurplus", "CY") or self.face("ReservesAndSurplus", "PY")):
            return
        q = lambda x: self._rs(x, scale)  # noqa: E731
        comps = []
        cur = None
        for cap, nums in rows:
            if cap and not nums and not re.match(r"^\s*(add|less)\b", cap, re.I):
                m = self._reserve_member(cap)
                if m:
                    cur = {"member": m, "caption": cap, "open": None, "add": [], "less": [], "close": None}
                    comps.append(cur)
                continue
            if cur is None or not nums:
                continue
            cy, py = q(nums[0]), q(nums[1]) if len(nums) > 1 else 0
            if re.search(r"balance carried|closing balance|total", cap, re.I) and comps and cur is comps[-1] and len(comps) > 1 and re.search(r"carried", cap, re.I):
                continue
            if re.search(r"as per last|opening|beginning|brought forward|last balance sheet", cap, re.I):
                cur["open"] = (cy, py)
            elif re.match(r"^\s*add\b", cap, re.I):
                cur["add"].append((cap, cy, py))
            elif re.match(r"^\s*less\b", cap, re.I):
                cur["less"].append((cap, cy, py))
            elif not cap.strip() or re.search(r"closing|balance at the end", cap, re.I):
                cur["close"] = (cy, py)
        comps = [c for c in comps if c["open"] or c["close"] or c["add"] or c["less"]]
        if not comps:
            return
        # closing = last subtotal of the block, else open + additions - deductions
        closes_cy, closes_py = [], []
        for c in comps:
            o = c["open"] or (0, 0)
            calc_cy = o[0] + sum(a[1] for a in c["add"]) - sum(a[1] for a in c["less"])
            calc_py = o[1] + sum(a[2] for a in c["add"]) - sum(a[2] for a in c["less"])
            cl = c["close"] or (calc_cy, calc_py)
            closes_cy.append(cl[0])
            closes_py.append(cl[1])
        closes_cy = self.fit(closes_cy, self.face("ReservesAndSurplus", "CY"))
        closes_py = self.fit(closes_py, self.face("ReservesAndSurplus", "PY"))
        for c, ccy, cpy in zip(comps, closes_cy, closes_py):
            o = c["open"] or (cpy, 0)
            vals = {"Reserves": {"CY": ccy, "PY": cpy, "OPEN": o[1]}}
            adds = {"ProfitLossForPeriod": [0, 0], "OtherAdditionsToReserves": [0, 0]}
            for cap, cy, py in c["add"]:
                k = "ProfitLossForPeriod" if re.search(r"profit|surplus for|loss for", cap, re.I) and c["member"] == "SurplusMember" else "OtherAdditionsToReserves"
                adds[k][0] += cy
                adds[k][1] += py
            less = {"TransferToGeneralReserve": [0, 0], "OtherDeductionsToReserves": [0, 0], "DividendAppropriation": [0, 0],
                    "DividendTaxAppropriation": [0, 0]}
            for cap, cy, py in c["less"]:
                if re.search(r"transfer", cap, re.I) and re.search(r"reserve", cap, re.I):
                    k = "TransferToGeneralReserve"
                elif re.search(r"tax on (proposed |interim |final )?dividend|dividend distribution tax", cap, re.I):
                    k = "DividendTaxAppropriation"
                elif re.search(r"dividend", cap, re.I):
                    k = "DividendAppropriation"
                else:
                    k = "OtherDeductionsToReserves"
                less[k][0] += cy
                less[k][1] += py
            if c["member"] != "SurplusMember":
                # MCA's hypercube allows dividend appropriations only on the surplus
                for k in ("DividendAppropriation", "DividendTaxAppropriation"):
                    for i in (0, 1):
                        less["OtherDeductionsToReserves"][i] += less[k][i]
                    del less[k]
            for k, (a, b) in list(adds.items()) + list(less.items()):
                vals[k] = {"CY": a, "PY": b}
            add_t = [adds["ProfitLossForPeriod"][i] + adds["OtherAdditionsToReserves"][i] for i in (0, 1)]
            ded_t = [less["OtherDeductionsToReserves"][i] for i in (0, 1)]
            app_t = [less["TransferToGeneralReserve"][i] + less.get("DividendAppropriation", [0, 0])[i] + less.get("DividendTaxAppropriation", [0, 0])[i] for i in (0, 1)]
            vals["AdditionsToReserves"] = {"CY": add_t[0], "PY": add_t[1]}
            vals["DeductionsToReserves"] = {"CY": ded_t[0], "PY": ded_t[1]}
            vals["AppropriationsForDividendDividendTaxAndGeneralReserve"] = {"CY": app_t[0], "PY": app_t[1]}
            vals["ChangesInReserves"] = {"CY": add_t[0] - ded_t[0] - app_t[0], "PY": add_t[1] - ded_t[1] - app_t[1]}
            # an opening figure that disagrees with last year's closing (as typed in the note) is rebased
            vals["DescriptionOfNatureAndPurposeOfOtherReserves"] = {"CY": "NA"}
            self.put_row("StatementOfChangesInReservesTable", {"ComponentsOfReservesAxis": c["member"]}, vals, "reserves and surplus note")
        self.notes.append("Statement of changes in reserves built from the reserves note: "
                          + ", ".join(re.sub(r"\s*[/(].*", "", c["caption"])[:40] for c in comps) + ".")

    @staticmethod
    def _reserve_member(cap: str) -> str | None:
        c = cap.lower()
        for rx, m in ((r"surplus|profit and loss|retained", "SurplusMember"), (r"capital redemption", "CapitalRedemptionReservesMember"),
                      (r"securities premium|share premium", "SecuritiesPremiumAccountMember"), (r"general reserve", "GeneralReserveMember"),
                      (r"debenture redemption", "DebentureRedemptionReserveMember"), (r"revaluation", "RevaluationReserveMember"),
                      (r"capital reserve", "CapitalReservesMember"), (r"foreign currency", "ForeignCurrencyTranslationReserveMember"),
                      (r"share options?|esop|stock option", "ShareOptionsOutstandingAccountMember"), (r"reserve", "OtherReservesOthersMember")):
            if re.search(rx, c):
                return m
        return None

    # ------------------------------------------------------------ provisions
    def provisions(self):
        for face, member, rx in (("LongTermProvisions", "LongTermMember", r"long[- ]?term\s*provisions?"),
                                 ("ShortTermProvisions", "ShortTermMember", r"short[- ]?term\s*provisions?")):
            cy, py = self.face(face, "CY") or 0, self.face(face, "PY") or 0
            if not cy and not py:
                continue
            parts = {}
            for cap, a, b in self.lines(rx):
                c = cap.lower()
                if "gratuity" in c:
                    k = "ProvisionGratuity"
                elif re.search(r"leave|encash|compensated absence", c):
                    k = "ProvisionLeaveEncashment"
                elif "pension" in c:
                    k = "ProvisionPension"
                elif re.search(r"bonus|salar|employee|staff", c):
                    k = "ProvisionOtherEmployeeRelatedLiabilities"
                elif re.search(r"income\s*tax|taxation|current tax", c):
                    k = "ProvisionForCurrentTax"
                elif re.search(r"\bcsr\b|social responsibility", c):
                    k = "CSRExpenditureProvision"
                elif "dividend" in c:
                    k = "ProvisionForProposedEquityDividend"
                else:
                    k = "OtherProvisions"
                p = parts.setdefault(k, [0, 0])
                p[0] += a
                p[1] += b
            if not parts:
                parts["OtherProvisions"] = [cy, py]
            ks = list(parts)
            fc = self.fit([parts[k][0] for k in ks], cy)
            fp = self.fit([parts[k][1] for k in ks], py)
            vals = {k: {"CY": a, "PY": b} for k, a, b in zip(ks, fc, fp)}
            emp = [k for k in ks if k in ("ProvisionGratuity", "ProvisionLeaveEncashment", "ProvisionPension", "ProvisionOtherEmployeeRelatedLiabilities")]
            if emp:
                vals["ProvisionsForEmployeeBenefits"] = {"CY": sum(vals[k]["CY"] for k in emp), "PY": sum(vals[k]["PY"] for k in emp)}
            if "ProvisionForCurrentTax" in vals:
                vals["ProvisionForCorporateTax"] = dict(vals["ProvisionForCurrentTax"])
            if "ProvisionForProposedEquityDividend" in vals:
                vals["ProvisionForProposedDividend"] = dict(vals["ProvisionForProposedEquityDividend"])
            vals["Provisions"] = {"CY": cy, "PY": py}
            self.put_row("DisclosureOfBreakupOfProvisionsTable", {"ClassificationBasedOnTimePeriodAxis": member}, vals,
                         f"{face.replace('Provisions', ' provisions').replace('Term', '-term').lower()} note")

    # ------------------------------------------------------------ trade receivables
    def receivables(self):
        cy, py = self.face("TradeReceivables", "CY"), self.face("TradeReceivables", "PY")
        if not cy and not py:
            return
        rows, scale = self.note(r"trade\s*receivables?")
        txt = " ".join(c for c, _ in rows).lower()
        # ageing: everything outstanding for more than six months
        over, on, seen = [0, 0], False, False
        for cap, nums in rows:
            c = cap.lower()
            if re.search(r"outstanding|months|years?\b", c) and (not nums or re.search(r"^\s*\(?[a-h]\)", c)):
                on = bool(re.search(r"6 months to|six months to|exceeding (6|six) months|more than (6|six) months|1 years? to|2 years? to|more than (1|2|3) years?", c)) \
                    and not re.search(r"less than (6|six) months", c)
                seen = True
                if not nums:
                    continue
            if on and nums and c.strip() and not re.match(r"^\s*total", c):
                over[0] += self._rs(nums[0], scale)
                over[1] += self._rs(nums[1], scale) if len(nums) > 1 else 0
        eid = G + "AggregateAmountOfTradeReceivablesOutstandingForPeriodExceedingSixMonths"
        if seen and self.schema.element(eid):
            for pk, v in zip(("CY", "PY"), over):
                if self.f.get_value(eid, pk) in (None, "", "0"):
                    self.f.set_value(eid, pk, str(v))
                    self.src[(eid, pk)] = "trade receivables ageing in the notes (over six months)"
        member = "SecuredConsideredGoodMember" if re.search(r"(?<!un)secured", txt) and "unsecured" not in txt else "UnsecuredConsideredGoodMember"
        allow_cy, allow_py = self.face("AllowanceForBadAndDoubtfulDebts", "CY"), self.face("AllowanceForBadAndDoubtfulDebts", "PY")
        vals = self.zeros("SubclassificationOfTradeReceivablesTable", r"DueBy")
        vals.update({"TradeReceivables": {"CY": cy, "PY": py}, "TradeReceivablesGross": {"CY": cy, "PY": py}})
        if allow_cy or allow_py:
            vals["TradeReceivablesGross"] = {"CY": (cy or 0) + (allow_cy or 0), "PY": (py or 0) + (allow_py or 0)}
            vals["AllowanceForBadAndDoubtfulDebts"] = {"CY": allow_cy or 0, "PY": allow_py or 0}
        self.put_row("SubclassificationOfTradeReceivablesTable", {"ClassificationOfAssetsBasedOnSecurityAxis": member}, vals,
                     "trade receivables note")

    # ------------------------------------------------------------ loans and advances
    LOAN_CLASSES = [
        (r"value to be received|recoverable in cash or in kind", "LoansAdvancesValueBeReceivedMember"),
        (r"capital advance", "CapitalAdvancesMember"),
        (r"security deposit|deposits? \(security\)|rent deposit", "SecurityDepositsMember"),
        (r"prepaid", "PrepaidExpensesMember"),
        (r"mat credit", "MATCreditEntitlementMember"),
        (r"tds|tax deducted", "TaxDeductedAtSourceMember"),
        (r"advance (income )?tax", "AdvanceIncomeTaxPaidMember"),
        (r"income\s*tax|tax refund|gst|vat|service tax|cenvat|input tax", "OtherTaxesReceivableMember"),
        (r"employee|staff", "LoansAdvancesGivenEmployeesMember"),
        (r"director", "LoansAdvancesGivenDirectorsMember"),
        (r"supplier|vendor|creditor", "LoansAdvancesGivenSuppliersMember"),
        (r"related part", "LoansAdvancesGivenOtherRelatedPartiesMember"),
        (r"inter[- ]?corporate deposit", "IntercorporateDepositsMember"),
        (r"deposit", "OtherDepositAssetsMember"),
    ]

    def loans(self):
        for face, member, rx in (("LongTermLoansAndAdvances", "LongTermMember", r"long[- ]?term\s*loans\s*(and|&)\s*advances"),
                                 ("ShortTermLoansAndAdvances", "ShortTermMember", r"short[- ]?\s*term\s*loans\s*(and|&)\s*advances")):
            cy, py = self.face(face, "CY") or 0, self.face(face, "PY") or 0
            if not cy and not py:
                continue
            parts = {}
            for cap, a, b in self.lines(rx):
                k = _classify(cap, self.LOAN_CLASSES, "OtherLoansAndAdvancesOthersMember")
                p = parts.setdefault(k, [0, 0])
                p[0] += a
                p[1] += b
            if not parts:
                parts["OtherLoansAndAdvancesOthersMember"] = [cy, py]
            ks = list(parts)
            fc, fp = self.fit([parts[k][0] for k in ks], cy), self.fit([parts[k][1] for k in ks], py)
            for k, a, b in zip(ks, fc, fp):
                self.put_row("LoansAndAdvancesTable", {"ClassificationBasedOnTimePeriodAxis": member, "ClassificationOfLoansAndAdvancesAxis": k,
                                                       "ClassificationOfAssetsBasedOnSecurityAxis": "UnsecuredConsideredGoodMember"},
                             dict(self.zeros("LoansAndAdvancesTable", r"DueBy"),
                                  LoansAndAdvancesGross={"CY": a, "PY": b}, LoansAndAdvances={"CY": a, "PY": b}),
                             f"{face.replace('LoansAndAdvances', '').replace('Term', '-term ').lower()}loans and advances note")

    # ------------------------------------------------------------ borrowings
    BORROWING_CLASSES = [
        (r"overdraft|cash credit|repayable on demand|\bod\b|\bcc\b", "LoansRepayableOnDemandFromBanksMember"),
        (r"vehicle|car loan", "LoansTakenForVehiclesMember"),
        (r"term loan.*bank|bank.*term loan", "RupeeTermLoansFromBanksMember"),
        (r"term loan", "RupeeTermLoansFromOthersMember"),
        (r"director", "LoansAndAdvancesFromDirectorsMember"),
        (r"inter[- ]?corporate", "IntercorporateBorrowingsMember"),
        (r"debenture", "NonConvertibleDebenturesOthersMember"),
        (r"working capital", "WorkingCapitalLoansFromBanksMember"),
        (r"deposit", "DepositsFromOthersMember"),
    ]

    def borrowings(self):
        tid = self.tid("ClassificationOfBorrowingsTable")
        if not tid:
            return
        ax = next(a for a in self.schema.tables[tid]["axes"] if a["id"].endswith("ClassificationOfBorrowingsAxis"))
        parent = {m["id"].split("_", 1)[1]: (m.get("parent") or "").split("_", 1)[-1] for m in ax["members"]}
        for face, member, rx in (("LongTermBorrowings", "LongTermMember", r"long[- ]?term\s*borrowings?"),
                                 ("ShortTermBorrowings", "ShortTermMember", r"short[- ]?term\s*borrowings?")):
            cy, py = self.face(face, "CY") or 0, self.face(face, "PY") or 0
            if not cy and not py:
                continue
            rows, scale = self.note(rx)
            secured = None
            parts = {}
            for cap, nums in rows:
                if cap and not nums:
                    if re.search(r"\bunsecured\b", cap, re.I):
                        secured = False
                    elif re.search(r"\bsecured\b", cap, re.I):
                        secured = True
                    continue
                if not cap or not nums or re.match(r"^\s*total", cap, re.I):
                    continue
                sec = secured
                if re.search(r"\bunsecured\b", cap, re.I):
                    sec = False
                elif re.search(r"\bsecured\b", cap, re.I):
                    sec = True
                k = (_classify(cap, self.BORROWING_CLASSES, "OtherLoansAndAdvancesOthersMember"),
                     "SecuredBorrowingsMember" if sec else "UnsecuredBorrowingsMember")
                p = parts.setdefault(k, [0, 0, ""])
                p[0] += self._rs(nums[0], scale)
                p[1] += self._rs(nums[1], scale) if len(nums) > 1 else 0
                m = re.search(r"\(([^)]*secur[^)]*)\)", cap, re.I)
                if sec:
                    p[2] = p[2] or ("Secured - " + (m.group(1) if m else re.sub(r"\s*\(.*", "", cap)))[:200]
            if not parts:
                parts[("OtherLoansAndAdvancesOthersMember", "UnsecuredBorrowingsMember")] = [cy, py, ""]
            ks = list(parts)
            fc, fp = self.fit([parts[k][0] for k in ks], cy), self.fit([parts[k][1] for k in ks], py)
            agg = {}
            for k, a, b in zip(ks, fc, fp):
                cls, sub = k
                # the leaf and each parent up to (not including) the axis root carry the amount
                c = cls
                while c and c not in ("BorrowingsMember", ""):
                    g = agg.setdefault((c, sub), [0, 0, parts[k][2]])
                    g[0] += a
                    g[1] += b
                    c = parent.get(c, "")
            for (cls, sub), (a, b, nat) in agg.items():
                vals = {"Borrowings": {k: v for k, v in (("CY", a), ("PY", b)) if v}}
                if nat:
                    vals["NatureOfSecurity"] = {k: nat for k, v in (("CY", a), ("PY", b)) if v}
                self.put_row("ClassificationOfBorrowingsTable", {"ClassificationBasedOnTimePeriodAxis": member, "ClassificationOfBorrowingsAxis": cls,
                                                                 "SubclassificationOfBorrowingsAxis": sub}, vals,
                             f"{face.replace('Borrowings', '').replace('Term', '-term').lower()} borrowings note")

    # ------------------------------------------------------------ non-current investments
    def investments(self):
        cy, py = self.face("NoncurrentInvestments", "CY") or 0, self.face("NoncurrentInvestments", "PY") or 0
        if not cy and not py:
            return
        rows, scale = self.note(r"non[- ]?current\s*investments?")
        kind = "Investments in mutual funds"
        trade = "Other investments"
        items = []
        for cap, nums in rows:
            c = cap.lower()
            if cap and not nums:
                if re.search(r"mutual fund", c):
                    kind = "Investments in mutual funds"
                elif re.search(r"preference", c):
                    kind = _inv_kind(c, "preference shares")
                elif re.search(r"equity|shares", c):
                    kind = _inv_kind(c, "equity instruments")
                elif re.search(r"debenture|bond", c):
                    kind = "Investments in debentures or bonds"
                elif re.search(r"government|gold", c):
                    kind = "Investments in government or trust securities"
                elif re.search(r"property", c):
                    kind = "Investment property"
                if re.search(r"\btrade\b", c) and "non" not in c:
                    trade = "Trade investments"
                continue
            if not cap or not nums or re.match(r"^\s*(total|less)", cap, re.I):
                continue
            a = self._rs(nums[0], scale)
            b = self._rs(nums[1], scale) if len(nums) > 1 else 0
            if re.search(r"\b(fund|scheme|series)\b", c):
                k = "Investments in mutual funds"
            elif re.search(r"private limited|pvt|limited|ltd", c):
                k = kind if kind.startswith("Investment in ") else _inv_kind(c, "preference shares" if "preference" in c else "equity instruments")
            else:
                k = kind
            items.append([cap, a, b, k])
        if not items:
            items = [["Non-current investments", cy, py, kind]]
        fc = self.fit([i[1] for i in items], cy)
        fp = self.fit([i[2] for i in items], py)
        for n, (it, a, b) in enumerate(zip(items, fc, fp), 1):
            vals = {"NameOfBodyCorporateInWhomInvestmentHasBeenMade": {"CY": it[0][:200], "PY": it[0][:200]},
                    "TypeOfNoncurrentInvestments": {"CY": it[3], "PY": it[3]},
                    "ClassOfNoncurrentInvestments": {"CY": trade, "PY": trade},
                    "NatureOfNoncurrentInvestments": {"CY": "Unquoted", "PY": "Unquoted"},
                    "NoncurrentInvestments": {"CY": a, "PY": b}}
            self.put_row("DetailsOfNoncurrentInvestmentsTable", {"ClassificationOfNoncurrentInvestmentsAxis": f"Column {n}"}, vals,
                         "non-current investments note")
        self.notes.append(f"Non-current investments table: {len(items)} investments, matched to the balance sheet.")

    # ------------------------------------------------------------ signatures: directors and auditors
    def _signature_block(self):
        """Directors and auditor from the balance sheet's signature block."""
        if hasattr(self, "_sig"):
            return self._sig
        sig = {"directors": [], "date": "", "firm": "", "frn": "", "partner": "", "mno": "", "place": ""}
        for fname, sheet, visible, rows in self.sheets:
            if not visible or not re.search(r"^\s*(bs|balance)", sheet, re.I):
                continue
            for i, r in enumerate(rows):
                cells = [_txt(v) for v in r]
                dins = [(j, re.search(r"(\d{8})", c).group(1)) for j, c in enumerate(cells) if re.search(r"DIN", c, re.I) and re.search(r"\d{8}", c)]
                if dins and not sig["directors"]:
                    for j, din in dins:
                        name = desig = ""
                        for back in range(1, 5):
                            if i - back < 0:
                                break
                            c = _txt(rows[i - back][j]) if j < len(rows[i - back]) else ""
                            if not c:
                                continue
                            if not desig and re.fullmatch(r"(managing |whole[- ]time |executive |independent )?(director|chairman.*|ceo|cfo|company secretary|manager)", c, re.I):
                                desig = c
                            elif re.search(r"[A-Za-z]{3,}", c) and not name:
                                name = c.strip("() ")
                                break
                        if name:
                            sig["directors"].append({"name": _clean_name(name), "din": din, "designation": desig or "Director"})
                text = " ".join(cells)
                m = re.search(r"dated?\s*[:\-]?\s*(\d{1,2}[-./]\d{1,2}[-./]\d{4})", text, re.I)
                if m and not sig["date"]:
                    sig["date"] = _iso(m.group(1))
                m = re.search(r"^\s*for\s+(.+?)\s*$", cells[0] if cells else "", re.I)
                if m and not sig["firm"] and re.search(r"associates|& co|and co|chartered|llp|& company", m.group(1), re.I):
                    sig["firm"] = m.group(1).strip()
                m = re.search(r"firm\s*reg(?:istration)?\.?\s*no\.?\s*[:\-]*\s*([0-9]{6}[A-Z]?)", text, re.I)
                if m:
                    sig["frn"] = m.group(1)
                m = re.search(r"m(?:embership)?\.?\s*no\.?\s*[:\-]*\s*(\d{5,6})", text, re.I)
                if m:
                    sig["mno"] = m.group(1)
                if cells and re.fullmatch(r"\(?[A-Za-z .]+\)?", cells[0] or "") and cells[0].startswith("(") and not sig["partner"]:
                    if i + 1 < len(rows) and re.search(r"partner|proprietor", _txt(rows[i + 1][0]), re.I):
                        sig["partner"] = cells[0].strip("() ")
                m = re.search(r"place\s*[:\-]\s*([A-Za-z ]+)", text, re.I)
                if m and not sig["place"]:
                    sig["place"] = m.group(1).strip()
        self._sig = sig
        return sig

    def _full_name(self, name: str) -> str:
        """Expand 'Anil K. Mohindru' to the full name used elsewhere in the documents."""
        best = name
        for other in [d["name"] for d in self._signature_block()["directors"]] + self._rp_people():
            if _same(other, name) and len(other) > len(best):
                best = other
        return _clean_name(best)

    def signatories(self):
        sig = self._signature_block()
        dirs = sig["directors"]
        board = self.d.get("directors") if hasattr(self.d, "get") else ""
        chair_din = None
        m = re.search(r"([A-Z][A-Za-z. ]{3,60}?)\s*\(\s*(chairman[^)]*|managing director|director)\s*\)\s*\n?\s*" + DIN_RX, board or "", re.I)
        if m:
            chair_din = m.group(3)
        for i, d in enumerate(dirs, 1):
            fn, mn, ln = _split_name(d["name"])
            desig = "CHAIRMAN" if d["din"] == chair_din and re.search(r"chairman", m.group(2) if m else "", re.I) else d["designation"].upper()
            vals = {"FirstNameOfDirector": {"CY": fn}, "MiddleNameOfDirector": {"CY": mn}, "LastNameOfDirector": {"CY": ln},
                    "DesignationOfDirector": {"CY": desig}, "DirectorIdentificationNumberOfDirector": {"CY": d["din"]},
                    "DateOfSigningOfFinancialStatementsByDirector": {"CY": sig["date"]}}
            self.put_row("DetailsOfDirectorsSigningFinancialStatementsTable", {"DirectorsSigningFinancialStatementsAxis": f"Column {i}"}, vals,
                         "signatures on the balance sheet")
        # board report: the director(s) signing the directors' report
        if board:
            date = ""
            mm = re.search(r"date\s*[:\-]?\s*(\d{1,2}[-./]\d{1,2}[-./]\d{4})", board[-3000:], re.I)
            if mm:
                date = _iso(mm.group(1))
            signers = []
            for mm in re.finditer(r"([A-Z][A-Za-z.]*(?:\s*[A-Z][A-Za-z.]*){1,4})\s*\(\s*(chairman[^)]*|managing director|director|whole[- ]time director)\s*\)\s*\n?\s*" + DIN_RX, board[-4000:], re.I):
                signers.append((mm.group(1), mm.group(2), mm.group(3)))
            if not signers and m:
                signers.append((m.group(1), m.group(2), m.group(3)))
            for i, (name, desig, din) in enumerate(signers, 1):
                full = next((d["name"] for d in dirs if d["din"] == din), None) or self._full_name(name)
                fn, mn, ln = _split_name(full)
                vals = {"FirstNameOfDirector": {"CY": fn}, "MiddleNameOfDirector": {"CY": mn}, "LastNameOfDirector": {"CY": ln},
                        "DesignationOfDirector": {"CY": "CHAIRMAN" if "chairman" in desig.lower() else desig.upper()},
                        "DirectorIdentificationNumberOfDirector": {"CY": din}, "DateOfSigningBoardReport": {"CY": date or sig["date"]}}
                self.put_row("DetailsOfDirectorsSigningBoardReportTable", {"DirectorsSigningBoardReportAxis": f"Column {i}"}, vals,
                             "signature on the directors' report")
        if dirs:
            self.notes.append("Directors signing the accounts: " + ", ".join(f"{d['name']} (DIN {d['din']})" for d in dirs) + ".")

    def auditors(self):
        sig = self._signature_block()
        rep = self.d.get("auditors") if hasattr(self.d, "get") else ""
        firm, frn, partner, mno = sig["firm"], sig["frn"], sig["partner"], sig["mno"]
        m = re.search(r"for\s+(?:m/s\.?\s*)?([A-Z][A-Za-z&.,' ]+?)\s*\n?\s*chartered accountants", rep or "", re.I)
        if m and not firm:
            firm = m.group(1).strip()
        if not frn:
            m = re.search(r"firm\s*reg(?:istration)?\.?\s*no\.?\s*[:\-]*\s*([0-9]{6}[A-Z]?)", rep or "", re.I)
            frn = m.group(1) if m else ""
        if not mno:
            m = re.search(r"m(?:embership)?\.?\s*no\.?\s*[:\-]*\s*(\d{5,6})", rep or "", re.I)
            mno = m.group(1) if m else ""
        if not partner:
            m = re.search(r"\(\s*([A-Z][A-Za-z .]+?)\s*\)\s*\n?\s*(partner|proprietor)", rep or "", re.I)
            partner = m.group(1) if m else ""
        if not (firm or partner):
            return
        date = sig["date"]
        m = re.findall(r"dated?\s*[:\-]?\s*(\d{1,2}[-./]\d{1,2}[-./]\d{4})", rep or "", re.I)
        if m:
            date = _iso(m[-1]) or date
        dims = {"AuditorsAxis": "Column 1"}
        pan = ""
        mm = re.search(r"PAN[^A-Z0-9]{0,15}(" + PAN_RX[2:-2] + r")", rep or "")
        if mm:
            pan = mm.group(1)
        vals = {"CategoryOfAuditor": {"CY": "Auditors firm" if firm else "Individual"}, "NameOfAuditFirm": {"CY": firm.upper()},
                "NameOfAuditorSigningReport": {"CY": " ".join(reversed(partner.upper().split())) if False else partner.upper()},
                "FirmsRegistrationNumberOfAuditFirm": {"CY": frn}, "MembershipNumberOfAuditor": {"CY": mno},
                "DateOfSigningAuditReportByAuditors": {"CY": date}, "DateOfSigningOfBalanceSheetByAuditors": {"CY": date},
                "PermanentAccountNumberOfAuditorOrAuditorsFirm": {"CY": pan}}
        self.put_row("DetailsRegardingAuditorsTable", dims, vals, "auditors' report and balance sheet signature")
        self.prior_same_person("DetailsRegardingAuditorsTable", "NameOfAuditFirm",
                               {"PermanentAccountNumberOfAuditorOrAuditorsFirm", "SRNOfFormADT1", "AddressOfAuditors",
                                "FirmsRegistrationNumberOfAuditFirm"})
        self.ask("DetailsRegardingAuditorsTable", dims, "PermanentAccountNumberOfAuditorOrAuditorsFirm", "PAN of the audit firm")
        self.ask("DetailsRegardingAuditorsTable", dims, "SRNOfFormADT1", "SRN of form ADT-1 (auditor's appointment)")
        self.ask("DetailsRegardingAuditorsTable", dims, "AddressOfAuditors", "Address of the audit firm")
        self.notes.append(f"Auditor: {firm or partner} (FRN {frn or '?'}, partner {partner or '?'}, M.No {mno or '?'}).")

    # ------------------------------------------------------------ people: KMP and related parties
    def _rp_people(self) -> list[str]:
        """Names listed under key management personnel in the related party note."""
        if hasattr(self, "_people"):
            return self._people
        t = self.d.get("policies", "other") if hasattr(self.d, "get") else ""
        t = t or self.d.all
        out = []
        m = re.search(r"key management personnel[^\n]*\n(.*?)(companies|enterprises|entities|firms|transactions? with)", t, re.I | re.S)
        if m:
            for line in m.group(1).splitlines():
                s = line.strip()
                if re.fullmatch(r"[A-Z][A-Za-z. ]{3,50}", s) and not re.fullmatch(r"(directors?|relatives?.*|key management.*)", s, re.I):
                    out.append(_clean_name(s))
        self._people = out
        return out

    def _rp_entities(self) -> list[str]:
        t = self.d.get("policies", "other") if hasattr(self.d, "get") else ""
        t = t or self.d.all
        out = []
        m = re.search(r"(companies|enterprises|entities)[^\n]*(control|influence)[^\n]*\n(.*?)(transactions? with|nature of transaction)", t, re.I | re.S)
        if m:
            for line in m.group(3).splitlines():
                s = line.strip()
                if re.search(r"(limited|ltd|llp|private|pvt|company|firm|trust|huf)\.?$", s, re.I) and len(s) < 90:
                    out.append(s)
        return out

    def _rp_sheet(self):
        """Per-party amounts from a 'related party' working sheet: [(heading, name, amount)]."""
        out = []
        for fname, sheet, visible, rows in self.sheets:
            if not re.search(r"related\s*part", sheet, re.I):
                continue
            head = ""
            total_col = None
            for r in rows:
                cells = [_txt(v) for v in r]
                if any(re.fullmatch(r"total", c, re.I) for c in cells) and any(re.fullmatch(r"name", c, re.I) for c in cells):
                    total_col = next(i for i, c in enumerate(cells) if re.fullmatch(r"total", c, re.I))
                    continue
                texts = [c for c in cells if c and not _isnum(c)]
                nums = [(i, v) for i, v in enumerate(r) if _isnum(v)]
                if texts and not nums:
                    head = texts[-1]
                    continue
                if not texts or not nums or re.fullmatch(r"total", texts[-1], re.I):
                    continue
                name = next((c for c in texts if re.search(r"[A-Za-z]{3,}", c) and not re.fullmatch(r"\d+", c)), "")
                if not name or re.fullmatch(r"total", name, re.I):
                    continue
                amt = _num(r[total_col]) if total_col is not None and total_col < len(r) and _isnum(r[total_col]) else _num(nums[-1][1])
                out.append((head, name, int(amt)))
        return out

    def kmp(self):
        people = self._rp_people()
        sig = self._signature_block()
        names = list(dict.fromkeys(people + [d["name"] for d in sig["directors"]]))
        if not names:
            return
        sheet = self._rp_sheet()
        pay = {}
        for name, nature, rel, amt in self._rp_note():
            if re.search(r"remun|salary", nature, re.I):
                pay[_key(name)] = pay.get(_key(name), 0) + amt
        if not pay:
            for head, name, amt in sheet:
                if re.search(r"remun|salary", head, re.I):
                    pay[_key(name)] = amt
        holdings = {}
        rows, _ = self.note(r"share\s*capital")
        for cap, nums in rows:
            if nums and re.search(r"[A-Za-z]{3,}", cap) and not re.search(r"equity|share|total|balance|authori|issued", cap, re.I):
                holdings.setdefault(_key(cap), int(nums[0]))
        dins = {_key(d["name"]): d["din"] for d in sig["directors"]}
        for t in [self.d.all]:
            for m in re.finditer(r"([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]*){1,4})\s*(?:\([^)]*\))?\s*\n?\s*" + DIN_RX, t):
                dins.setdefault(_key(m.group(1)), m.group(2))
        why = "related party note, directors' remuneration working and signatures"
        for i, name in enumerate(names, 1):
            nm = self._full_name(name)
            dims = {"KeyManagerialPersonnelsAndDirectorsAxis": f"Column {i}"}
            din = next((v for k, v in dins.items() if _same(k, nm)), "")
            pan = self.pan_of(nm)
            sal = next((v for k, v in pay.items() if _same(k, nm)), None)
            held = next((v for k, v in holdings.items() if _same(k, nm)), None)
            T = "DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable"
            vals = self.zeros(T, r"KeyManagerialPersonnelOrDirector(Remuneration)?$", pks=("CY",))
            vals.pop("CeilingAsPerActKeyManagerialPersonnelOrDirectorRemuneration", None)
            vals["SharesHeldByKeyManagerialPersonnelOrDirector"] = {"CY": 0}
            vals.update({"NameOfKeyManagerialPersonnelOrDirector": {"CY": nm.upper()}, "DesignationOfKeyManagerialPersonnelOrDirector": {"CY": "Director"},
                    "DirectorIdentificationNumberOfKeyManagerialPersonnelOrDirector": {"CY": din},
                    "PermanentAccountNumberOfKeyManagerialPersonnelOrDirector": {"CY": pan}})
            if held:
                vals["SharesHeldByKeyManagerialPersonnelOrDirector"] = {"CY": held}
            if sal:
                for e in ("SalaryKeyManagerialPersonnelOrDirector", "GrossSalaryToKeyManagerialPersonnelOrDirector", "KeyManagerialPersonnelOrDirectorRemuneration"):
                    vals[e] = {"CY": sal}
            self.put_row("DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable", dims, vals, why)
            T = "DisclosureOfKeyManagerialPersonnelsAndDirectorsAndRemunerationToKeyManagerialPersonnelsAndDirectorsTable"
            self.prior_same_person(T, "NameOfKeyManagerialPersonnelOrDirector",
                                   {"DirectorIdentificationNumberOfKeyManagerialPersonnelOrDirector", "PermanentAccountNumberOfKeyManagerialPersonnelOrDirector",
                                    "DateOfBirthOfKeyManagerialPersonnelOrDirector", "QualificationOfKeyManagerialPersonnelOrDirector"})
            self.ask(T, dims, "DirectorIdentificationNumberOfKeyManagerialPersonnelOrDirector", f"DIN of {nm.upper()}")
            self.ask(T, dims, "PermanentAccountNumberOfKeyManagerialPersonnelOrDirector", f"PAN of {nm.upper()}")
            self.ask(T, dims, "DateOfBirthOfKeyManagerialPersonnelOrDirector", f"Date of birth of {nm.upper()}")
            self.ask(T, dims, "QualificationOfKeyManagerialPersonnelOrDirector", f"Qualification of {nm.upper()} (e.g. Graduate)")
        self.notes.append(f"Directors / KMP table: {len(names)} people from the related party note.")

    RP_KINDS = [
        (r"remun|salary", "RemunerationForKeyManagerialPersonnel", "Directors remuneration"),
        (r"outstanding.*(payable|creditor)|payable", "AmountsPayableRelatedPartyTransactions", "Outstanding payable"),
        (r"outstanding.*(loan|advance|receivable)|receivable", "AmountsReceivableRelatedPartyTransactions", "Outstanding receivable"),
        (r"received back|repaid|refund", "AdvancesTakenDuringYearRelatedPartyTransactions", "Loan / advance received back"),
        (r"loan given|advance given|loan/ ?advance given", "AdvancesGivenDuringYearRelatedPartyTransactions", "Loan / advance given"),
        (r"loan taken|advance taken|loan received|borrow", "AdvancesTakenDuringYearRelatedPartyTransactions", "Loan / advance taken"),
        (r"interest paid", "InterestPaidDuringYearRelatedPartyTransactions", "Interest paid"),
        (r"interest received", "InterestReceivedDuringYearRelatedPartyTransactions", "Interest received"),
        (r"income|receipt|sale|rendered", "OtherRelatedPartyTransactionsIncome", "Income"),
        (r"\brent\b|lease|operational|freight|service|expense|uniform|welfare|purchase", "OtherRelatedPartyTransactionsExpense", "Expenses"),
    ]
    RP_STOCK = ("AmountsPayableRelatedPartyTransactions", "AmountsReceivableRelatedPartyTransactions")
    COMPANY_RX = r"(limited|ltd|llp|private|pvt|company|management|logistics|garments?|lines|industries|enterprises|trust)\b"

    def _rp_note(self):
        """The related party note's transaction table: [(name, nature, relationship, amount in rupees)]."""
        out = []
        for fname, sheet, visible, rows in self.sheets:
            if not visible:
                continue
            for i, r in enumerate(rows):
                cells = [_txt(v).lower() for v in r]
                if not (any(re.search(r"name of (the )?related part", c) for c in cells) and any(re.search(r"nature", c) for c in cells)):
                    continue
                ni = next(j for j, c in enumerate(cells) if re.search(r"name of", c))
                ti = next((j for j, c in enumerate(cells) if re.search(r"nature of (contract|transaction)", c)), None)
                ri = next((j for j, c in enumerate(cells) if re.search(r"relation", c)), None)
                for r2 in rows[i + 1:]:
                    c2 = [_txt(v) for v in r2]
                    if any(re.match(r"^\s*note\b", c, re.I) for c in c2 if c):
                        break
                    nums = [v for v in r2 if _isnum(v)]
                    name = c2[ni] if ni < len(c2) else ""
                    if not name or not nums or re.fullmatch(r"total", name, re.I):
                        continue
                    nature = c2[ti] if ti is not None and ti < len(c2) else ""
                    rel = c2[ri] if ri is not None and ri < len(c2) else ""
                    out.append((_clean_name(name), nature, rel, self._rs(_num(nums[-1]), self.scale)))
                if out:
                    return out
        return out

    def related_parties(self):
        people = self._rp_people()
        entities = self._rp_entities()
        parties: dict[str, dict] = {}
        order = []

        def party(name):
            for k in order:
                if _same(k, name) or _key(k).startswith(_key(name)) or _key(name).startswith(_key(k)):
                    return parties[k]
            order.append(name)
            parties[name] = {"name": name, "vals": {}, "desc": [], "rel": ""}
            return parties[name]

        lines = [(n, t, rel, amt) for n, t, rel, amt in self._rp_note()]
        why = "related party note (transactions during the year)"
        if not lines:  # a working sheet with the payments, if the note has no table
            why = "related party transactions working and related party note"
            for head, name, amt in self._rp_sheet():
                if self.round_unit > 1:
                    amt = int((Decimal(amt) / self.round_unit).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * self.round_unit
                lines.append((_clean_name(name), head, "", amt))
        for name, nature, rel, amt in lines:
            p = party(name)
            el, desc = next(((e, d) for rx, e, d in self.RP_KINDS if re.search(rx, nature, re.I)),
                            ("OtherRelatedPartyTransactionsExpense", nature.strip().capitalize() or "Other transactions"))
            p["vals"][el] = p["vals"].get(el, 0) + amt
            label = nature.strip().capitalize() or desc
            if label not in p["desc"]:
                p["desc"].append(label)
            p["rel"] = p["rel"] or rel
        prev = {}
        for v in self.prior_rows("DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable").values():
            nm = next((per.get("CY") for e, per in v.items() if e.endswith("_NameOfRelatedParty")), None)
            if nm:
                prev[nm] = v
        if not parties and not prev:
            return
        T = "DisclosureOfRelationshipAndTransactionsBetweenRelatedPartiesTable"
        n = 0
        used_prev = set()
        for k in order:
            p = parties[k]
            n += 1
            company = re.search(self.COMPANY_RX, p["name"], re.I)
            is_person = not company and (re.search(r"kmp|key|director|relative", p["rel"], re.I) or any(_same(x, p["name"]) for x in people) or not p["rel"])
            nm = next((x for x in people + entities if _same(x, p["name"]) or _key(x).startswith(_key(p["name"]))), p["name"])
            full = self._full_name(nm) if is_person else nm
            dims = {"CategoriesOfRelatedPartiesAxis": f"Column {n}"}
            vals = {"NameOfRelatedParty": {"CY": full.upper()},
                    "CountryOfIncorporationOrResidenceOfRelatedParty": {"CY": "INDIA"},
                    "DescriptionOfNatureOfRelatedPartyRelationship": {"CY": "Key Management Personnel" if is_person else "Others"},
                    "DescriptionOfNatureOfTransactionsWithRelatedParty": {"CY": ", ".join(p["desc"])[:200]},
                    "AmountWrittenOffDuringPeriodInRespectOfDebtsDueFromRelatedParties": {"CY": 0},
                    "AmountWrittenBackDuringPeriodInRespectOfDebtsDueToRelatedParties": {"CY": 0}}
            for el, v in p["vals"].items():
                vals[el] = {"CY": v}
            pv = next((v for nm2, v in prev.items() if _same(nm2, full) or _key(nm2).startswith(_key(full)[:12])), None)
            if pv is not None:
                used_prev.add(id(pv))
                for eid, per in pv.items():
                    if per.get("CY") not in (None, ""):
                        vals.setdefault(eid.split("_", 1)[1], {}).setdefault("PY", per["CY"])
                        if eid.endswith(("_PermanentAccountNumberOfRelatedParty", "_CINOfRelatedParty")):
                            vals[eid.split("_", 1)[1]].setdefault("CY", per["CY"])
            pan = vals.get("PermanentAccountNumberOfRelatedParty", {}).get("CY") or (self.pan_of(full) if is_person else "")
            if pan:
                vals.setdefault("PermanentAccountNumberOfRelatedParty", {})["CY"] = pan
            self.put_row(T, dims, vals, why)
            if not pan and not vals.get("CINOfRelatedParty", {}).get("CY"):
                corp = re.search(r"(limited|ltd|pvt|private|llp)\b", full, re.I)
                self.ask(T, dims, "CINOfRelatedParty" if corp else "PermanentAccountNumberOfRelatedParty",
                         f"{'CIN' if corp else 'PAN'} of related party {full.upper()}"
                         + ("" if is_person or corp else " (a proprietorship or firm: give the PAN)"))
        # parties only in last year's filing: their previous-year transactions
        for nm2, pv in prev.items():
            if id(pv) in used_prev:
                continue
            n += 1
            vals = {eid.split("_", 1)[1]: {"PY": per["CY"]} for eid, per in pv.items() if per.get("CY") not in (None, "")}
            self.put_row(T, {"CategoriesOfRelatedPartiesAxis": f"Column {n}"}, vals, "last year's filing (previous-year related party transactions)")
        for pk in ("CY", "PY"):
            eid = G + "WhetherThereAreAnyRelatedPartyTransactionsDuringYear"
            if self.schema.element(eid) and self.f.get_value(eid, pk) in (None, ""):
                self.f.set_value(eid, pk, "true")
        self.notes.append(f"Related party table: {n} parties with their transactions for the year"
                          + (" and last year (from last year's filing)." if prev else "."))

    # ------------------------------------------------------------ CSR
    def csr_net_profits(self):
        rows_198, pbt, years, avg_row = {}, {}, [], None
        for fname, sheet, visible, rows in self.sheets:
            for r in rows:
                cells = [_txt(v) for v in r]
                text = " ".join(c for c in cells if c)
                m = re.search(r"profits?\s+calculated\s+u/s\s*198\s+for\s+the\s+fy\s*(\d{4})\s*-\s*(\d{2,4})", text, re.I)
                if m:
                    nums = [v for v in r if _isnum(v)]
                    if nums:
                        rows_198.setdefault(f"{m.group(1)}-{m.group(2)[-2:]}", int(_num(nums[-1]).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
                if re.search(r"average of (the )?(annual )?(net )?profits?", text, re.I) and avg_row is None:
                    nums = [v for v in r if _isnum(v)]
                    if nums:
                        avg_row = int(_num(nums[-1]).quantize(Decimal(1), rounding=ROUND_HALF_UP))
                if re.search(r"^\s*fy\s*\d{2}", cells[0] if cells else "", re.I) or (sum(bool(re.match(r"fy\s*\d{2}\s*-\s*\d{2}", c, re.I)) for c in cells) >= 2):
                    ys = [re.sub(r"fy\s*", "", c, flags=re.I) for c in cells if re.match(r"fy\s*\d{2}\s*-\s*\d{2}", c, re.I)]
                    if len(ys) >= 2:
                        years = ["20" + y.replace(" ", "") if len(y.split("-")[0].strip()) == 2 else y for y in ys]
                if re.search(r"profit before tax as per", text, re.I) and years:
                    nums = [int(_num(v).quantize(Decimal(1), rounding=ROUND_HALF_UP)) for v in r if _isnum(v)]
                    for y, v in zip(years, nums):
                        pbt[y.replace(" ", "")] = v
        if not rows_198:
            return
        ordered = sorted(rows_198, reverse=True)[:3]
        for i, y in enumerate(ordered, 1):
            vals = {"DescriptionOfFinancialYear": {"CY": y},
                    "NetProfitComputedUnderSection198AndAdjustedAsPerRule21FOfCompaniesCSRPolicyRules2014": {"CY": rows_198[y]}}
            p = next((v for k, v in pbt.items() if k.replace("20", "", 1)[:2] == y.replace("20", "", 1)[:2] or k[:4] == y[:4]), None)
            if p is not None:
                vals["ProfitBeforeTaxOfFinancialYear"] = {"CY": p}
            self.put_row("DisclosureOfNetProfitsForLastThreeFinancialYearsTable", {"NetProfitsForLastThreeFinancialYearsAxis": f"FinancialYearMember{i}"},
                         vals, "CSR working: net profit u/s 198 for the last three years")
        eid = C + "AverageNetProfitForLastThreeFinancialYears"
        if not self.schema.element(eid):
            eid = G + "AverageNetProfitForLastThreeFinancialYears"
        if self.schema.element(eid) and len(ordered) == 3:
            # MCA checks the average against the three years in the table and 2% of it against the prescribed amount
            avg = (Decimal(sum(rows_198[y] for y in ordered)) / 3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            self.f.set_value(eid, "CY", str(avg.normalize() if avg == avg.to_integral() else avg))
            self.src[(eid, "CY")] = "average of the net profits u/s 198 for the last three years (CSR working)"
            pid = eid.split("_", 1)[0] + "_PrescribedCSRExpenditure"
            if self.schema.element(pid):
                two = (avg * Decimal("0.02")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                old = self.f.get_value(pid, "CY")
                self.f.set_value(pid, "CY", str(two.normalize() if two == two.to_integral() else two))
                self.src[(pid, "CY")] = "2% of the average net profit (section 135(5))"
                if old not in (None, "") and Decimal(str(old)) != two:
                    self.notes.append(f"Prescribed CSR expenditure set to {two} (2% of the average net profit {avg}); the CSR note "
                                      f"says {old}, which stays as the amount to be spent.")
        self.notes.append("Net profits for the last three years (CSR) taken from the CSR working: " + ", ".join(ordered) + ".")

    def csr_spending(self):
        bodies = self.d.html.get("csr", []) + self.d.html.get("annexure", []) + self.d.html.get("directors", [])
        n = 0
        agencies: list[str] = []
        for body in bodies:
            for tbl in html_tables(body):
                if len(tbl) < 2:
                    continue
                head = " | ".join(" ".join(r) for r in tbl[:2]).lower()
                if not ("local area" in head and "project" in head) or "project duration" in head and False:
                    continue
                hdr = [c.lower() for c in tbl[0]]
                sub = [c.lower() for c in tbl[1]] if len(tbl) > 1 else []
                width = max(len(r) for r in tbl)

                def col(rx, row=hdr):
                    return next((i for i, c in enumerate(row) if re.search(rx, c)), None)
                ci = {"name": col(r"name of (the )?project"), "item": col(r"item from the list|schedule vii"), "local": col(r"local area"),
                      "state": col(r"^state$", sub) if sub else None, "district": col(r"^district$", sub) if sub else None,
                      "spent": col(r"amount spent"), "alloc": col(r"amount allocated"), "direct": col(r"direct"),
                      "agency": col(r"^name$", sub) if sub else None}
                for r in tbl[1:]:
                    if len(r) < width - 1 or not r or not re.match(r"^\s*\d+\.?\s*$", r[0] or ""):
                        continue
                    g = lambda k: (r[ci[k]] if ci.get(k) is not None and ci[k] < len(r) else "")  # noqa: E731
                    spent = _amt(g("spent") or "0", self.scale if self.scale > 1 and _num(g("spent")) < 100000 else 1)
                    if not spent:
                        continue
                    n += 1
                    sector = self._csr_sector(g("item") + " " + g("name"))
                    direct = g("direct").strip().lower().startswith("y")
                    vals = {"CSRProjectOrActivityIdentified": {"CY": g("name")[:200]},
                            "SectorInWhichProjectIsCovered": {"CY": sector},
                            "WhetherProjectsOrProgramsUndertakenInLocalAreaOrOther": {"CY": "YES" if g("local").lower().startswith("y") else "NO"},
                            "NameOfStateOrUnionTerritoryWhereProjectsOrProgramsWasUndertaken": {"CY": g("state").title()},
                            "NameOfDistrictWhereProjectsOrProgramsWasUndertaken": {"CY": g("district").title()},
                            "BudgetAmountOutlayProjectOrProgramWise": {"CY": _amt(g("alloc"), self.scale) if g("alloc") and _isnum(g("alloc")) else spent},
                            "DirectExpenditureOnProjectsOrPrograms": {"CY": spent}, "AmountSpentOnProjectsOrPrograms": {"CY": spent},
                            "OverheadsOnProjectsOrPrograms": {"CY": 0}, "ExpenditureOnAdministrativeOverheads": {"CY": 0},
                            "CumulativeExpenditureUptoReportingPeriod": {"CY": spent},
                            "ModeOfAmountSpent": {"CY": "Direct" if direct else "Other implementing agencies"}}
                    self.put_row("ClassificationOfCSRSpendingTable", {"ClassificationOfCSRSpendingAxis": f"Column {n}"}, vals,
                                 "CSR annual report: details of amount spent")
                    if not direct:
                        # the agency's name and CSR registration number follow the 'Direct' column
                        rest = [c.strip() for c in r[(ci["direct"] or 0) + 1:] if c and c.strip()]
                        reg = next((c for c in rest if re.fullmatch(r"CSR\d{6,}", c.replace(" ", ""), re.I)), "")
                        name = next((c for c in rest if re.search(r"[A-Za-z]{3,}", c) and c != reg), "")
                        if name or reg:
                            agencies.append(f"Name: {name}" + (f", CSR Reg. No: {reg}" if reg else ""))
            if n:
                break
        if n:
            tid = self.tid("ClassificationOfCSRSpendingTable")
            local = sum(int(r["values"].get(G + "AmountSpentOnProjectsOrPrograms", {}).get("CY", 0))
                        for r in self.f.tables.get(tid, [])
                        if r["values"].get(G + "WhetherProjectsOrProgramsUndertakenInLocalAreaOrOther", {}).get("CY") == "YES")
            for p in (C, G):
                eid = p + "AmountSpentInLocalArea"
                if self.schema.element(eid) and self.f.get_value(eid, "CY") in (None, "", "0"):
                    self.f.set_value(eid, "CY", str(local))
                    self.src[(eid, "CY")] = "CSR projects in the local area (CSR annual report)"
                    break
            self.notes.append(f"CSR spending table: {n} project(s) from the CSR annual report.")
            eid = G + "DetailsOfImplementingAgency"
            if agencies and self.schema.element(eid) and self.f.get_value(eid, "CY") in (None, ""):
                self.f.set_value(eid, "CY", "; ".join(dict.fromkeys(agencies))[:1000])
                self.src[(eid, "CY")] = "CSR annual report: implementing agency"

    @staticmethod
    def _csr_sector(t: str) -> str:
        t = t.lower()
        for rx, s in ((r"water", "Safe drinking water"), (r"health|medical|hospital", "Promoting health care including preventive health care"),
                      (r"sanitation|toilet", "Sanitation"), (r"educat|school|scholar", "Promoting education"),
                      (r"hunger|poverty|malnutrition|food", "Eradicating hunger, poverty and malnutrition"),
                      (r"women|gender|orphan|old age|senior citizen", "Promoting gender equality and empowering women"),
                      (r"environment|tree|plantation|forest|animal", "Ensuring environmental sustainability"),
                      (r"armed forces|veteran|war widow", "Measures for the benefit of armed forces veterans, war widows and their dependents"),
                      (r"sport", "Training to promote rural sports, nationally recognised sports, paralympic sports and olympic sports"),
                      (r"rural", "Rural development projects"), (r"slum", "Slum area development"),
                      (r"disaster|relief", "Disaster management, including relief, rehabilitation and reconstruction activities"),
                      (r"prime minister|pm cares|relief fund", "Contribution to the prime minister's national relief fund"),
                      (r"heritage|art|culture", "Protection of national heritage, art and culture"),
                      (r"livelihood|vocational|skill", "Employment enhancing vocational skills")):
            if re.search(rx, t):
                return s
        return "Others"

    # ------------------------------------------------------------ principal products / services
    def principal_products(self):
        rev = self.face("RevenueFromOperations") or ((self.face("RevenueFromSaleOfServices") or 0) + (self.face("RevenueFromSaleOfProducts") or 0))
        if not rev:
            return
        t = self.d.get("directors") if hasattr(self.d, "get") else ""
        t = (t or "") + "\n" + self.d.all
        m = re.search(r"engaged in (the )?business of ([^.]{5,200})", t, re.I)
        desc_src = m.group(2) if m else t[:5000]
        hit = None
        for rx, c4, cat, c8 in ITC_CODES:
            mm = re.search(rx, desc_src, re.I)
            if mm:
                hit = (c4, cat, c8, mm.group(0))
                break
        dims = {"TypesOfPrincipalProductOrServicesAxis": "Column 1"}
        self.principal_activity(hit, desc_src if m else "")
        if not hit:
            self.ask("DisclosureOfPrincipalProductOrServicesTable", dims, "ProductOrServiceCategoryITC4DigitCode",
                     "ITC / NPCS 4-digit code of the main product or service")
            self.ask("DisclosureOfPrincipalProductOrServicesTable", dims, "HighestTurnoverContributingProductOrServiceITC8DigitCode",
                     "ITC / NPCS 8-digit code of the main product or service")
            self.put_row("DisclosureOfPrincipalProductOrServicesTable", dims, {"TurnoverOfProductOrServiceCategory": {"CY": rev},
                         "TurnoverOfHighestContributingProductOrService": {"CY": rev}}, "revenue from operations")
            return
        c4, cat, c8, word = hit
        name = word.title() if len(word) > 3 else cat.title()
        vals = {"ProductOrServiceCategoryITC4DigitCode": {"CY": c4}, "DescriptionOfProductOrServiceCategory": {"CY": cat},
                "TurnoverOfProductOrServiceCategory": {"CY": rev}, "HighestTurnoverContributingProductOrServiceITC8DigitCode": {"CY": c8},
                "DescriptionOfProductOrService": {"CY": name}, "TurnoverOfHighestContributingProductOrService": {"CY": rev},
                "QuantityOfHighestContributingProductOrServiceInUom": {"CY": "1"}}
        self.put_row("DisclosureOfPrincipalProductOrServicesTable", dims, vals,
                     f"directors' report: business of {word} (service code {c4} / {c8})")
        self.notes.append(f"Principal service: {name}, code {c4} ({cat}) / {c8}, turnover = revenue from operations.")

    def principal_activity(self, hit, business: str):
        """MCA rule (C&I BR, 400400): name, description and NIC code of the main business are mandatory for years
        starting after 01-04-2018. The NIC code is the industry code inside the CIN (characters 2 to 6)."""
        T = "DetailsOfPrincipalBusinessActivitiesContributing10PercentOrMoreOfTotalTurnoverOfCompanyTable"
        dims = {"PrincipalBusinessActivitiesOfCompanyAxis": "ProductOrService1Member"}
        cin = (self.f.cin or "").strip().upper()
        vals = {"PercentageToTotalTurnoverOfCompany": {"CY": "1"}}  # MCA percentages are fractions (1 = 100%)
        if re.match(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$", cin):
            vals["NICCodeOfProductOrService"] = {"CY": cin[1:6]}
        if hit:
            c4, cat, c8, word = hit
            vals["NameOfMainProductOrService"] = {"CY": word.title() if len(word) > 3 else cat.title()}
            vals["DescriptionOfMainProductOrService"] = {"CY": (business.strip() or cat)[:200]}
        self.put_row(T, dims, vals, "main business from the directors' report; NIC code from the CIN")
        if not hit:
            self.ask(T, dims, "NameOfMainProductOrService", "Name of the main product or service")
            self.ask(T, dims, "DescriptionOfMainProductOrService", "Description of the main product or service")
        if "NICCodeOfProductOrService" not in vals:
            self.ask(T, dims, "NICCodeOfProductOrService", "NIC code of the main product or service")

    # ------------------------------------------------------------ CARO remarks
    CARO_MAP = {
        ("i", ""): "DisclosureInAuditorsReportRelatingToFixedAssets",
        ("i", "a"): "DisclosureRelatingToQuantitativeDetailsOfFixedAssets",
        ("i", "b"): "DisclosureRelatingToPhysicalVerificationAndMaterialDiscrepanciesOfFixedAssets",
        ("i", "c"): "DisclosureRelatingToTitleDeedsOfImmovableProperties",
        ("ii", ""): "DisclosureInAuditorsReportRelatingToInventories",
        ("iii", ""): "DisclosureInAuditorsReportRelatingToLoans",
        ("iii", "a"): "DisclosureAboutLoansGrantedToPartiesCoveredUnderSection189OfCompaniesAct",
        ("iii", "b"): "DisclosureRelatingToTermsAndConditionsOfLoansGranted",
        ("iii", "c"): "DisclosureRegardingReceiptOfLoansGranted",
        ("iii", "d"): "DisclosureRegardingTermsOfRecoveryOfLoansGranted",
        ("iv", ""): "DisclosureInAuditorsReportRelatingToComplianceWithSection185And186OfCompaniesAct2013",
        ("v", ""): "DisclosureInAuditorsReportRelatingToDepositsAccepted",
        ("vi", ""): "DisclosureInAuditorsReportRelatingToMaintenanceOfCostRecords",
        ("vii", ""): "DisclosureInAuditorsReportRelatingToStatutoryDuesTextBlock",
        ("vii", "a"): "DisclosureRelatingToRegularityInPaymentOfUndisputedStatutoryDuesTextBlock",
        ("vii", "b"): "DisclosureRelatingToDisputedStatutoryDuesTextBlock",
        ("ix", ""): "DisclosureInAuditorsReportRelatingToDefaultInRepaymentOfFinancialDues",
        ("x", "a"): "DisclosureInAuditorsReportRelatingToPublicOfferAndTermLoansUsedForPurposeForWhichThoseWereRaised",
        ("x", "b"): "DisclosureInAuditorsReportRelatingToPreferentialAllotmentOrPrivatePlacementOfSharesOrConvertibleDebentures",
        ("xi", ""): "DisclosureInAuditorsReportRelatingToFraudByTheCompanyOrOnTheCompanyByItsOfficersOrItsEmployeesReportedDuringPeriod",
        ("xii", ""): "DisclosureInAuditorsReportRelatingToNidhiCompany",
        ("xiii", ""): "DisclosureInAuditorsReportRelatingToTransactionsWithRelatedParties",
        ("xv", ""): "DisclosureInAuditorsReportRelatingToNonCashTransactionsWithDirectorsOrPersonsConnectedWithHim",
        ("xvi", ""): "DisclosureInAuditorsReportRelatingToRegistrationUnderSection45IAOfReserveBankOfIndiaAct1934",
    }
    NOT_APPLICABLE = (r"not applicable|is not a nidhi|not required to be registered|has not accepted|has not raised|"
                      r"has not made any preferential|has not made\s+any\s+preferential|does not (hold|have) any inventor|"
                      r"not covered|no (such )?cases?|has not entered into any non-cash")

    def caro(self):
        clauses = {}
        found = False
        for fname, sheet, visible, rows in self.sheets:
            if not visible:
                continue
            text = " ".join(_txt(v) for r in rows[:15] for v in r)
            if not re.search(r"auditor.s report\)\s*order|\bCARO\b|annexure\s*.?a.?\s*to the independent", text, re.I):
                continue
            found = True
            main = sub = ""
            for r in rows:
                cells = [_txt(v) for v in r if _txt(v)]
                if not cells:
                    continue
                m = re.match(r"^\(\s*([ivx]+)\s*\)$", cells[0])
                if m:
                    main, sub = m.group(1), ""
                    cells = cells[1:]
                if cells:
                    m = re.match(r"^\(?\s*([a-h])\s*\)\s*(.*)$", cells[0], re.S)
                    if m and main:
                        sub = m.group(1)
                        cells = ([m.group(2)] if m.group(2).strip() else []) + cells[1:]
                if not main or re.match(r"^for\s|chartered accountants|firm registration|partner|place|dated|udin|m\. ?no", " ".join(cells), re.I):
                    if main and re.match(r"^for\s", " ".join(cells), re.I):
                        main = ""
                    continue
                body = " ".join(cells)
                clauses.setdefault((main, ""), []).append((f"{sub}) " if sub else "") + body)
                if sub:
                    clauses.setdefault((main, sub), []).append(body)
            if clauses:
                break
        if not found or not clauses:
            return
        if ("iii", "d") not in clauses and ("iii", "e") in clauses:
            clauses[("iii", "d")] = clauses[("iii", "e")]
        if ("ix", "") in clauses and ("ix", "a") in clauses:
            clauses[("ix", "")] = clauses[("ix", "a")]
        rows = {"AuditorsFavourableRemarkMember": {}, "ClauseNotApplicableMember": {}}
        tid = self.tid("DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable")
        for key, local in self.CARO_MAP.items():
            if key not in clauses:
                continue
            txt = re.sub(r"\s+", " ", " ".join(clauses[key])).strip()
            if not txt:
                continue
            member = "ClauseNotApplicableMember" if re.search(self.NOT_APPLICABLE, txt, re.I) else "AuditorsFavourableRemarkMember"
            if local.endswith("TextBlock"):
                import html as _h
                txt = "<p>" + _h.escape(txt) + "</p>"
            rows[member][local] = {"CY": txt[:4000]}
        # MCA always wants the managerial remuneration remark (section 197(16)); CARO 2020 has no clause for it
        mr = "DisclosureInAuditorsReportRelatingToManagerialRemuneration"
        if not any(mr in v for v in rows.values()):
            said = ""
            for _f, _s, _r in self.all_rows():
                t = " ".join(_txt(v) for v in _r if _txt(v))
                if re.search(r"managerial remuneration|remuneration paid .{0,80}directors", t, re.I) and re.search(r"197", t):
                    said = re.sub(r"^\(?\s*[ivx]+\s*\)\s*", "", re.sub(r"\s+", " ", t)).strip()
                    break
            if not said:
                private = "PTC" in (self.f.cin or "").upper() or re.search(r"private limited", self.f.company or "", re.I)
                said = ("The company is a private company, so the provisions of section 197 of the Companies Act, 2013 on managerial "
                        "remuneration do not apply." if private else
                        "In our opinion the managerial remuneration has been paid or provided in accordance with section 197 of the Act.")
            rows["AuditorsFavourableRemarkMember"][mr] = {"CY": said[:4000]}
        for member, vals in rows.items():
            if vals:
                self.put_row("DisclosureOfAuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportTable",
                             {"AuditorsQualificationsReservationsOrAdverseRemarksInAuditorsReportAxis": member}, vals,
                             "CARO annexure to the auditors' report")
        if tid and self.f.tables.get(tid):
            n = sum(len(v) for v in rows.values())
            self.notes.append(f"CARO remarks table: {n} clauses from the auditors' report annexure "
                              f"({len(rows['ClauseNotApplicableMember'])} marked not applicable).")


NUMERIC = ("monetaryItemType", "sharesItemType", "perShareItemType", "decimalItemType")


def py_zero_counterparts(f: Filing, schema, src: dict | None = None) -> int:
    """MCA: an amount given for the current year needs the previous year too. A zero this year with nothing
    for last year is zero last year as well (dividends, amortisation, remittances ...)."""
    n = 0
    for eid, per in list(f.values.items()):
        el = schema.element(eid) or {}
        if el.get("section") is None or el.get("noPY") or el.get("type") not in NUMERIC:
            continue
        if per.get("PY") in (None, "") and per.get("CY") not in (None, ""):
            try:
                zero = Decimal(str(per["CY"])) == 0
            except Exception:  # noqa: BLE001
                continue
            if zero:
                f.set_value(eid, "PY", "0")
                if src is not None:
                    src[(eid, "PY")] = "zero this year and nothing for last year in the documents"
                n += 1
    return n


def apply_table_answers(f: Filing, schema, values: dict) -> int:
    """Put the page's answers for table facts (keys "__t~table~dims~element") into the tables."""
    n = 0
    for key, per in (values or {}).items():
        if not key.startswith("__t~"):
            continue
        try:
            _, tid, dims, eid = key.split("~", 3)
            dims = json.loads(dims)
        except ValueError:
            continue
        if tid not in schema.tables:
            continue
        v = (per or {}).get("CY")
        if v in (None, ""):
            continue
        row = f.table_row(tid, dims)
        row["values"].setdefault(eid, {})["CY"] = str(v)
        n += 1
    return n
