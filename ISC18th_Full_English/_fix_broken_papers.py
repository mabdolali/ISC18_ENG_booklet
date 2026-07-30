#!/usr/bin/env python3
"""
Fix papers with broken images / bad wraps:
- Restore missing figure assets from staging zips
- Prefer author-compiled PDF when TeX compile is incomplete
- Recompile wrap-method papers natively after TeX restore
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
import importlib.util
from pathlib import Path

from pypdf import PdfReader

MAIN = Path(r"i:\Booklet ENG\ISC18th_Full_English\main")
STAGING = Path(r"i:\Booklet ENG\_staging_papers")
BUILD = Path(r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex")
RAW_OUT = BUILD / "raw"
PAPERS = BUILD / "papers"

spec = importlib.util.spec_from_file_location(
    "b", r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex.py"
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

# Papers previously wrapped or flagged for missing images
TARGETS = [
    "p.1086", "p.1046", "p.1083", "p.1094", "p.1096", "p.1097", "p.1211",
    "p.1268", "p.1350", "p.1034", "p.1143", "p.1147", "p.1178", "p.1191",
    "p.1205", "p.1214", "p.1255", "p.1276", "p.1331", "p.1347", "p.1050",
    "p.1243", "p.1301", "p.1354", "p.1052", "p.1129",
]


def extract_assets(paper_id: str) -> int:
    num = paper_id.split(".", 1)[1]
    zpath = STAGING / f"{num}.zip"
    folder = MAIN / paper_id
    if not zpath.exists() or not folder.exists():
        return 0
    n = 0
    with zipfile.ZipFile(zpath) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if "__MACOSX" in name or name.startswith("."):
                continue
            base = Path(name).name
            low = base.lower()
            # skip aux latex outputs except author manuscript pdf
            if low.endswith((".aux", ".log", ".synctex.gz", ".out", ".toc")):
                continue
            data = zf.read(info)
            # prefer placing next to tex (flat), also keep Images/
            dest = folder / base
            if low.endswith((".png", ".jpg", ".jpeg", ".eps", ".pdf", ".PNG", ".JPG")):
                # Avoid overwriting large good files with smaller? always refresh assets
                if (not dest.exists()) or dest.stat().st_size < len(data):
                    dest.write_bytes(data)
                    n += 1
            # also manuscript PDFs under special names
            if low.endswith(".pdf") and any(
                k in low for k in ("conference", "manuscript", "paper", "main", num, "revise", "final")
            ):
                if "template" in low:
                    continue
                mdest = folder / f"_author_{base}"
                mdest.write_bytes(data)
                n += 1
    return n


def pick_author_pdf(paper_id: str) -> Path | None:
    folder = MAIN / paper_id
    cands = []
    for p in folder.glob("_author_*.pdf"):
        cands.append(p)
    for p in folder.glob("*.pdf"):
        low = p.name.lower()
        if low.startswith(paper_id.lower()):
            continue
        if "eps-converted" in low or "template" in low:
            continue
        if p.stat().st_size < 80_000:
            continue
        cands.append(p)
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_size, reverse=True)
    return cands[0]


def main() -> None:
    results = {}
    for pid in TARGETS:
        print(f"=== {pid} ===", flush=True)
        n = extract_assets(pid)
        print(f"  extracted assets: {n}", flush=True)

        tex = b.resolve_main_tex(pid)
        author_pdf = pick_author_pdf(pid)
        out_raw = RAW_OUT / f"{pid}.pdf"

        # Compile native
        ok = False
        detail = ""
        method = ""
        if tex is not None:
            ok, detail = b.compile_native(pid, tex, out_raw)
            method = "native"
            print(f"  native: {ok} {detail}", flush=True)

        # If native PDF is tiny vs author PDF, prefer author PDF
        use_author = False
        if author_pdf and author_pdf.exists():
            auth_size = author_pdf.stat().st_size
            native_size = out_raw.stat().st_size if out_raw.exists() else 0
            auth_pages = len(PdfReader(str(author_pdf)).pages)
            native_pages = len(PdfReader(str(out_raw)).pages) if out_raw.exists() else 0
            # Prefer author PDF when substantially richer
            if (not ok) or (auth_size > native_size * 1.8 and auth_pages >= native_pages):
                use_author = True
            # Or when tex references many figures but native pdf is small
            if tex and out_raw.exists():
                t = b.read_text(tex)
                ninc = len(re.findall(r"\\includegraphics", t))
                if ninc >= 4 and native_size < 500_000 and auth_size > native_size:
                    use_author = True

        if use_author:
            shutil.copy2(author_pdf, out_raw)
            ok = True
            method = "author_pdf"
            detail = author_pdf.name
            print(f"  using author pdf: {author_pdf.name} ({author_pdf.stat().st_size})", flush=True)
        elif not ok and tex is not None:
            ok, detail = b.compile_wrapper(pid, tex, out_raw)
            method = "wrap_fallback"
            print(f"  wrap: {ok} {detail}", flush=True)

        if not ok:
            src = b.find_submission_pdf(pid)
            if src:
                shutil.copy2(src, out_raw)
                ok = True
                method = "pdf_fallback"
                detail = src.name

        if ok:
            pages = b.normalize_unnumbered(out_raw, PAPERS / f"{pid}.pdf")
            results[pid] = {"ok": True, "method": method, "detail": detail, "pages": pages}
            print(f"  DONE pages={pages} method={method}", flush=True)
        else:
            results[pid] = {"ok": False, "method": method, "detail": detail, "pages": 0}
            print("  FAILED", flush=True)

    (BUILD / "fix_targets_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
