"""Build docs/xbrl_aoc4.zip, the engine used by the browser version (docs/index.html).

The zip is deterministic (fixed timestamps, sorted entries), so it only changes
when the engine code changes. Run after editing anything under xbrl_aoc4/:

    python tools/build_web_zip.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "xbrl_aoc4"
OUT = ROOT / "docs" / "xbrl_aoc4.zip"


def build(out: Path = OUT) -> Path:
    files = sorted(p for p in SRC.rglob("*") if p.is_file()
                   and "__pycache__" not in p.parts and p.suffix != ".pyc"
                   and p.relative_to(SRC).parts[0] != "web")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            info = zipfile.ZipInfo(p.relative_to(ROOT).as_posix(), date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, p.read_bytes())
    return out


if __name__ == "__main__":
    out = build(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")
