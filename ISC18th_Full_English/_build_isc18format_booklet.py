#!/usr/bin/env python3
"""
Build Full-English-isc18format.pdf from TeX for every paper.

Policy
------
1. TeX available + ISC18 conference format  → compile native (continuous page counter)
2. TeX available + NOT conference format   → wrap in ISC18 template, then compile
3. No TeX                                  → stamp author/submission PDF; MARK + report
4. TeX present but both compiles fail      → stamp PDF if any; MARK + report

Never reuse prior booklet PDFs / restamp. Never prefer author PDF when TeX compiles.
"""

from __future__ import annotations

import json
import re
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
OUT_PDF = BOOK / "Full-English-isc18format.pdf"
REPORT_JSON = BOOK / "full_isc18format_report.json"
MARKED_JSON = BOOK / "marked_papers_report.json"


def is_conference_format(tex: str) -> bool:
    """Stronger ISC18-template check (sty / banner / template markers)."""
    has_sty = bool(
        re.search(r"ISC18-english\.sty|\\input\{[^}]*ISC18", tex, re.I)
        or re.search(r"\\usepackage\{[^}]*ISC18", tex, re.I)
    )
    has_banner = bool(re.search(r"isc18E|Images/isc18", tex, re.I))
    has_fancy = "fancyhdr" in tex or r"\fancyhead" in tex
    has_conf_header = bool(
        re.search(r"Article authors and affiliations|Article title header", tex, re.I)
    )
    if has_sty and (has_banner or has_conf_header or has_fancy):
        return True
    if has_sty:
        return True
    if has_conf_header and has_banner:
        return True
    return False


def latex_escape_light(s: str) -> str:
    if not s:
        return ""
    return (
        s.replace("\\", r"\textbackslash{}")
        .replace("&", r"\&")
        .replace("%", r"\%")
        .replace("#", r"\#")
        .replace("_", r"\_")
    )


def pick_author_pdf(pid: str) -> Path | None:
    cands: list[Path] = []
    folder = MAIN / pid
    if folder.exists():
        cands.extend(folder.glob("_author_*.pdf"))
        for p in folder.glob("*.pdf"):
            low = p.name.lower()
            if (
                p.stat().st_size > 400_000
                and "booklet" not in low
                and not p.name.startswith("_")
            ):
                cands.append(p)
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
    found = b.find_submission_pdf(pid)
    if found:
        cands.append(found)
    if not cands:
        return None
    return max(cands, key=lambda p: p.stat().st_size)


def stamp_pdf_source(src: Path, out_raw: Path, start: int) -> int:
    work = BUILD / "pdf_stamp"
    work.mkdir(parents=True, exist_ok=True)
    fitted = work / f"{out_raw.stem}_fit.pdf"
    stamped = work / f"{out_raw.stem}_num.pdf"
    b.normalize_unnumbered(src, fitted, wipe_old_numbers=False)
    b.stamp_continuous(fitted, stamped, start=start)
    shutil.copy2(stamped, out_raw)
    return len(PdfReader(str(out_raw)).pages)


