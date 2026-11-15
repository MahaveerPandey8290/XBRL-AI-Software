"""Command line use without the browser interface.

  python cli.py validate  filing.json|instance.xml|template.xls
  python cli.py generate  filing.json|instance.xml|template.xls  [-o out.xml] [--force]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xbrl_aoc4.generator import generate, output_filename  # noqa: E402
from xbrl_aoc4.importers.instance_reader import read_instance  # noqa: E402
from xbrl_aoc4.importers.template_reader import read_template  # noqa: E402
from xbrl_aoc4.model import Filing  # noqa: E402
from xbrl_aoc4.validator import validate  # noqa: E402


def load(path, taxonomy=None):
    p = Path(path)
    if p.suffix.lower() == ".json":
        return Filing.load(p)
    if p.suffix.lower() == ".xml":
        return read_instance(p, taxonomy)[0]
    return read_template(p, taxonomy)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["validate", "generate"])
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--taxonomy", choices=["AS", "INDAS"])
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    f = load(a.input, a.taxonomy)
    res = validate(f)
    for i in res.items:
        print(f"{i['level'].upper():8} {i['period'] or '':4} {i['message']}")
    print(f"{len(res.errors)} error(s), {len(res.warnings)} warning(s)")
    if a.command == "generate":
        if res.errors and not a.force:
            print("Not generated: fix the errors or pass --force.")
            sys.exit(1)
        xml, warns = generate(f)
        out = Path(a.output or output_filename(f))
        out.write_text(xml, encoding="utf-8", newline="")
        for w in warns:
            print("note:", w)
        print("written", out)
    sys.exit(1 if res.errors else 0)


if __name__ == "__main__":
    main()
