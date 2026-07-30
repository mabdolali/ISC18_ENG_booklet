#!/usr/bin/env python3
"""
One-command rebuild of the ISC18 English full-papers booklet.

Typical use
-----------
1. Drop new submissions into:
     i:\\Booklet ENG\\_staging_papers\\NNNN.zip   (or .rar / .pdf / .tex)
2. Run:
     python "i:\\Booklet ENG\\rebuild_all.py"

What it does
------------
1. (optional) Sync archives from Papers (2).zip into _staging_papers
2. Ingest staging → ISC18th_Full_English/main/p.NNNN/ + papers_metadata.json
3. Fill sort_key / first_author (TOC by first author's last name)
4. Compile every paper from TeX (wrap non-ISC18 into conference template)
5. Write Full-English-isc18format.pdf + marked_papers_report (no-TeX / failures)

Flags
-----
  --skip-ingest     Only rebuild PDF from current main/ + metadata
  --ingest-only     Stop after ingest + sort keys (no PDF rebuild)
  --sync-zip PATH   Copy new top-level archives from a bulk zip into staging
                    (default: Papers (2).zip if present and --sync-zip given
                     without a path, or pass an explicit path)
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(r"i:\Booklet ENG")
STAGING = ROOT / "_staging_papers"
BOOK = ROOT / "ISC18th_Full_English"
META_JSON = BOOK / "papers_metadata.json"
META_CSV = BOOK / "papers_metadata.csv"
DEFAULT_BULK_ZIP = ROOT / "Papers (2).zip"
BUILD_BOOKLET = ROOT / "build_booklet.py"
REBUILD_PDF = BOOK / "_build_isc18format_booklet.py"
FIX_AUTHORS = BOOK / "_fix_pages_authors.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sync_bulk_zip(bulk: Path) -> int:
    """Extract top-level submission archives from a bulk zip into staging."""
    if not bulk.exists():
        raise FileNotFoundError(bulk)
    STAGING.mkdir(parents=True, exist_ok=True)
    added = 0
    with zipfile.ZipFile(bulk, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.startswith(".") or name.lower() in {"thumbs.db", "desktop.ini"}:
                continue
            suf = Path(name).suffix.lower()
            if suf not in {".zip", ".rar", ".pdf", ".tex", ".latex"}:
                continue
            # Prefer numeric id names: 1234.zip
            dest = STAGING / name
            if dest.exists() and dest.stat().st_size == info.file_size:
                continue
            print(f"  staging <- {name}", flush=True)
            with zf.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            added += 1
    return added


def enrich_sort_keys(refresh_toc_authors: bool = False) -> int:
    """Ensure papers_metadata.json has sort_key / first_author / toc_author.

    Hand-edited ``toc_author`` values are kept unless ``refresh_toc_authors``
    is True (or the field is empty). Set ``toc_manual``: true to always keep.
    """
    if not META_JSON.exists():
        raise FileNotFoundError(META_JSON)
    fix = load_module(FIX_AUTHORS, "fix_pages_authors")
    toc = load_module(BOOK / "_toc_authors.py", "toc_authors")
    records = json.loads(META_JSON.read_text(encoding="utf-8"))
    main_dir = BOOK / "main"
    for r in records:
        authors = r.get("authors") or ""
        header = r.get("header_authors") or ""
        toc_author = toc.resolve(
            r.get("paper_id") or "",
            main_dir,
            r,
            refresh=refresh_toc_authors and not r.get("toc_manual"),
        )
        key, first = fix.first_author_lastname(
            toc_author or authors, header if not toc_author else ""
        )
        # If user set sort_key manually with toc_manual, keep it
        if not (r.get("toc_manual") and r.get("sort_key")):
            r["sort_key"] = key
            r["first_author_lastname"] = key
        else:
            r["first_author_lastname"] = r.get("sort_key") or key
        r["first_author"] = toc_author or first
        r["toc_author"] = toc_author or first
    META_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    fields = [
        "paper_id",
        "status",
        "sort_key",
        "first_author_lastname",
        "first_author",
        "toc_author",
        "toc_manual",
        "title",
        "authors",
        "abstract",
        "keywords",
        "msc",
        "header_title",
        "header_authors",
        "main_tex",
        "source_archive",
        "notes",
        "folder",
    ]
    with META_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in fields}
            notes = r.get("notes") or ""
            if isinstance(notes, list):
                row["notes"] = " | ".join(notes)
            w.writerow(row)
    ok = sum(1 for r in records if r.get("status") in {"ok", "pdf_only"})
    print(f"Sort keys updated for {len(records)} records ({ok} includable)", flush=True)
    return ok


def merge_manual_toc_fields(new_records: list[dict]) -> list[dict]:
    """Preserve hand-edited toc_author / toc_manual / sort_key across ingest."""
    if not META_JSON.exists():
        return new_records
    try:
        old = {
            r["paper_id"]: r
            for r in json.loads(META_JSON.read_text(encoding="utf-8"))
            if r.get("paper_id")
        }
    except Exception:
        return new_records
    for r in new_records:
        prev = old.get(r.get("paper_id") or "")
        if not prev:
            continue
        if prev.get("toc_manual"):
            r["toc_manual"] = True
            if prev.get("toc_author"):
                r["toc_author"] = prev["toc_author"]
            if prev.get("sort_key"):
                r["sort_key"] = prev["sort_key"]
            if prev.get("first_author"):
                r["first_author"] = prev["first_author"]
        elif prev.get("toc_author") and not r.get("toc_author"):
            r["toc_author"] = prev["toc_author"]
    return new_records


def run_ingest(refresh_toc_authors: bool = False) -> int:
    print("=== 1/3 Ingest staging → main/ + metadata ===", flush=True)
    if not STAGING.exists() or not any(STAGING.iterdir()):
        print(
            f"No submissions in {STAGING}\n"
            f"Add NNNN.zip (or .rar/.pdf/.tex) there, then re-run.",
            file=sys.stderr,
        )
        return 1
    # Snapshot manual TOC fields before ingest overwrites metadata
    prev_manual = {}
    if META_JSON.exists():
        try:
            for r in json.loads(META_JSON.read_text(encoding="utf-8")):
                if r.get("toc_manual") or r.get("toc_author"):
                    prev_manual[r["paper_id"]] = {
                        k: r.get(k)
                        for k in (
                            "toc_author",
                            "toc_manual",
                            "sort_key",
                            "first_author",
                            "first_author_lastname",
                        )
                    }
        except Exception:
            prev_manual = {}

    bb = load_module(BUILD_BOOKLET, "build_booklet")
    rc = bb.main()
    if rc != 0:
        return rc

    # Restore manual fields onto freshly written metadata
    if prev_manual and META_JSON.exists():
        records = json.loads(META_JSON.read_text(encoding="utf-8"))
        for r in records:
            prev = prev_manual.get(r.get("paper_id") or "")
            if not prev:
                continue
            if prev.get("toc_manual"):
                r["toc_manual"] = True
                for k, v in prev.items():
                    if v not in (None, ""):
                        r[k] = v
            elif prev.get("toc_author"):
                r["toc_author"] = prev["toc_author"]
        META_JSON.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print("=== 2/3 Fill TOC sort keys ===", flush=True)
    enrich_sort_keys(refresh_toc_authors=refresh_toc_authors)
    return 0


def run_pdf_rebuild() -> int:
    print("=== 3/3 Compile from TeX -> Full-English-isc18format.pdf ===", flush=True)
    reb = load_module(REBUILD_PDF, "build_isc18format_booklet")
    return int(reb.main())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ingest new papers and rebuild Full-English.pdf"
    )
    ap.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Skip staging ingest; rebuild PDF from current main/",
    )
    ap.add_argument(
        "--ingest-only",
        action="store_true",
        help="Only ingest + sort keys; do not rebuild PDF",
    )
    ap.add_argument(
        "--sync-zip",
        nargs="?",
        const=str(DEFAULT_BULK_ZIP),
        default=None,
        metavar="PATH",
        help=f"Copy new archives from a bulk zip into staging "
        f"(default path: {DEFAULT_BULK_ZIP.name})",
    )
    ap.add_argument(
        "--refresh-toc-authors",
        action="store_true",
        help="Re-extract toc_author from LaTeX (keeps entries with toc_manual=true)",
    )
    args = ap.parse_args(argv)

    if args.sync_zip:
        bulk = Path(args.sync_zip)
        print(f"=== Sync archives from {bulk} ===", flush=True)
        n = sync_bulk_zip(bulk)
        print(f"Added/updated {n} staging file(s)", flush=True)

    if not args.skip_ingest:
        rc = run_ingest(refresh_toc_authors=args.refresh_toc_authors)
        if rc != 0:
            return rc
    else:
        print("=== Skipping ingest; refreshing sort keys ===", flush=True)
        if META_JSON.exists():
            enrich_sort_keys(refresh_toc_authors=args.refresh_toc_authors)

    if args.ingest_only:
        print("Done (ingest-only).", flush=True)
        return 0

    rc = run_pdf_rebuild()
    if rc == 0:
        out = BOOK / "Full-English.pdf"
        print(f"\nDone. Booklet: {out}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
