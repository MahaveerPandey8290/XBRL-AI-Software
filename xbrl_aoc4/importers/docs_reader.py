"""Turn uploaded documents (Word, text, HTML, PDF) into text for text blocks
and text fields (directors' report, auditor's report, notes, policies)."""
from __future__ import annotations

import html
import re
from pathlib import Path


def docx_to_html(path) -> str:
    import docx
    d = docx.Document(str(path))
    out = []
    body = d.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            text = "".join(t.text or "" for t in child.iter() if t.tag.endswith("}t"))
            if text.strip():
                out.append(f"<p>{html.escape(text)}</p>")
        elif tag == "tbl":
            rows = []
            for tr in child.iter():
                if not tr.tag.endswith("}tr"):
                    continue
                cells = []
                for tc in tr:
                    if not tc.tag.endswith("}tc"):
                        continue
                    txt = " ".join("".join(t.text or "" for t in p.iter() if t.tag.endswith("}t"))
                                   for p in tc if p.tag.endswith("}p")).strip()
                    cells.append(f"<td>{html.escape(txt)}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            out.append("<table border=\"1\">" + "".join(rows) + "</table>")
    return "\n".join(out)


def pdf_to_text(path) -> str:
    try:
        import pdfplumber  # optional dependency
    except ImportError:
        return ""
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def document_html(path) -> str:
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".docx":
        return docx_to_html(p)
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
