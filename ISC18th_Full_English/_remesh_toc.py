#!/usr/bin/env python3
"""Rebuild TOC only (first-author names) and remesh Full-English.pdf."""

from __future__ import annotations

import json
import importlib.util
from pathlib import Path

from pypdf import PdfReader

spec = importlib.util.spec_from_file_location(
    "b", r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex.py"
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

BOOK = b.BOOK
BUILD = b.BUILD


def main() -> int:
    records = json.loads((BOOK / "papers_metadata.json").read_text(encoding="utf-8"))
    page_map = json.loads((BUILD / "page_map.json").read_text(encoding="utf-8"))
    papers = [r for r in records if r.get("status") in {"ok", "pdf_only"} and r["paper_id"] in page_map]
    papers.sort(
        key=lambda r: (
            r.get("sort_key") or "zzz",
            (r.get("first_author") or "").casefold(),
            (r.get("title") or "").casefold(),
        )
    )
    # Update TOC authors (keeps toc_manual / existing toc_author unless empty)
    import importlib.util

    fix_spec = importlib.util.spec_from_file_location(
        "fix_pages_authors", str(BOOK / "_fix_pages_authors.py")
    )
    fix = importlib.util.module_from_spec(fix_spec)
    fix_spec.loader.exec_module(fix)
    toc_spec = importlib.util.spec_from_file_location(
        "toc_authors", str(BOOK / "_toc_authors.py")
    )
    toc = importlib.util.module_from_spec(toc_spec)
    toc_spec.loader.exec_module(toc)

    for r in records:
        if r.get("status") not in {"ok", "pdf_only"}:
            continue
        # Refresh auto names that look corrupted (e.g. "... mohsen")
        existing = (r.get("toc_author") or "").strip()
        toks = existing.split()
        dirty = len(toks) >= 3 and (
            toks[-1].casefold() == toks[0].casefold()
            or toks[-1].casefold() == toks[-2].casefold()
            or (toks[-1].islower() and toks[-1].casefold() in {t.casefold() for t in toks[:-1]})
        )
        r["toc_author"] = toc.resolve(
            r["paper_id"],
            BOOK / "main",
            r,
            refresh=dirty or not existing,
        )
        if r["toc_author"]:
            r["first_author"] = r["toc_author"]
            if not (r.get("toc_manual") and r.get("sort_key")):
                key, _ = fix.first_author_lastname(r["toc_author"], "")
                r["sort_key"] = key
                r["first_author_lastname"] = key
    (BOOK / "papers_metadata.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    toc_pdf = BUILD / "toc.pdf"
    toc_pages = b.build_toc(papers, page_map, toc_pdf)
    blank = b.blank_page_pdf(BUILD / "blank_page.pdf")
    toc_parts = [toc_pdf]
    if toc_pages % 2 == 1:
        toc_parts.append(blank)
        toc_pages += 1

    numbered = BUILD / "papers_numbered.pdf"
    if not numbered.exists():
        raise SystemExit(f"Missing {numbered}; run full rebuild first")
    out = BOOK / "Full-English.pdf"
    b.merge_pdfs(toc_parts + [numbered], out)
    print(
        json.dumps(
            {
                "toc_pages": toc_pages,
                "full_pages": len(PdfReader(str(out)).pages),
                "sample": [
                    {
                        "id": r["paper_id"],
                        "toc_author": r.get("toc_author"),
                    }
                    for r in papers[:8]
                ],
                "output": str(out),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
