"""Semantic diff of two XBRL instances (facts by concept, period, dimensions, unit).

Usage: python tools/compare_instances.py expected.xml actual.xml
"""
import sys
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from xbrl_aoc4.importers.instance_reader import read_contexts  # noqa: E402


def facts(path):
    root = etree.parse(path).getroot()
    ctx = read_contexts(root)
    out = {}
    dups = 0
    for el in root:
        if not isinstance(el.tag, str) or el.get("contextRef") not in ctx:
            continue
        c = ctx[el.get("contextRef")]
        per = c.get("date") or (c["start"], c["end"])
        ns, local = el.tag[1:].split("}")
        key = (local, per, tuple(sorted(c["dims"].items())), el.get("unitRef"))
        val = (el.text or "").strip()
        if el.get("unitRef"):
            try:
                val = str(float(val))
            except ValueError:
                pass
        if key in out:
            dups += 1
        out[key] = val
    if dups:
        print(f"  {path}: {dups} duplicate fact(s)")
        out[("__duplicates__",)] = str(dups)
    return out


def compare(a, b, show=20):
    fa, fb = facts(a), facts(b)
    missing = [k for k in fa if k not in fb]
    extra = [k for k in fb if k not in fa]
    diff = [k for k in fa if k in fb and fa[k] != fb[k]]
    print(f"expected {len(fa)} facts, actual {len(fb)}; missing {len(missing)}, extra {len(extra)}, value diffs {len(diff)}")
    for k in missing[:show]:
        print("  MISSING", k, fa[k][:60])
    for k in extra[:show]:
        print("  EXTRA  ", k, fb[k][:60])
    for k in diff[:show]:
        print("  DIFF   ", k, repr(fa[k][:60]), "->", repr(fb[k][:60]))
    return not (missing or extra or diff)


if __name__ == "__main__":
    ok = compare(sys.argv[1], sys.argv[2])
    sys.exit(0 if ok else 1)
