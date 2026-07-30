#!/usr/bin/env python3
"""Copy missing figures from _extracted_raw into main/p.* and rebuild affected papers."""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path
import importlib.util

from pypdf import PdfReader

MAIN = Path(r"i:\Booklet ENG\ISC18th_Full_English\main")
RAW = Path(r"i:\Booklet ENG\_extracted_raw")
STAGING = Path(r"i:\Booklet ENG\_staging_papers")
BUILD = Path(r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex")

spec = importlib.util.spec_from_file_location(
    "b", r"i:\Booklet ENG\ISC18th_Full_English\_build_from_tex.py"
)
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)

IMG_EXT = {".png", ".jpg", ".jpeg", ".eps", ".pdf", ".PNG", ".JPG", ".JPEG", ".EPS", ".PDF"}


def sync_raw_assets(paper_id: str) -> int:
    num = paper_id.split(".", 1)[1]
    src_root = RAW / num
    dst = MAIN / paper_id
    if not src_root.exists() or not dst.exists():
        return 0
    n = 0
    for p in src_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in IMG_EXT:
            continue
        low = p.name.lower()
        if "eps-converted-to" in low and p.suffix.lower() == ".pdf":
            # still useful
            pass
        if low in {"isc18e.jpg", "logo.jpg"}:
            continue
        # Prefer Images/ if source under Images, else flatten
        rel = p.relative_to(src_root)
        parts = [x for x in rel.parts if x.lower() not in {"__macosx"}]
        if not parts:
            continue
        if any(part.lower() == "images" for part in parts[:-1]):
            dest = dst / "Images" / parts[-1]
        else:
            dest = dst / parts[-1]
            # also copy into Images for papers that expect Images/
            alt = dst / "Images" / parts[-1]
            alt.parent.mkdir(parents=True, exist_ok=True)
            if not alt.exists() or alt.stat().st_size < p.stat().st_size:
                shutil.copy2(p, alt)
                n += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size < p.stat().st_size:
            shutil.copy2(p, dest)
            n += 1
    return n


def fix_1086() -> None:
    """Use author conference.pdf; suppress date; strip page nums via normalize."""
    zpath = STAGING / "1086.zip"
    folder = MAIN / "p.1086"
    if zpath.exists():
        with zipfile.ZipFile(zpath) as zf:
            # extract converted pdfs for figures + author pdf
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                low = name.lower()
                if low.endswith((".eps", ".pdf", ".png")):
                    data = zf.read(info)
                    dest = folder / name
                    if (not dest.exists()) or dest.stat().st_size < len(data):
                        dest.write_bytes(data)
            if "conference.pdf" in [Path(i.filename).name for i in zf.infolist()]:
                data = zf.read(
                    next(i for i in zf.infolist() if Path(i.filename).name == "conference.pdf")
                )
                (folder / "_author_conference.pdf").write_bytes(data)

    # Prefer native compile after assets; if still small use author pdf
    tex = folder / "p.1086.tex"
    # remove date from tex if present
    t = b.read_text(tex)
    if r"\date{" not in t:
        t = t.replace(r"\begin{document}", r"\begin{document}" + "\n" + r"\date{}" + "\n", 1)
    else:
        t = re.sub(r"\\date\{.*?\}", r"\\date{}", t, count=1, flags=re.S)
    # uncomment the main risk figure block is already active; uncomment f1/f2/f3 if commented
    t2 = t.replace("%\\begin{figure}", "\\begin{figure}")
    t2 = t2.replace("%\\centerline{\\includegraphics", "\\centerline{\\includegraphics")
    t2 = t2.replace("%\\end{figure}", "\\end{figure}")
    # carefully only write if we didn't create chaos — keep original if too many begins
    if t2.count(r"\begin{figure}") <= t.count(r"\begin{figure}") + 3:
        t = t2
    tex.write_text(t, encoding="utf-8", newline="\n")

    raw = BUILD / "raw" / "p.1086.pdf"
    ok, detail = b.compile_native("p.1086", tex, raw)
    print("1086 native", ok, detail, "size", raw.stat().st_size if raw.exists() else None)
    author = folder / "_author_conference.pdf"
    if author.exists() and raw.exists() and author.stat().st_size > raw.stat().st_size * 0.9:
        # author pdf similar size; if native has more pages keep native
        pass
    if (not ok) or (raw.exists() and raw.stat().st_size < 200_000 and author.exists()):
        shutil.copy2(author, raw)
        print("1086 using author pdf")
    b.normalize_unnumbered(raw, BUILD / "papers" / "p.1086.pdf")


