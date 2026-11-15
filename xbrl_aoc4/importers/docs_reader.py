"""Turn uploaded documents (Word, text, HTML, PDF) into text for text blocks
and text fields (directors' report, auditor's report, notes, policies)."""
from __future__ import annotations

import html
import re
from pathlib import Path


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_body(path):
    """The <w:body> element of a .docx (read with the standard library, no python-docx)."""
    import xml.etree.ElementTree as ET
    import zipfile
    with zipfile.ZipFile(str(path)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    return root.find(f"{W}body")


def _para_text(p) -> str:
    out = []
    for el in p.iter():
        if el.tag == f"{W}t":
            out.append(el.text or "")
        elif el.tag == f"{W}tab":
            out.append(" ")
        elif el.tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
    return "".join(out)


def _table_rows(tbl) -> list[list[str]]:
    rows = []
    for tr in tbl.iter(f"{W}tr"):
        cells = []
        for tc in tr.findall(f"{W}tc"):
            txt = " ".join(_para_text(p) for p in tc.iter(f"{W}p")).strip()
            span = tc.find(f"{W}tcPr/{W}gridSpan")
            n = int(span.get(f"{W}val", "1")) if span is not None else 1
            cells += [txt] + [""] * (n - 1)
        rows.append(cells)
    return rows


def docx_to_html(path) -> str:
    out = []
    for child in _docx_body(path):
        if child.tag == f"{W}p":
            text = _para_text(child)
            if text.strip():
                out.append(f"<p>{html.escape(text)}</p>")
        elif child.tag == f"{W}tbl":
            rows = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>"
                           for r in _table_rows(child))
            out.append(f'<table border="1">{rows}</table>')
    return "\n".join(out)


def docx_tables(path) -> list[tuple[str, list[list[str]]]]:
    """[(title, rows)] for each table; the title is the nearest text above the table."""
    out, last = [], ""
    for child in _docx_body(path):
        if child.tag == f"{W}p":
            t = _para_text(child).strip()
            if t:
                last = t
        elif child.tag == f"{W}tbl":
            out.append((last, _table_rows(child)))
    return out


def pdf_to_text(path) -> str:
    """Text of a PDF: pdfplumber if installed, else pypdf (pure Python, also works in the browser)."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            return "\f".join((p.extract_text() or "") for p in pdf.pages)
    except ImportError:
        pass
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    return "\f".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)


class Note(str):
    """A note reference ('3', '6(a)') kept as text so it isn't read as an amount."""


NOTE = r"\d{1,2}(?:\s*\(?[a-z]\))?"
AMOUNT = r"\(?-?[\d,]+(?:\.\d+)?\)?|-|–|—"


def pdf_pages_rows(path) -> list[tuple[str, list[list]]]:
    """[(page heading, rows)]: each text line split into a caption and trailing amounts,
    so statements in a PDF can be read like a worksheet."""
    pages = []
    text = pdf_to_text(path)
    for page in text.split("\f") if "\f" in text else [text]:
        rows = []
        lines = page.splitlines()
        has_note_col = any(re.search(r"\bnote\b", ln, re.I) for ln in lines[:8])
        for line in lines:
            # dates ("31st March, 2026", "31.03.2026") are part of the caption, not amounts
            line = re.sub(r"\b(\d{1,2}(st|nd|rd|th)?\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b",
                          lambda m: m.group(0).replace(" ", "\u2060"), line, flags=re.I)
            line = re.sub(r"\b\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b", lambda m: m.group(0) + "\u2060", line)
            toks = line.split()
            nums = []
            while toks and re.fullmatch(AMOUNT, toks[-1]):
                nums.insert(0, toks.pop())
            note = []
            if toks and re.fullmatch(r"\d{1,2}\([a-z]\)", toks[-1]):
                note = [Note(toks.pop())]
            elif len(nums) >= 2 and re.fullmatch(r"[1-9]\d?", nums[0]) and (
                    len(nums) >= 3 or has_note_col or not re.fullmatch(r"\d{1,2}", nums[1])):
                note = [Note(nums.pop(0))]
            rows.append([" ".join(toks).replace("\u2060", " ").strip()] + note + nums)
        heading = " ".join(r[0] for r in rows[:6] if r and r[0])
        pages.append((heading, rows))
    return pages


def classify_document(name: str, text: str = "") -> str | None:
    """Which report a document is: directors, auditors, csr, policies (or None)."""
    n = name.lower()
    head = text[:3000].lower()
    if re.search(r"director|board.?s? report|boards report", n) or "directors' report" in head[:400] or "board's report" in head[:400]:
        return "directors"
    if re.search(r"auditor|audit report|caro", n) or "independent auditor" in head[:600]:
        return "auditors"
    if re.search(r"\bcsr\b|corporate social", n):
        return "csr"
    if re.search(r"polic|notes? to (the )?(accounts|financial)|note-?1\b", n) or "significant accounting policies" in head:
        return "policies"
    if "corporate social responsibility" in head[:600]:
        return "csr"
    # annexures (AOC-2, MGT-9, secretarial audit ...) belong to the board's report
    if re.search(r"annexure|aoc\s*-?\s*2|mgt\s*-?\s*9", n + " " + head[:300]):
        return "directors"
    return None


TEXT_BLOCK_LABELS = {
    "directors": r"board of directors.? report explanatory",
    "auditors": r"auditor.?s report explanatory",
    "csr": r"corporate social responsibility explanatory",
    "policies": r"significant accounting polic|^disclosure of accounting policies, change in accounting policies|^disclosure of accounting policies explanatory",
}


def text_block_for(schema, kind: str) -> str | None:
    """Element id of the text block that holds this kind of report in the given taxonomy."""
    rx = TEXT_BLOCK_LABELS.get(kind)
    if not rx:
        return None
    for alt in rx.split("|"):  # alternatives in order of preference
        for sec in schema.sections:
            for it in sec["items"]:
                if it.get("type") == "textBlockItemType" and re.search(alt, it.get("label", ""), re.I):
                    return it["id"]
    return None


def document_html(path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return docx_to_html(p)
    if ext == ".doc":
        raise ValueError(f"{p.name}: old Word .doc files can't be read; open it in Word and save as .docx")
    if ext in (".htm", ".html"):
        return p.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        txt = pdf_to_text(p)
        return "".join(f"<p>{html.escape(x)}</p>" for x in txt.split("\n\n") if x.strip())
    txt = p.read_text(encoding="utf-8", errors="ignore")
    return "".join(f"<p>{html.escape(x)}</p>" for x in re.split(r"\n\s*\n", txt) if x.strip())


def html_to_text(h: str) -> str:
    h = re.sub(r"</(p|tr|div|h\d)>", "\n", h, flags=re.I)
    h = re.sub(r"<[^>]+>", " ", h)
    return re.sub(r"[ \t]+", " ", html.unescape(h)).strip()


def paragraphs(path) -> list[str]:
    h = document_html(path)
    return [html.unescape(re.sub(r"<[^>]+>", "", x)).strip() for x in re.findall(r"<p>(.*?)</p>", h, flags=re.S)]
