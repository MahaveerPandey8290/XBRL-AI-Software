"""Filings are stored as folders under data/filings/<id>/ (filing.json, docs/, output/)."""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from .config import APP_DIR, SETTINGS
from .model import Filing

DATA_DIR = Path(SETTINGS.get("data_dir") or (APP_DIR.parent / "data"))
FILINGS = DATA_DIR / "filings"


def _dir(fid: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", fid):
        raise ValueError("bad id")
    return FILINGS / fid


def new_id(f: Filing) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", f.cin or "filing")[:21] or "filing"
    return f"{base}_{f.taxonomy}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:4]}"


def save(fid: str, f: Filing):
    d = _dir(fid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "filing.json.tmp"
    f.save(tmp)
    tmp.replace(d / "filing.json")


def load(fid: str) -> Filing:
    return Filing.load(_dir(fid) / "filing.json")


def create(f: Filing) -> str:
    fid = new_id(f)
    save(fid, f)
    return fid


def delete(fid: str):
    shutil.rmtree(_dir(fid), ignore_errors=True)


def docs_dir(fid: str) -> Path:
    d = _dir(fid) / "docs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def output_dir(fid: str) -> Path:
    d = Path(SETTINGS.get("output_dir") or (_dir(fid) / "output"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_filings() -> list[dict]:
    out = []
    if not FILINGS.exists():
        return out
    for d in sorted(FILINGS.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        fp = d / "filing.json"
        if not fp.exists():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        out.append({
            "id": d.name, "company": data.get("company") or "", "cin": data.get("cin", ""),
            "taxonomy": data.get("taxonomy"), "fy_end": data.get("fy_end", ""), "nature": data.get("nature", ""),
            "updated": datetime.fromtimestamp(fp.stat().st_mtime).strftime("%d-%m-%Y %H:%M"),
            "n_values": len(data.get("values", {})),
        })
    return out
