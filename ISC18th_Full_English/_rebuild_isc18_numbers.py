#!/usr/bin/env python3
"""
Rebuild Full-English with TRUE ISC18 page-number style:
- Keep fancyhdr LO/RE + headrule from each paper's TeX
- Only change the counter so numbers are continuous across the booklet
- Do NOT globally tikz-stamp TeX papers (that moved numbers / dropped underline)
- PDF-only papers: wipe old digits then stamp LO/RE + headrule
"""

from __future__ import annotations

import json
import shutil
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
PAPERS = b.PAPERS
RAW = BUILD / "raw"
MAIN = b.MAIN

# Prefer author PDF only when TeX is known to lose figures
FORCE_AUTHOR_PDF = {
    "p.1354",
    "p.1143",
    "p.1255",
    "p.1147",
    "p.1205",
    "p.1052",
    "p.1129",
}


def pick_author_pdf(pid: str) -> Path | None:
    cands: list[Path] = []
    folder = MAIN / pid
    if folder.exists():
        cands.extend(folder.glob("_author_*.pdf"))
        cands.extend(
            p
            for p in folder.glob("*.pdf")
            if p.stat().st_size > 400_000
            and "booklet" not in p.name.lower()
            and not p.name.startswith("_booklet")
        )
    num = pid.split(".", 1)[1]
    raw_root = Path(r"i:\Booklet ENG\_extracted_raw") / num
    if raw_root.exists():
        for p in raw_root.rglob("*.pdf"):
            low = p.name.lower()
            if (
                p.stat().st_size > 400_000
                and "template" not in low
                and "eps-converted" not in low
            ):
                cands.append(p)
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_size)


def stamp_pdf_source(src: Path, out_raw: Path, start: int) -> int:
    """Fit to booklet size, then overlay continuous LO/RE numbers (keeps original headrule)."""
    work = BUILD / "pdf_stamp"
    work.mkdir(parents=True, exist_ok=True)
    fitted = work / f"{out_raw.stem}_fit.pdf"
    stamped = work / f"{out_raw.stem}_num.pdf"
    b.normalize_unnumbered(src, fitted, wipe_old_numbers=False)
    b.stamp_continuous(fitted, stamped, start=start)
    shutil.copy2(stamped, out_raw)
    return len(PdfReader(str(out_raw)).pages)


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

    print(
        f"Recompiling {len(papers)} papers with continuous ISC18 counters "
        f"(fancyhdr + headrule)...",
        flush=True,
    )

    built = []
    results = {}
    cursor = 1

    for i, r in enumerate(papers):
        pid = r["paper_id"]
        if cursor % 2 == 0:
            cursor += 1
        start = cursor

        tex = b.resolve_main_tex(pid)
        raw_out = RAW / f"{pid}.pdf"
        paper_out = PAPERS / f"{pid}.pdf"
        ok = False
        method = ""
        detail = ""

        force_pdf = pid in FORCE_AUTHOR_PDF
        auth = pick_author_pdf(pid) if force_pdf else None

        if force_pdf and auth is not None:
            n = stamp_pdf_source(auth, raw_out, start)
            ok = True
            method = "author_pdf_isc18stamp"
            detail = auth.name
        elif tex is not None:
            ok, detail = b.compile_native(pid, tex, raw_out, start_page=start)
            method = "native"
            if not ok:
                ok, detail = b.compile_wrapper(pid, tex, raw_out, start_page=start)
                method = "wrap"

        if not ok:
            src = b.find_submission_pdf(pid)
            if src is None:
                auth2 = pick_author_pdf(pid)
                src = auth2
            if src:
                n = stamp_pdf_source(src, raw_out, start)
                ok = True
                method = "pdf_fallback_isc18stamp"
                detail = src.name

        if not ok:
            print(f"FAIL {pid}", flush=True)
            results[pid] = {"ok": False, "start": start}
            continue

        if method.endswith("isc18stamp"):
            n = len(PdfReader(str(raw_out)).pages)
            paper_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_out, paper_out)
        else:
            # TeX already has continuous LO/RE + headrule — fit size only, do not wipe
            n = b.normalize_unnumbered(raw_out, paper_out, wipe_old_numbers=False)

        print(
            f"[{i + 1}/{len(papers)}] {pid} start={start} pages={n} {method} {detail}",
            flush=True,
        )
        results[pid] = {
            "ok": True,
            "start": start,
            "pages": n,
            "method": method,
            "detail": detail,
        }
        built.append((r, paper_out, n, start))
        cursor = start + n

    blank = b.blank_page_pdf(BUILD / "blank_page.pdf")
    merge_list = []
    cursor = 1
    blanks = 0
    final_map = {}
    for i, (r, pdf, n, start) in enumerate(built):
        if cursor % 2 == 0:
            merge_list.append(blank)
            blanks += 1
            cursor += 1
        if start != cursor:
            print(
                f"WARNING {r['paper_id']}: compiled@{start} but merge@{cursor}",
                flush=True,
            )
        final_map[r["paper_id"]] = cursor
        merge_list.append(pdf)
        cursor += n
        if i < len(built) - 1 and cursor % 2 == 0:
            merge_list.append(blank)
            blanks += 1
            cursor += 1

    unnumbered = BUILD / "papers_unnumbered.pdf"
    print(
        f"Merging {len(built)} papers + {blanks} blanks (no global stamp)...",
        flush=True,
    )
    b.merge_pdfs(merge_list, unnumbered)
    shutil.copy2(unnumbered, BUILD / "papers_numbered.pdf")

    toc_pdf = BUILD / "toc.pdf"
    toc_pages = b.build_toc([r for r, _, _, _ in built], final_map, toc_pdf)
    toc_parts = [toc_pdf]
    if toc_pages % 2 == 1:
        toc_parts.append(blank)
        toc_pages += 1

    out = BOOK / "Full-English.pdf"
    b.merge_pdfs(toc_parts + [BUILD / "papers_numbered.pdf"], out)

    report = {
        "included": len(built),
        "blanks": blanks,
        "arabic_pages": cursor - 1,
        "toc_pages": toc_pages,
        "full_pages": len(PdfReader(str(out)).pages),
        "method": "isc18_fancyhdr_continuous_setcounter",
        "odd_starts_ok": all(
            final_map[r["paper_id"]] % 2 == 1 for r, _, _, _ in built
        ),
        "output": str(out),
    }
    (BOOK / "full_build_from_tex_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (BUILD / "page_map.json").write_text(
        json.dumps(final_map, indent=2), encoding="utf-8"
    )
    (BUILD / "compile_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
