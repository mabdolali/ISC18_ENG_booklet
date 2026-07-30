#!/usr/bin/env python3
"""
Build ISC18 English booklet structure from Papers (2).zip submissions.

- Extracts nested zip/rar archives
- Keeps only the main LaTeX source (drops unused conference templates)
- Organizes as ISC18th_Full_English/main/p.<id>/
- Writes metadata JSON/CSV for abstract booklet generation
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(r"i:\Booklet ENG")
STAGING = ROOT / "_staging_papers"
EXTRACTED = ROOT / "_extracted_raw"
BOOKLET = ROOT / "ISC18th_Full_English"
MAIN = BOOKLET / "main"
WINRAR = Path(r"C:\Program Files\WinRAR\WinRAR.exe")
UNRAR = Path(r"C:\Program Files\WinRAR\UnRAR.exe")

SKIP_NAME_PARTS = ("__macosx", ".ds_store", "thumbs.db")
BUILD_EXTS = {
    ".aux", ".log", ".out", ".synctex", ".synctex.gz", ".toc", ".lof", ".lot",
    ".bbl", ".blg", ".fls", ".fdb_latexmk", ".ptd", ".bak", ".pdf",
}
KEEP_EXTS = {
    ".tex", ".latex", ".sty", ".bib", ".bst", ".cls",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".eps", ".pdf",
    ".svg", ".webp",
}

PLACEHOLDER_MARKERS = [
    r"short article title",
    r"short authors'? names",
    r"first author name",
    r"second author name",
    r"first author's affiliation",
    r"second author's affiliation",
    r"email@email\.ac\.ir",
    r"keyword 1,\s*keyword 2",
    r"99x99",
    r"\{\\large\s*\\bf\s*article title\}",
    r"how to write papers for the 18th iranian statistical conference",
    r"those interested in participating in this conference can easily format",
]

TEMPLATE_NAME_RE = re.compile(
    r"(isc\d*-?english-?template|english-?template|^template$|^sample$|^example$)",
    re.I,
)


@dataclass
class PaperRecord:
    paper_id: str
    status: str
    folder: str = ""
    main_tex: str = ""
    main_tex_original: str = ""
    discarded_tex: list[str] = field(default_factory=list)
    title: str = ""
    authors: str = ""
    abstract: str = ""
    keywords: str = ""
    msc: str = ""
    header_title: str = ""
    header_authors: str = ""
    has_sty: bool = False
    n_figures: int = 0
    notes: list[str] = field(default_factory=list)
    source_archive: str = ""


def is_junk(path: Path) -> bool:
    low = str(path).lower().replace("\\", "/")
    return any(p in low for p in SKIP_NAME_PARTS)


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    # Some .zip files are actually RAR; prefer WinRAR for robustness.
    if WINRAR.exists():
        cmd = [str(WINRAR), "x", "-y", "-ibck", "-o+", str(archive), str(dest) + os.sep]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0 and any(dest.rglob("*")):
            return
    if suffix == ".rar" and UNRAR.exists():
        cmd = [str(UNRAR), "x", "-y", "-o+", str(archive), str(dest) + os.sep]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 0:
            return
        raise RuntimeError(f"UnRAR failed for {archive.name}: {proc.stderr or proc.stdout}")
    if suffix == ".zip":
        try:
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(dest)
            return
        except zipfile.BadZipFile as e:
            if WINRAR.exists():
                raise RuntimeError(f"Could not extract {archive.name}") from e
            raise
    # Loose .tex/.pdf/.latex
    if suffix in {".tex", ".latex", ".pdf"}:
        shutil.copy2(archive, dest / archive.name)
        return
    raise RuntimeError(f"Unsupported archive type: {archive.name}")


def find_project_root(raw_dir: Path) -> Path:
    """Prefer the directory that actually contains the main tex sources."""
    tex_files = [
        p for p in raw_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".tex", ".latex"}
        and not is_junk(p)
        and p.suffix.lower() != ".bak"
        and ".bak" not in p.name.lower()
    ]
    if not tex_files:
        return raw_dir
    # Choose the shallowest common parent among tex files with max count.
    from collections import Counter
    parents = Counter(p.parent for p in tex_files)
    # Prefer parents with more tex files, then shallower path.
    best = sorted(parents.items(), key=lambda kv: (-kv[1], len(kv[0].parts)))[0][0]
    return best


def is_placeholder_template(text: str) -> bool:
    low = text.lower()
    hits = sum(1 for pat in PLACEHOLDER_MARKERS if re.search(pat, low))
    return hits >= 3


def score_tex(path: Path, text: str) -> float:
    name = path.stem.lower()
    score = 0.0
    size = len(text)
    score += min(size / 1000.0, 40.0)

    if re.search(r"\\begin\{document\}", text):
        score += 15
    if re.search(r"\\section\*?\s*\{?\s*Introduction", text, re.I):
        score += 10
    if re.search(r"(Abstract:|\\abstract\b)", text, re.I):
        score += 8
    if re.search(r"Keywords?:", text, re.I):
        score += 4
    if re.search(r"Mathematics Subject Classification", text, re.I):
        score += 3
    if re.search(r"\\Large\s*\\bf|\\title\{", text):
        score += 5

    # Version preference
    ver = re.search(r"(?:ver|version|v)[_\s-]*(\d+)", name, re.I)
    if ver:
        score += int(ver.group(1))
    rev = re.search(r"review\s*(\d+)|rev\s*(\d+)", name, re.I)
    if rev:
        score += 2 * int(next(g for g in rev.groups() if g))

    if TEMPLATE_NAME_RE.search(name):
        score -= 25
    if is_placeholder_template(text):
        score -= 80
    if name in {"response", "cover", "coverletter", "letter", "rebuttal"}:
        score -= 30
    if "algorithm" in name and size < 15000:
        score -= 5
    if path.suffix.lower() == ".latex":
        score -= 2  # converted Word; still usable if only option

    return score


def choose_main_tex(tex_files: list[Path]) -> tuple[Path | None, list[Path]]:
    if not tex_files:
        return None, []
    scored = []
    for p in tex_files:
        try:
            text = read_text(p)
        except Exception:
            continue
        scored.append((score_tex(p, text), p, text))
    if not scored:
        return None, tex_files
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best, best_text = scored[0]
    # If best is still a clear empty template and there is an alternative, pick next.
    if is_placeholder_template(best_text):
        for s, p, t in scored[1:]:
            if not is_placeholder_template(t):
                best, best_score = p, s
                break
    discarded = [p for _, p, _ in scored if p != best]
    # Also discard other placeholder templates explicitly
    return best, discarded


def clean_ws(s: str) -> str:
    s = re.sub(r"%.*?$", "", s, flags=re.M)
    s = re.sub(r"\\footnote\*?(\[[^\]]*\])?\{.*?\}", "", s, flags=re.S)
    s = re.sub(r"\\(textbf|textit|emph|bf|it|rm|tt|large|Large|huge|small|normalsize)\b\s*", "", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}$~]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def extract_between(text: str, start_pat: str, end_pats: list[str]) -> str:
    m = re.search(start_pat, text, re.I | re.S)
    if not m:
        return ""
    start = m.end()
    end = len(text)
    for ep in end_pats:
        em = re.search(ep, text[start:], re.I | re.S)
        if em:
            end = min(end, start + em.start())
    return text[start:end].strip()


def parse_metadata(text: str) -> dict:
    meta = {
        "title": "",
        "authors": "",
        "abstract": "",
        "keywords": "",
        "msc": "",
        "header_title": "",
        "header_authors": "",
    }

    hm = re.search(r"\\fancyhead\[LE\]\{(.*?)\}", text, re.S)
    if hm:
        meta["header_title"] = clean_ws(hm.group(1))
    hm = re.search(r"\\fancyhead\[RO\]\{(.*?)\}", text, re.S)
    if hm:
        meta["header_authors"] = clean_ws(hm.group(1))

    # Title patterns used by ISC18 template and classic \title{}
    title = ""
    tm = re.search(r"\{\\Large\s*\\bf\s*(.*?)\}", text, re.S)
    if tm:
        title = tm.group(1)
    if not title:
        tm = re.search(r"\\title\s*\{(.*?)\}", text, re.S)
        if tm:
            title = tm.group(1)
    if not title and meta["header_title"]:
        title = meta["header_title"]
    meta["title"] = clean_ws(title)

    # Authors: block after title before \end{center} or abstract
    authors = ""
    am = re.search(
        r"\{\\Large\s*\\bf\s*.*?\}\\?\s*\{\\bf\s*(.*?)\}",
        text,
        re.S,
    )
    if am:
        authors = am.group(1)
    if not authors:
        am = re.search(r"\\author\s*\{(.*?)\}", text, re.S)
        if am:
            authors = am.group(1)
    # Keep first line-ish of author block (names), drop long affiliations somewhat
    authors_clean = clean_ws(authors)
    # Prefer names before first Department/University if very long
    if len(authors_clean) > 180:
        cut = re.split(r"\b(Department|Faculty|University|School)\b", authors_clean, maxsplit=1)
        if cut:
            authors_clean = cut[0].strip(" ,;")
    meta["authors"] = authors_clean or meta["header_authors"]

    abstract = extract_between(
        text,
        r"(?:\\noindent\{\\bf\s*Abstract:\s*\}|\\abstract\b|Abstract:)\s*",
        [
            r"\\noindent\{\\bf\s*Keywords?:",
            r"Keywords?:",
            r"\\keyword\b",
            r"Mathematics Subject Classification",
            r"\\section\b",
            r"\\hspace\{1cm\}\\rule",
            r"\\noindent\\rule",
            r"\\rule\{\\textwidth\}",
        ],
    )
    meta["abstract"] = clean_ws(abstract)

    keywords = extract_between(
        text,
        r"(?:\\noindent\{\\bf\s*Keywords?:\s*\}|Keywords?:\s*|\\keyword\s*)",
        [
            r"Mathematics Subject Classification",
            r"\\section\b",
            r"\\hspace\{1cm\}\\rule",
            r"\\noindent\\rule",
            r"\\rule\{\\textwidth\}",
        ],
    )
    meta["keywords"] = clean_ws(keywords).rstrip(".")

    msc = extract_between(
        text,
        r"Mathematics Subject Classification\s*\([^)]*\):\s*",
        [
            r"\\section\b",
            r"\\hspace\{1cm\}\\rule",
            r"\\noindent\\rule",
            r"\\rule\{\\textwidth\}",
            r"\\newpage",
        ],
    )
    msc = clean_ws(msc)
    msc = re.sub(r"^rm\s*", "", msc, flags=re.I)
    meta["msc"] = msc

    return meta


def collect_assets(project_root: Path, raw_dir: Path) -> list[Path]:
    """Collect useful assets near the project (images, sty, bib) excluding build junk."""
    assets = []
    # Search from project root upward to raw_dir for Images folders and sty/bib.
    search_roots = {project_root, raw_dir}
    # Also include immediate children commonly named Images
    for root in list(search_roots):
        for p in root.rglob("*"):
            if not p.is_file() or is_junk(p):
                continue
            ext = p.suffix.lower()
            if ext in BUILD_EXTS and ext != ".pdf":
                # Keep PDF only if it looks like a figure (not a compiled paper).
                continue
            name_l = p.name.lower()
            if ext == ".pdf":
                # Skip compiled paper PDFs (same stem as a tex / template-ish large docs at root)
                if "template" in name_l or p.parent == project_root:
                    # keep figure-like PDFs in Images/
                    if "image" not in str(p.parent).lower() and "fig" not in name_l:
                        continue
            if ext in KEEP_EXTS or ext in {".sty", ".bib", ".bst", ".cls"}:
                # Skip other tex/latex — only main will be copied specially
                if ext in {".tex", ".latex"}:
                    continue
                assets.append(p)
    return assets


def rewrite_image_paths(tex: str, paper_id: str) -> str:
    """Point graphics to booklet-relative main//p.id//... paths where helpful."""
    prefix = f"main//{paper_id}//"

    def repl_includegraphics(m: re.Match) -> str:
        opts = m.group(1) or ""
        path = m.group(2).strip()
        # Already booklet-relative
        if path.replace("\\", "/").startswith("main/"):
            return m.group(0)
        # logo / conference header often at booklet root later
        base = path.replace("\\", "/")
        if base.lower().endswith(("isc18e", "isc18e.jpg", "isc18e.png", "logo.jpg", "logo.png")):
            # keep relative inside paper folder (Images/isc18E)
            if not base.startswith(prefix):
                if "/" not in base and "\\" not in base:
                    base = "Images/" + base
                return f"\\includegraphics{opts}{{{prefix}{base}}}"
            return m.group(0)
        if not base.startswith(prefix):
            return f"\\includegraphics{opts}{{{prefix}{base}}}"
        return m.group(0)

    tex = re.sub(
        r"\\includegraphics(\[[^\]]*\])?\{([^}]*)\}",
        repl_includegraphics,
        tex,
    )
    return tex


