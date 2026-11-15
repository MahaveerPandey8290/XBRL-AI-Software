"""Start the AOC-4 XBRL generator in your browser:  python run.py"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xbrl_aoc4.web.app import run  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    run(port=a.port, open_browser=not a.no_browser)
