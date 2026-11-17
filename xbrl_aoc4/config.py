"""Taxonomy settings for MCA AOC-4 XBRL filings.

The C&I (non-Ind AS, "AS") settings match the TaxPrint/InstantXBRL output
sample. The Ind AS entry point and namespaces follow the MCA Ind AS filing
manual and the role URIs found in the Ind AS template; they can be overridden
in ``settings.json`` next to the app (see ``load_settings``) and are confirmed
automatically when the taxonomy is downloaded (see ``taxonomy.py``).
"""
from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
USER_SETTINGS = APP_DIR.parent / "settings.json"

TAXONOMIES = {
    "AS": {
        "title": "Non-Ind AS (Companies Accounting Standards) - C&I Taxonomy 2016",
        "schemaRef": "https://www.mca.gov.in/V3XBRL/2016/07/26/Taxonomy/CnI/in-ci-ent-2016-03-31.xsd",
        "namespaces": {
            "in-ci-ent": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-gaap/in-ci-ent",
            "in-gaap": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-gaap",
            "in-ca-types": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-ca-types",
            "in-ca": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-ca",
            "in-gaap-roles": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-gaap-roles",
            "in-ca-roles": "http://www.icai.org/xbrl/taxonomy/2016-03-31/in-ca-roles",
        },
        "verified": True,
    },
    "INDAS": {
        "title": "Ind AS (Indian Accounting Standards) Taxonomy 2017",
        "schemaRef": "https://www.mca.gov.in/V3XBRL/2017/07/16/Taxonomy/Ind/in-ci-ent-2017-03-31.xsd",
        "namespaces": {
            "ind-as": "http://www.icai.org/xbrl/taxonomy/2017-03-31/ind-as",
            "in-ca": "http://www.icai.org/xbrl/taxonomy/2017-03-31/in-ca",
            "in-ca-types": "http://www.icai.org/xbrl/taxonomy/2017-03-31/in-ca-types",
            "ind-as-roles": "http://www.icai.org/xbrl/taxonomy/2017-03-31/ind-as-roles",
            "in-ca-roles": "http://www.icai.org/xbrl/taxonomy/2017-03-31/in-ca-roles",
        },
        # Not yet checked against a real Ind AS instance from the MCA portal.
        "verified": False,
    },
    "COST": {
        "title": "Costing Taxonomy 2019 - Cost Audit Report (Form CRA-4)",
        "form": "CRA-4",
        # Entry point and namespace of the Costing Taxonomy 2019 are taken from the
        # official taxonomy package when it is loaded (python -m xbrl_aoc4.taxonomy COST
        # <package.zip>), or from settings.json. Until then these follow MCA's naming of
        # its other taxonomies and must be checked before filing.
        "schemaRef": "https://www.mca.gov.in/XBRL/2019/Taxonomy/Costing/in-cost-car-2019-03-31.xsd",
        "namespaces": {
            "in-cost": "http://www.icmai.in/xbrl/taxonomy/2019-03-31/in-cost",
        },
        "verified": False,
    },
}

STANDARD_NS = {
    "xbrli": "http://www.xbrl.org/2003/instance",
    "link": "http://www.xbrl.org/2003/linkbase",
    "num": "http://www.xbrl.org/dtr/type/numeric",
    "xbrldt": "http://xbrl.org/2005/xbrldt",
    "xbrldi": "http://xbrl.org/2006/xbrldi",
    "negated": "http://www.xbrl.org/2009/role/negated",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xlink": "http://www.w3.org/1999/xlink",
    "net": "http://www.xbrl.org/2009/role/net",
    "nonnum": "http://www.xbrl.org/dtr/type/non-numeric",
    "iso4217": "http://www.xbrl.org/2003/iso4217",
}

CIN_SCHEME = "http://www.mca.gov.in/CIN"


def load_settings() -> dict:
    """Apply overrides from settings.json (if present) to TAXONOMIES."""
    if USER_SETTINGS.exists():
        try:
            data = json.loads(USER_SETTINGS.read_text(encoding="utf-8"))
        except Exception:
            return {}
        for key, over in (data.get("taxonomies") or {}).items():
            if key in TAXONOMIES:
                ns = over.pop("namespaces", None)
                TAXONOMIES[key].update(over)
                if ns:
                    TAXONOMIES[key]["namespaces"].update(ns)
        return data
    return {}


def load_packages():
    """Apply what was read from an official taxonomy package (schema/package_<KEY>.json,
    written by ``python -m xbrl_aoc4.taxonomy KEY <package.zip>``): the real entry point
    and namespaces."""
    for key in TAXONOMIES:
        p = APP_DIR / "schema" / f"package_{key}.json"
        if not p.exists():
            continue
        try:
            pkg = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if pkg.get("schemaRef"):
            TAXONOMIES[key]["schemaRef"] = pkg["schemaRef"]
        if pkg.get("namespaces"):
            TAXONOMIES[key]["namespaces"] = dict(pkg["namespaces"])
        TAXONOMIES[key]["verified"] = True


load_packages()
SETTINGS = load_settings()
