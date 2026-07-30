#!/usr/bin/env python3
"""Re-normalize existing raw TeX PDFs, odd-pad, stamp visible page numbers, write Full-English.pdf."""

from __future__ import annotations

import json
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pypdf import PdfReader

spec = importlib.util.spec_from_file_location(
    "b", r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex.py"
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

BOOK = b.BOOK
BUILD = b.BUILD
PAPERS = b.PAPERS
RAW = BUILD / "raw"


def main() -> int:
    records = json.loads((BOOK / "papers_metadata.json").read_text(encoding="utf-8"))
    papers = [r for r in records if r.get("status") in {"ok", "pdf_only"}]
    papers.sort(
        key=lambda r: (
            r.get("sort_key") or "zzz",
            (r.get("first_author") or "").casefold(),
            (r.get("title") or "").casefold(),
        )
    )

    raws = []
    for r in papers:
        pid = r["paper_id"]
        src = RAW / f"{pid}.pdf"
        if not src.exists():
            # fall back to previous papers pdf
            src = PAPERS / f"{pid}.pdf"
        if src.exists():
            raws.append((r, src))
        else:
            print("MISSING", pid)

    print(f"Renormalizing {len(raws)} papers...", flush=True)

    def one(item):
        r, src = item
        pid = r["paper_id"]
        out = PAPERS / f"{pid}.pdf"
        n = b.normalize_unnumbered(src, out)
        return pid, n

    sizes = {}
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = [ex.submit(one, item) for item in raws]
        for i, fut in enumerate(as_completed(futs), 1):
            pid, n = fut.result()
            sizes[pid] = n
            if i % 10 == 0 or i == len(futs):
                print(f"  [{i}/{len(futs)}] last={pid} pages={n}", flush=True)

    built = []
    for r, _ in raws:
        pid = r["paper_id"]
        pdf = PAPERS / f"{pid}.pdf"
        n = sizes.get(pid) or len(PdfReader(str(pdf)).pages)
        built.append((r, pdf, n))

    blank = b.blank_page_pdf(BUILD / "blank_page.pdf")
    print("blank ready", blank)

    page_map = {}
    merge_list = []
    cursor = 1
    blanks = 0
    for i, (r, pdf, n) in enumerate(built):
        if cursor % 2 == 0:
            merge_list.append(BUILD / "blank_page.pdf")
            blanks += 1
            cursor += 1
        page_map[r["paper_id"]] = cursor
        merge_list.append(pdf)
        cursor += n
        if i < len(built) - 1 and cursor % 2 == 0:
            merge_list.append(BUILD / "blank_page.pdf")
            blanks += 1
            cursor += 1

    unnumbered = BUILD / "papers_unnumbered.pdf"
    print(f"Merging {len(built)} papers + {blanks} blanks...", flush=True)
    b.merge_pdfs(merge_list, unnumbered)

    numbered = BUILD / "papers_numbered.pdf"
    print("Stamping page numbers...", flush=True)
    b.stamp_continuous(unnumbered, numbered, start=1)

    toc_pdf = BUILD / "toc.pdf"
    toc_pages = b.build_toc([r for r, _, _ in built], page_map, toc_pdf)
    toc_parts = [toc_pdf]
    if toc_pages % 2 == 1:
        toc_parts.append(BUILD / "blank_page.pdf")
        toc_pages += 1

    out = BOOK / "Full-English.pdf"
    b.merge_pdfs(toc_parts + [numbered], out)
    # also write a clearly-named copy
    out2 = BOOK / "Full-English-from-tex.pdf"
    try:
        import shutil
        shutil.copy2(out if out.exists() else BOOK / "Full-English-new.pdf", out2)
    except Exception:
        b.merge_pdfs(toc_parts + [numbered], out2)

    final = out if out.exists() else out2
    r0 = PdfReader(str(final))
    box = r0.pages[min(10, len(r0.pages) - 1)].mediabox
    report = {
        "included": len(built),
        "blanks": blanks,
        "arabic_pages": cursor - 1,
        "toc_pages": toc_pages,
        "full_pages": len(r0.pages),
        "content_page_size": [float(box.width), float(box.height)],
        "odd_starts_ok": all(page_map[r["paper_id"]] % 2 == 1 for r, _, _ in built),
        "output": str(final),
    }
    (BOOK / "full_build_from_tex_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (BUILD / "page_map.json").write_text(json.dumps(page_map, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