def robust_rmtree(path: Path) -> None:
    if not path.exists():
        return
    import time
    def _onexc(func, p, exc_info=None):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass
    for _ in range(5):
        try:
            try:
                shutil.rmtree(path, onexc=_onexc)
            except TypeError:
                shutil.rmtree(path, onerror=lambda f, p, e: _onexc(f, p, e))
            if not path.exists():
                return
        except Exception:
            pass
        time.sleep(0.4)
    if path.exists():
        trash = path.with_name(path.name + f".__trash_{os.getpid()}")
        try:
            path.rename(trash)
        except Exception as e:
            raise PermissionError(f"Could not remove {path}") from e


def ensure_canonical_sty(booklet: Path) -> Path | None:
    """Copy a known-good ISC18-english.sty to booklet root if found."""
    candidates = list(EXTRACTED.rglob("ISC18-english.sty")) + list(EXTRACTED.rglob("isc18-english.sty"))
    if not candidates:
        return None
    # Prefer shorter/canonical ones from successful papers
    candidates.sort(key=lambda p: p.stat().st_size)
    dest = booklet / "ISC18-english.sty"
    shutil.copy2(candidates[0], dest)
    return dest


def process_paper(archive: Path) -> PaperRecord:
    paper_id_num = archive.stem  # 1003
    paper_id = f"p.{paper_id_num}"
    rec = PaperRecord(paper_id=paper_id, status="pending", source_archive=archive.name)

    raw_dir = EXTRACTED / paper_id_num
    robust_rmtree(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    try:
        extract_archive(archive, raw_dir)
    except Exception as e:
        rec.status = "extract_failed"
        rec.notes.append(str(e))
        return rec

    tex_files = [
        p for p in raw_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in {".tex", ".latex"}
        and not is_junk(p)
        and not p.name.lower().endswith(".bak")
        and ".bak." not in p.name.lower()
    ]

    if not tex_files:
        pdfs = [p for p in raw_dir.rglob("*.pdf") if not is_junk(p)]
        if pdfs or archive.suffix.lower() == ".pdf":
            out = MAIN / paper_id
            out.mkdir(parents=True, exist_ok=True)
            for p in pdfs:
                shutil.copy2(p, out / p.name)
            if archive.suffix.lower() == ".pdf":
                shutil.copy2(archive, out / archive.name)
            rec.status = "pdf_only"
            rec.folder = str(out.relative_to(ROOT))
            rec.notes.append("No LaTeX source; PDF copied only")
            return rec
        rec.status = "no_tex"
        rec.notes.append("No .tex/.latex found after extraction")
        return rec

    main_tex, discarded = choose_main_tex(tex_files)
    if main_tex is None:
        rec.status = "no_main"
        rec.notes.append("Could not select main tex")
        return rec

    # Drop discarded placeholder templates from consideration; still record names
    discarded_names = []
    for d in discarded:
        try:
            t = read_text(d)
        except Exception:
            t = ""
        rel = str(d.relative_to(raw_dir))
        if is_placeholder_template(t) or TEMPLATE_NAME_RE.search(d.stem):
            discarded_names.append(rel + " [template]")
        else:
            discarded_names.append(rel + " [not selected]")
    rec.discarded_tex = discarded_names
    rec.main_tex_original = str(main_tex.relative_to(raw_dir))

    text = read_text(main_tex)
    if is_placeholder_template(text) and len(tex_files) == 1:
        rec.notes.append("WARNING: only file looks like unfilled conference template")

    meta = parse_metadata(text)
    for k, v in meta.items():
        setattr(rec, k, v)

    out = MAIN / paper_id
    robust_rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    # Copy project assets (images, sty, bib) from main tex directory tree
    project_root = main_tex.parent
    # Copy sty/bib from project root
    for pattern in ("*.sty", "*.bib", "*.bst", "*.cls"):
        for p in project_root.glob(pattern):
            if not is_junk(p):
                shutil.copy2(p, out / p.name)
                if p.suffix.lower() == ".sty":
                    rec.has_sty = True

    # Copy Images folder(s) if present near project
    for images_dir in project_root.rglob("*"):
        if images_dir.is_dir() and images_dir.name.lower() in {"images", "image", "figures", "figure", "figs", "fig"}:
            if is_junk(images_dir):
                continue
            dest_img = out / images_dir.name
            if dest_img.exists():
                continue
            shutil.copytree(
                images_dir,
                dest_img,
                ignore=shutil.ignore_patterns("__MACOSX", ".DS_Store", "*.ptd"),
            )

    # Copy loose figure files sitting next to the tex
    for p in project_root.iterdir():
        if not p.is_file() or is_junk(p):
            continue
        ext = p.suffix.lower()
        if ext in {".png", ".jpg", ".jpeg", ".eps", ".gif", ".bmp", ".tif", ".tiff", ".svg", ".webp"}:
            shutil.copy2(p, out / p.name)
        elif ext == ".pdf" and any(k in p.stem.lower() for k in ("fig", "plot", "image", "graph", "chart")):
            shutil.copy2(p, out / p.name)

    # Also copy figure-like PDFs referenced in tex that live next to it
    for m in re.finditer(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]*)\}", text):
        rel = m.group(1).strip().replace("\\", "/")
        # strip extension variants
        candidates = [project_root / rel, project_root / Path(rel).name]
        if not Path(rel).suffix:
            for ext in (".pdf", ".png", ".jpg", ".jpeg", ".eps"):
                candidates.append(project_root / (rel + ext))
                candidates.append(project_root / "Images" / (Path(rel).name + ext))
        for c in candidates:
            if c.is_file() and not is_junk(c):
                dest = out / c.name if c.parent == project_root else out / c.relative_to(project_root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    shutil.copy2(c, dest)

    # Write main tex renamed
    rewritten = rewrite_image_paths(text, paper_id)
    # Normalize style input/usepackage to local sty if present
    out_tex = out / f"{paper_id}.tex"
    # If original was .latex, still write .tex
    out_tex.write_text(rewritten, encoding="utf-8")
    rec.main_tex = str(out_tex.relative_to(ROOT))
    rec.folder = str(out.relative_to(ROOT))
    rec.has_sty = rec.has_sty or any(out.glob("*.sty"))
    rec.n_figures = sum(
        1
        for p in out.rglob("*")
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".eps", ".pdf", ".gif"}
        and "isc18e" not in p.name.lower()
    )
    rec.status = "ok"
    if not rec.title:
        rec.notes.append("Could not parse title")
    if not rec.abstract:
        rec.notes.append("Could not parse abstract")
    return rec


