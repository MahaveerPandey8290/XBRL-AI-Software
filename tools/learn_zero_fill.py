"""Learn which amounts TaxPrint / InstantXBRL writes as 0 when they are blank.

Usage: python tools/learn_zero_fill.py AS sample1.xml [sample2.xml ...]
Writes xbrl_aoc4/schema/zerofill_<KEY>.json:
  {"values": {element: [period keys]},
   "tables": {table: {row signature: {element: [period keys]}}}}
A row signature is the row's members with typed values shown as "*"
(see xbrl_aoc4.autofill.row_signature).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xbrl_aoc4.autofill import row_signature  # noqa: E402
from xbrl_aoc4.generator import is_numeric  # noqa: E402
from xbrl_aoc4.importers.instance_reader import read_instance  # noqa: E402
from xbrl_aoc4.schema_loader import load_schema  # noqa: E402

key, files = sys.argv[1], sys.argv[2:]
schema = load_schema(key)
out = Path(__file__).resolve().parent.parent / "xbrl_aoc4" / "schema" / f"zerofill_{key}.json"
data = json.loads(out.read_text()) if out.exists() else {"values": {}, "tables": {}}


def add(d, eid, pk):
    if pk not in d.setdefault(eid, []):
        d[eid].append(pk)
        d[eid].sort()


for fp in files:
    f, _ = read_instance(fp, key)
    for eid, per in f.values.items():
        for pk, v in per.items():
            if v == "0" and is_numeric(schema.element(eid) or {}):
                add(data["values"], eid, pk)
    for tid, rows in f.tables.items():
        if tid not in schema.tables:
            continue
        for row in rows:
            # the row itself, and the same row for any asset class (classes differ by company)
            sigs = {row_signature(schema, tid, row["dims"]), row_signature(schema, tid, row["dims"], wildcard_classes=True)}
            for eid, per in row["values"].items():
                for pk, v in per.items():
                    if v == "0" and is_numeric(schema.element(eid) or {}):
                        for sig in sigs:
                            add(data["tables"].setdefault(tid, {}).setdefault(sig, {}), eid, pk)
out.write_text(json.dumps(data, indent=0, sort_keys=True))
n = sum(len(v) for v in data["values"].values()) + sum(
    len(p) for t in data["tables"].values() for r in t.values() for p in r.values())
print(n, "zero-filled amounts ->", out)