def main() -> None:
    meta = [
        r["paper_id"]
        for r in __import__("json").loads(
            Path(r"i:\Booklet ENG\ISC18th_Full_English\papers_metadata.json").read_text(
                encoding="utf-8"
            )
        )
        if r.get("status") in {"ok", "pdf_only"}
    ]
    # Focus on known broken + any with missing includegraphics files
    focus = set()
    for pid in meta:
        folder = MAIN / pid
        tex = folder / f"{pid}.tex"
        if not tex.exists():
            continue
        t = b.read_text(tex)
        incs = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", t)
        for inc in incs:
            if re.search(r"isc18e|logo", inc, re.I):
                continue
            inc = inc.strip().strip('"')
            cands = [folder / inc, folder / "Images" / Path(inc).name, folder / Path(inc).name]
            if not Path(inc).suffix:
                for ext in (".png", ".pdf", ".jpg", ".jpeg", ".eps", ".PNG"):
                    cands += [folder / f"{inc}{ext}", folder / "Images" / f"{Path(inc).name}{ext}"]
            if not any(c.exists() for c in cands):
                focus.add(pid)
                break

    focus |= {
        "p.1086", "p.1143", "p.1147", "p.1178", "p.1191", "p.1205", "p.1214",
        "p.1255", "p.1276", "p.1331", "p.1347", "p.1034", "p.1211",
    }
    print("focus", sorted(focus))

    for pid in sorted(focus):
        n = sync_raw_assets(pid)
        print(f"{pid}: synced {n} assets", flush=True)

    fix_1086()

    # recompile all focus papers
    for pid in sorted(focus):
        if pid == "p.1086":
            continue
        tex = b.resolve_main_tex(pid)
        raw = BUILD / "raw" / f"{pid}.pdf"
        if tex is None:
            continue
        ok, detail = b.compile_native(pid, tex, raw)
        # if author pdf in folder is richer, use it
        authors = list((MAIN / pid).glob("_author_*.pdf")) + [
            p
            for p in (MAIN / pid).glob("*.pdf")
            if p.stat().st_size > 200_000
            and "eps-converted" not in p.name.lower()
            and not p.name.lower().startswith(pid.lower())
            and "template" not in p.name.lower()
        ]
        # also from raw tree
        num = pid.split(".", 1)[1]
        raw_root = RAW / num
        if raw_root.exists():
            for p in raw_root.rglob("*.pdf"):
                if p.stat().st_size > 300_000 and "template" not in p.name.lower() and "eps-converted" not in p.name.lower():
                    authors.append(p)
        best = None
        if authors:
            best = max(authors, key=lambda p: p.stat().st_size)
        native_size = raw.stat().st_size if raw.exists() else 0
        if best and best.stat().st_size > max(native_size * 1.5, 400_000):
            shutil.copy2(best, raw)
            print(f"{pid}: author/raw pdf {best.name} ({best.stat().st_size})", flush=True)
        elif ok:
            print(f"{pid}: native {detail}", flush=True)
        else:
            print(f"{pid}: FAIL {detail}", flush=True)
            continue
        pages = b.normalize_unnumbered(raw, BUILD / "papers" / f"{pid}.pdf")
        print(f"{pid}: normalized pages={pages}", flush=True)


if __name__ == "__main__":
    main()
