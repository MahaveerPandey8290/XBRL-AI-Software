"""Learn element period types (instant/duration) from existing XBRL instances.

Usage: python tools/learn_periods.py AS sample1.xml [sample2.xml ...]
Adds to xbrl_aoc4/schema/periods_<KEY>.json (used before the heuristic).
"""
import json
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xbrl_aoc4.importers.instance_reader import read_contexts  # noqa: E402

key, files = sys.argv[1], sys.argv[2:]
out = Path(__file__).resolve().parent.parent / "xbrl_aoc4" / "schema" / f"periods_{key}.json"
data = json.loads(out.read_text()) if out.exists() else {}
for fp in files:
    root = etree.parse(fp).getroot()
    ctx = read_contexts(root)
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") not in ctx:
            continue
        ns, local = el.tag[1:].split("}")
        prefix = next((p for p, u in el.nsmap.items() if u == ns and p), None)
        if prefix:
            data[f"{prefix}_{local}"] = ctx[el.get("contextRef")]["kind"]
out.write_text(json.dumps(dict(sorted(data.items())), indent=0))
print(len(data), "period types ->", out)
