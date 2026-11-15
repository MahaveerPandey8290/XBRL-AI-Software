"""In-memory filing model and JSON (de)serialisation.

A filing holds:

* ``meta``   - taxonomy key, CIN, reporting period dates, decimals.
* ``values`` - non-dimensional facts: ``{element_id: {period_key: value}}``
* ``tables`` - dimensional facts: ``{table_id: [ {"dims": {axis: member|typed}, "values": {...}} ]}``

Period keys:

* ``CY``   current year (duration) / end of current year (instant)
* ``PY``   previous year (duration) / end of previous year (instant)
* ``OPEN`` start of previous year (instant only; InstantXBRL's ``I_Opening``)
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

PERIOD_KEYS = ("CY", "PY", "OPEN")


def parse_date(v) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    v = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            from datetime import datetime
            return datetime.strptime(v, fmt).date()
        except ValueError:
            pass
    return None


@dataclass
class Filing:
    taxonomy: str = "AS"
    cin: str = ""
    fy_start: str = ""   # YYYY-MM-DD
    fy_end: str = ""
    py_start: str = ""
    py_end: str = ""
    decimals: str = "INF"
    nature: str = "Standalone"
    amount_unit: str = "Rupees"   # unit the amounts were typed in; scaled to INR on output
    company: str = ""
    values: dict = field(default_factory=dict)
    tables: dict = field(default_factory=dict)
    attachments: list = field(default_factory=list)

    # -------------------------------------------------------------- periods
    def ensure_periods(self):
        fs, fe = parse_date(self.fy_start), parse_date(self.fy_end)
        if fs and fe:
            if not self.py_end:
                self.py_end = (fs - timedelta(days=1)).isoformat()
            if not self.py_start:
                try:
                    self.py_start = fs.replace(year=fs.year - 1).isoformat()
                except ValueError:
                    self.py_start = (fs - timedelta(days=365)).isoformat()
            self.fy_start, self.fy_end = fs.isoformat(), fe.isoformat()

    @property
    def open_date(self) -> str:
        ps = parse_date(self.py_start)
        return (ps - timedelta(days=1)).isoformat() if ps else ""

    # -------------------------------------------------------------- values
    def set_value(self, eid: str, pk: str, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            self.values.get(eid, {}).pop(pk, None)
            if eid in self.values and not self.values[eid]:
                del self.values[eid]
            return
        self.values.setdefault(eid, {})[pk] = value

    def get_value(self, eid: str, pk: str):
        return self.values.get(eid, {}).get(pk)

    def table_row(self, tid: str, dims: dict, create=True) -> dict | None:
        rows = self.tables.setdefault(tid, []) if create else self.tables.get(tid, [])
        for row in rows:
            if row["dims"] == dims:
                return row
        if not create:
            return None
        row = {"dims": dict(dims), "values": {}}
        rows.append(row)
        return row

    # -------------------------------------------------------------- io
    def to_dict(self) -> dict:
        return {
            "taxonomy": self.taxonomy, "cin": self.cin,
            "fy_start": self.fy_start, "fy_end": self.fy_end,
            "py_start": self.py_start, "py_end": self.py_end,
            "decimals": self.decimals, "nature": self.nature,
            "amount_unit": self.amount_unit, "company": self.company,
            "values": self.values, "tables": self.tables,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Filing":
        f = cls()
        for k, v in d.items():
            if hasattr(f, k):
                setattr(f, k, v)
        return f

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Filing":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