def write_include_scaffold(records: list[PaperRecord]) -> None:
    lines = [
        "% Auto-generated paper includes for ISC18 English full booklet",
        "% Paste into Full-English.tex (or \\input{paper-includes})",
        "% After TOC / front matter, start arabic paging then include papers.",
        "",
    ]
    toc_lines = [
        "% --- TOC entries (title / authors / pageref) ---",
        "",
    ]
    ok = [r for r in records if r.status == "ok"]
    ok.sort(key=lambda r: r.title.lower() if r.title else r.paper_id)
    for r in ok:
        title = r.title or r.paper_id
        authors = r.authors or r.header_authors or ""
        toc_lines += [
            f"% {r.paper_id}",
            "\\noindent",
            "{\\bf",
            f"{title}",
            f"}}\\dotfill \\pageref{{{r.paper_id}}}\\\\",
            "{\\small \\it",
            f"{authors}",
            "}",
            "",
            "",
        ]
        lines += [
            "\\newpage",
            "\\setcounter{equation}{0}",
            "\\setcounter{section}{0} \\setcounter{figure}{0}",
            "\\setcounter{table}{0}",
            f"\\phantomsection \\label{{{r.paper_id}}}",
            f"\\include{{main//{r.paper_id}//{r.paper_id}}}",
            "",
        ]
    (BOOKLET / "paper-includes.tex").write_text("\n".join(lines), encoding="utf-8")
    (BOOKLET / "paper-toc.tex").write_text("\n".join(toc_lines), encoding="utf-8")