def meta_title_author(r: dict) -> tuple[str, str]:
    title = (r.get("title") or "").strip()
    title = re.sub(r"^\s*series\s+", "", title, flags=re.I)
    title = re.sub(r"\s*\[\d+pt\]\s*", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    toc = (r.get("toc_author") or "").strip().casefold()
    if title.casefold() in {toc, "s. nezamdoust"} or len(title) < 12:
        title = (r.get("header_title") or "").strip() or title
        if title.casefold() in {toc, "s. nezamdoust"} or len(title) < 12:
            title = ""
    author = (r.get("authors") or r.get("toc_author") or r.get("first_author") or "").strip()
    author = re.sub(r"^Dr\.\s+", "", author, flags=re.I)
    author = author.replace(";", ",").strip()
    if title and "\\" not in title:
        title = latex_escape_light(title)
    if author and "\\" not in author and "$" not in author:
        author = latex_escape_light(author)
    return title, author


def compile_one(r: dict, start: int) -> dict:
    """Compile one paper. Returns result dict with ok/method/pages/mark."""
    pid = r["paper_id"]
    tex = b.resolve_main_tex(pid)
    raw_out = RAW / f"{pid}.pdf"
    paper_out = PAPERS / f"{pid}.pdf"
    title, author = meta_title_author(r)
    base = {
        "paper_id": pid,
        "title": (r.get("title") or "")[:100],
        "toc_author": r.get("toc_author") or r.get("first_author") or "",
        "start": start,
    }

    # --- No TeX ---
    if tex is None:
        src = pick_author_pdf(pid)
        if not src:
            return {
                **base,
                "ok": False,
                "method": "no_tex_no_pdf",
                "pages": 0,
                "mark": "NO_TEX",
                "detail": "no LaTeX source and no submission PDF",
            }
        n = stamp_pdf_source(src, raw_out, start)
        shutil.copy2(raw_out, paper_out)
        return {
            **base,
            "ok": True,
            "method": "pdf_only_isc18stamp",
            "pages": n,
            "mark": "NO_TEX",
            "detail": f"no LaTeX; stamped {src.name}",
        }

    tex_text = b.read_text(tex)
    conf = is_conference_format(tex_text)
    base["conference_format"] = conf
    base["tex"] = str(tex)

    # --- Conference format → native ---
    if conf:
        ok, detail = b.compile_native(pid, tex, raw_out, start_page=start)
        if ok:
            n = b.normalize_unnumbered(raw_out, paper_out, wipe_old_numbers=False)
            return {
                **base,
                "ok": True,
                "method": "native",
                "pages": n,
                "mark": None,
                "detail": detail,
            }
        # Native failed: try wrap as recovery (still TeX-based)
        ok2, detail2 = b.compile_wrapper(
            pid, tex, raw_out, start_page=start,
            title_override=title, author_override=author,
        )
        if ok2:
            n = b.normalize_unnumbered(raw_out, paper_out, wipe_old_numbers=False)
            return {
                **base,
                "ok": True,
                "method": "isc18_wrap_after_native_fail",
                "pages": n,
                "mark": "NATIVE_FAIL_WRAPPED",
                "detail": f"native:{detail}; wrap:{detail2}",
            }
        src = pick_author_pdf(pid)
        if src:
            n = stamp_pdf_source(src, raw_out, start)
            shutil.copy2(raw_out, paper_out)
            return {
                **base,
                "ok": True,
                "method": "pdf_after_tex_fail",
                "pages": n,
                "mark": "TEX_COMPILE_FAILED",
                "detail": f"native:{detail}; wrap:{detail2}; used {src.name}",
            }
        return {
            **base,
            "ok": False,
            "method": "failed",
            "pages": 0,
            "mark": "TEX_COMPILE_FAILED",
            "detail": f"native:{detail}; wrap:{detail2}",
        }

    # --- Not conference format → wrap ---
    ok, detail = b.compile_wrapper(
        pid, tex, raw_out, start_page=start,
        title_override=title, author_override=author,
    )
    if ok:
        n = b.normalize_unnumbered(raw_out, paper_out, wipe_old_numbers=False)
        return {
            **base,
            "ok": True,
            "method": "isc18_wrap",
            "pages": n,
            "mark": "WRAPPED_TO_ISC18",
            "detail": detail,
        }
    # Wrap failed: try native anyway
    ok2, detail2 = b.compile_native(pid, tex, raw_out, start_page=start)
    if ok2:
        n = b.normalize_unnumbered(raw_out, paper_out, wipe_old_numbers=False)
        return {
            **base,
            "ok": True,
            "method": "native_after_wrap_fail",
            "pages": n,
            "mark": "WRAP_FAIL_NATIVE",
            "detail": f"wrap:{detail}; native:{detail2}",
        }
    src = pick_author_pdf(pid)
    if src:
        n = stamp_pdf_source(src, raw_out, start)
        shutil.copy2(raw_out, paper_out)
        return {
            **base,
            "ok": True,
            "method": "pdf_after_tex_fail",
            "pages": n,
            "mark": "TEX_COMPILE_FAILED",
            "detail": f"wrap:{detail}; native:{detail2}; used {src.name}",
        }
    return {
        **base,
        "ok": False,
        "method": "failed",
        "pages": 0,
        "mark": "TEX_COMPILE_FAILED",
        "detail": f"wrap:{detail}; native:{detail2}",
    }


def write_marked_report(results: dict, path: Path) -> dict:
    marked = [v for v in results.values() if v.get("mark")]
    by_mark: dict[str, list] = {}
    for v in marked:
        by_mark.setdefault(v["mark"], []).append(
            {
                "paper_id": v["paper_id"],
                "toc_author": v.get("toc_author"),
                "title": v.get("title"),
                "method": v.get("method"),
                "detail": v.get("detail"),
            }
        )
    no_tex = by_mark.get("NO_TEX", [])
    failed = by_mark.get("TEX_COMPILE_FAILED", [])
    wrapped = by_mark.get("WRAPPED_TO_ISC18", [])
    report = {
        "summary": {
            "marked_total": len(marked),
            "no_tex": len(no_tex),
            "tex_compile_failed": len(failed),
            "wrapped_to_isc18": len(wrapped),
            "other_marks": {
                k: len(vs)
                for k, vs in by_mark.items()
                if k not in {"NO_TEX", "TEX_COMPILE_FAILED", "WRAPPED_TO_ISC18"}
            },
        },
        "by_mark": by_mark,
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Human-readable sidecar
    lines = [
        "MARKED PAPERS REPORT",
        "====================",
        f"No TeX (PDF stamped only): {len(no_tex)}",
        f"TeX compile failed (PDF fallback): {len(failed)}",
        f"Non-ISC18 TeX wrapped into conference format: {len(wrapped)}",
        "",
    ]
    for mark, items in sorted(by_mark.items()):
        lines.append(f"--- {mark} ({len(items)}) ---")
        for x in items:
            lines.append(f"  {x['paper_id']}: {x.get('toc_author') or '?'}")
            if x.get("detail"):
                lines.append(f"      {x['detail']}")
        lines.append("")
    (path.with_suffix(".txt")).write_text("\n".join(lines), encoding="utf-8")
    return report


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
        f"Building from TeX ({len(papers)} papers; no PDF reuse/restamp)...",
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
        res = compile_one(r, start)
        results[pid] = res
        mark = res.get("mark") or ""
        mark_s = f" [{mark}]" if mark else ""
        if not res["ok"]:
            print(f"FAIL [{i+1}/{len(papers)}] {pid}{mark_s} {res.get('detail')}", flush=True)
            continue
        print(
            f"[{i+1}/{len(papers)}] {pid} start={start} pages={res['pages']} "
            f"{res['method']}{mark_s}",
            flush=True,
        )
        built.append((r, PAPERS / f"{pid}.pdf", res["pages"], start))
        cursor = start + res["pages"]

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
            print(f"WARNING {r['paper_id']}: compiled@{start} merge@{cursor}", flush=True)
        final_map[r["paper_id"]] = cursor
        merge_list.append(pdf)
        cursor += n
        if i < len(built) - 1 and cursor % 2 == 0:
            merge_list.append(blank)
            blanks += 1
            cursor += 1

    numbered = BUILD / "papers_numbered_isc18format.pdf"
    print(
        f"Merging {len(built)} papers + {blanks} blanks -> {OUT_PDF.name}...",
        flush=True,
    )
    b.merge_pdfs(merge_list, numbered)

    toc_pdf = BUILD / "toc_isc18format.pdf"
    toc_pages = b.build_toc([r for r, _, _, _ in built], final_map, toc_pdf)
    toc_parts = [toc_pdf]
    if toc_pages % 2 == 1:
        toc_parts.append(blank)
        toc_pages += 1

    b.merge_pdfs(toc_parts + [numbered], OUT_PDF)

    marked_report = write_marked_report(results, MARKED_JSON)

    report = {
        "output": str(OUT_PDF),
        "included": len(built),
        "failed_excluded": sum(1 for v in results.values() if not v.get("ok")),
        "blanks": blanks,
        "arabic_pages": cursor - 1,
        "toc_pages": toc_pages,
        "full_pages": len(PdfReader(str(OUT_PDF)).pages),
        "odd_starts_ok": all(final_map[r["paper_id"]] % 2 == 1 for r, _, _, _ in built),
        "methods": {
            m: sum(1 for v in results.values() if v.get("ok") and v.get("method") == m)
            for m in sorted({v.get("method") for v in results.values() if v.get("ok")})
        },
        "marked_summary": marked_report["summary"],
        "marked_report": str(MARKED_JSON),
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2), encoding="utf-8")
    (BUILD / "page_map_isc18format.json").write_text(
        json.dumps(final_map, indent=2), encoding="utf-8"
    )
    (BUILD / "compile_results_isc18format.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2), flush=True)
    print("\n" + (MARKED_JSON.with_suffix(".txt")).read_text(encoding="utf-8"), flush=True)
    return 0 if report["failed_excluded"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
