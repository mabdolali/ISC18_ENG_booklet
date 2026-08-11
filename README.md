# Conference booklet from a giant zip of papers

Are you the organizer who accepted a mountain of papers — and somehow also the lonely hand who has to turn them into a printable booklet?

You are in the right place. I was in your shoes for the **18th Iranian Statistical Conference (ISC18)**. This repository is what got me from a giant zip of submissions to a continuous, page-numbered, TOC-sorted full-papers PDF — mostly alone. (Thank you, Cursor: without it, this would have been impossible.)

If your conference handed you something like `Papers.zip` with hundreds of nested `.zip` / `.rar` / `.tex` / `.pdf` dumps, and you need one coherent English booklet out the other side, start here.

---

## What this does

Given a bulk archive of accepted papers, the pipeline:

1. **Unpacks** nested zip/rar submissions into per-paper folders (`main/p.NNNN/`)
2. **Picks** the real LaTeX source (drops unused conference templates / samples)
3. **Builds metadata** (title, authors, abstract, keywords, MSC, sort keys) → `papers_metadata.json`
4. **Compiles** each paper with the conference style, continuous arabic page numbers, odd-page starts
5. **Sorts** the TOC by first author’s last name (with room for manual overrides)
6. **Merges** everything into `Full-English.pdf`, and records what could not be included in `excluded_papers.json`

You still have to fix the weird papers. The point is you are not unpacking and renumbering 150 archives by hand.

---

## Repository layout

| Path | Role |
|------|------|
| `build_booklet.py` | Core ingest: staging → `main/p.NNNN/` + metadata |
| `rebuild_all.py` | One-command: sync zip → ingest → sort keys → compile booklet |
| `Final Booklet ENG/` | **Production tree** used for the shipped ISC18 English booklet |
| `ISC18th_Full_English/` | Earlier / parallel build tree (same idea, older entry points) |
| `_staging_papers/` | Drop new `NNNN.zip` / `.rar` / `.pdf` / `.tex` here (gitignored) |
| `_extracted_raw/` | Raw extract cache (gitignored) |
| `Papers (2).zip` / `Papers (3).zip` | Bulk submission archives (too large for git; keep locally) |

Inside `Final Booklet ENG/` the important pieces are:

- `build_final_booklet.py` — end-to-end build for that folder
- `main/p.*/` — one cleaned source tree per paper
- `papers_metadata.json` / `.csv` — titles, authors, TOC sort keys
- `excluded_papers.json` — no-TeX / wrong template / failed compiles
- `ISC18-english.sty` + `Images/` — conference style and banner
- `Full-English.pdf` — the booklet

---

## Requirements

- **Python 3** with `pypdf`
- **MiKTeX** (or another `pdflatex` + `epstopdf`)
- **WinRAR** / UnRAR (many “`.zip`” submissions are actually RAR)
- A bulk archive of submissions (e.g. `Papers (3).zip`), optionally an author spreadsheet (`ExcelPaper.xlsx`) for English name overlays

Paths to `pdflatex` and WinRAR are currently hard-coded for a Windows/MiKTeX setup — adjust them at the top of the build scripts for your machine.

---

## Quick start (recommended mental model)

### A. Drop papers and rebuild

```text
1. Put the bulk zip next to the project (or under Final Booklet ENG/)
2. Or drop individual NNNN.zip / .rar / .pdf / .tex into _staging_papers/
3. Run:
```

```bash
python rebuild_all.py --sync-zip "Papers (2).zip"
```

Useful flags:

| Flag | Meaning |
|------|---------|
| `--skip-ingest` | Rebuild PDF from current `main/` + metadata only |
| `--ingest-only` | Unpack + metadata + sort keys; no PDF |
| `--sync-zip [PATH]` | Pull new top-level archives from a bulk zip into staging |
| `--refresh-toc-authors` | Re-extract TOC names from TeX (keeps `toc_manual: true`) |

### B. Production booklet (`Final Booklet ENG`)

When you want everything self-contained in one folder (sources, reports, final PDF):

```bash
cd "Final Booklet ENG"
python build_final_booklet.py
```

That path syncs from `Papers (3).zip`, overlays English authors from `ExcelPaper.xlsx` when present, keeps conference-template papers, compiles with continuous page counters, builds a clickable TOC, and writes exclusions.

---

## What you will still do by hand

Automation gets you ~90% there. The last mile is always human:

- **TOC names** — set `"toc_manual": true` and edit `toc_author` / `sort_key` in metadata when auto-parsing gets the surname wrong
- **Broken figures / EPS leaks / missing images** — there are small `_scan_*.py` / `_remesh_*.py` helpers under `Final Booklet ENG/` for the usual disasters
- **Wrong template / PDF-only** — listed in `excluded_papers.json`; chase authors or convert carefully
- **Header placeholders** — leftover “Short article title” / sample affiliation text in fancyhdr

Expect a few `_remesh_NNNN.py` one-offs. That is normal. The pipeline is there so remeshing five papers is the job, not remeshing one hundred and fifty.

---

## Outputs worth keeping

- `Full-English.pdf` — the booklet
- `papers_metadata.json` — source of truth for titles, authors, sort order
- `excluded_papers.json` / `.txt` — what did not make the cut, and why
- `full_build_report.json` / compile reports under `_build/` — what failed and where

---

## Adapting to another conference

The bones are generic; the skin is ISC18:

1. Swap `ISC18-english.sty` and the banner image
2. Retarget hard-coded paths (`ROOT`, `PDFLATEX`, WinRAR)
3. Adjust template-detection heuristics if your sample `.tex` looks different
4. Keep the same ingest → metadata → compile → TOC → merge loop

If you are that lonely organizer again: dump the zip, run the rebuild, fix the outliers, ship the PDF. You are not supposed to do this with only Explorer and tears.

---

*Built under fire for ISC18. Shared so the next person does not start from zero.*