def write_abstract_booklet_scaffold(records: list[PaperRecord]) -> None:
    lines = [
        "% Auto-generated abstract booklet body from extracted metadata",
        "\\documentclass[10pt,twoside]{article}",
        "\\usepackage[papersize={16.5cm,23.5cm},top=2cm,bottom=1.5cm,left=1.5cm,right=1.5cm]{geometry}",
        "\\usepackage{times}",
        "\\begin{document}",
        "",
    ]
    ok = [r for r in records if r.status == "ok"]
    ok.sort(key=lambda r: r.title.lower() if r.title else r.paper_id)
    for r in ok:
        lines += [
            f"% {r.paper_id}",
            "\\noindent{\\bf " + (r.title or r.paper_id) + "}\\\\",
            "{\\it " + (r.authors or "") + "}\\\\[0.4em]",
            (r.abstract or "") + "\\\\[0.3em]",
            ("{\\bf Keywords:} " + r.keywords + "\\\\" if r.keywords else ""),
            ("{\\bf MSC:} " + r.msc + "\\\\" if r.msc else ""),
            "\\vspace{1em}\\hrule\\vspace{1em}",
            "",
        ]
    lines.append("\\end{document}")
    (BOOKLET / "Abstract-Booklet-auto.tex").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not STAGING.exists():
        print("Staging folder missing. Extract Papers (2).zip first.", file=sys.stderr)
        return 1

    BOOKLET.mkdir(parents=True, exist_ok=True)
    MAIN.mkdir(parents=True, exist_ok=True)
    EXTRACTED.mkdir(parents=True, exist_ok=True)

    archives = sorted(
        [
            p for p in STAGING.iterdir()
            if p.is_file() and p.suffix.lower() in {".zip", ".rar", ".tex", ".latex", ".pdf"}
        ],
        key=lambda p: p.name,
    )
    print(f"Found {len(archives)} submissions")

    records: list[PaperRecord] = []
    for i, archive in enumerate(archives, 1):
        print(f"[{i}/{len(archives)}] {archive.name} ...", flush=True)
        rec = process_paper(archive)
        records.append(rec)
        msg = rec.title[:60] if rec.title else "; ".join(rec.notes) or rec.status
        print(f"    -> {rec.status}: {msg}".encode("ascii", "replace").decode("ascii"))

    ensure_canonical_sty(BOOKLET)

    # Metadata outputs
    meta_json = BOOKLET / "papers_metadata.json"
    meta_csv = BOOKLET / "papers_metadata.csv"
    with meta_json.open("w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in records], f, ensure_ascii=False, indent=2)

    fieldnames = [
        "paper_id", "status", "title", "authors", "abstract", "keywords", "msc",
        "header_title", "header_authors", "main_tex", "main_tex_original",
        "discarded_tex", "has_sty", "n_figures", "source_archive", "notes", "folder",
    ]
    with meta_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            row = asdict(r)
            row["discarded_tex"] = " | ".join(r.discarded_tex)
            row["notes"] = " | ".join(r.notes)
            w.writerow(row)

    write_include_scaffold(records)
    write_abstract_booklet_scaffold(records)

    # Summary
    from collections import Counter
    counts = Counter(r.status for r in records)
    summary = {
        "total": len(records),
        "by_status": dict(counts),
        "ok_with_title": sum(1 for r in records if r.status == "ok" and r.title),
        "ok_with_abstract": sum(1 for r in records if r.status == "ok" and r.abstract),
        "templates_discarded": sum(
            1 for r in records for d in r.discarded_tex if "[template]" in d
        ),
    }
    (BOOKLET / "build_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nOutputs under: {BOOKLET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
