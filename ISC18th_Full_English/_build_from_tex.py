#!/usr/bin/env python3
"""
Rebuild Full-English.pdf by compiling EVERY paper from LaTeX.

- Prefer author's original ISC18 .tex (keeps template look); neutralize page numbers
- Plain / non-ISC18 .tex: wrap once with ISC18 header + single \\maketitle
- PDF-only fallback only when no .tex exists
- Fit to booklet page size; strip residual original numbers
- Each paper starts on an ODD arabic page (blank page after previous if needed)
- Stamp continuous arabic page numbers in ISC18 header corners (RE/LO)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

ROOT = Path(r"i:\Booklet ENG")
BOOK = ROOT / "ISC18th_Full_English"
MAIN = BOOK / "main"
RAW = ROOT / "_extracted_raw"
BUILD = BOOK / "_build_from_tex"
PAPERS = BUILD / "papers"
PDFLATEX = Path(r"C:\Users\Asus\AppData\Local\Programs\MiKTeX 2.9\miktex\bin\x64\pdflatex.exe")
BANNER = BOOK / "Images" / "isc18E.jpg"
STY = BOOK / "ISC18-english.sty"

# Booklet trim size (cm) used throughout
PAGE_W_CM = 16.5
PAGE_H_CM = 23.5
# PDF points (1 cm ≈ 28.3465 pt)
PAGE_W_PT = PAGE_W_CM * 28.3465
PAGE_H_PT = PAGE_H_CM * 28.3465

WORKERS = 2


def run_pdflatex(tex_name: str, cwd: Path, jobname: str | None = None) -> subprocess.CompletedProcess:
    args = [str(PDFLATEX), "-interaction=nonstopmode", "-shell-escape"]
    if jobname:
        args.append(f"-jobname={jobname}")
    args.append(tex_name)
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    # Normalize newlines so Windows write_text does not create \\r\\r\\n corruption
    return text.replace("\r\n", "\n").replace("\r", "\n")


def latex_escape(s: str) -> str:
    if not s:
        return ""
    for a, b in {
        "\u2011": "-", "\u2013": "--", "\u2014": "---",
        "\u2018": "'", "\u2019": "'", "\u201c": "``", "\u201d": "''",
        "\ufb01": "fi", "\ufb02": "fl",
    }.items():
        s = s.replace(a, b)
    s = re.sub(r"^\\+", "", s.strip())
    parts = re.split(r"(\$[^$]+\$)", s)
    out = []
    for part in parts:
        if part.startswith("$") and part.endswith("$"):
            out.append(part)
            continue
        p = part.replace("\\", r"\textbackslash{}")
        for a, b in {
            "&": r"\&", "%": r"\%", "#": r"\#", "_": r"\_",
            "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}",
            "^": r"\textasciicircum{}",
        }.items():
            p = p.replace(a, b)
        out.append(p)
    return "".join(out)


_TITLE_PREFIX_RE = re.compile(
    r"^(?:dr\.?|prof\.?|professor|mr\.?|mrs\.?|ms\.?|miss|eng\.?|engineer|sir|phd)\s+",
    re.I,
)
_NAME_JUNK_RE = re.compile(
    r"(?i)\b(?:orcid\s*:?\s*[\d-]+|corresponding\s+authou?r.*|email\s*:.*|"
    r"department\b.*|university\b.*|faculty\b.*)$"
)
_INITIAL_TOKEN_RE = re.compile(r"^[A-Za-z]\.?$")
_SKIP_NAME_LINE_RE = re.compile(
    r"(?i)\b(Abstract|Keyword|Corresponding Author|Iranian Statistical|"
    r"Article title|MSC|Classification|Short article|Short authors|"
    r"Mathematics Subject|How to write|Proof\.|Theorem|Lemma)\b"
)


def _strip_name_titles(s: str) -> str:
    s = (s or "").strip(" ,;.|")
    s = s.replace(r"\ ", " ").replace(r"\&", "&")
    s = re.sub(r"\s+", " ", s)
    while True:
        nxt = _TITLE_PREFIX_RE.sub("", s).strip(" ,;.|")
        if nxt == s:
            break
        s = nxt
    return s


def _tex_strip_to_plain(s: str) -> str:
    """Remove TeX commands/braces from an author snippet."""
    s = re.sub(r"(?is)\\footnote\*?(\[[^\]]*\])?\{.*?\}", " ", s)
    s = re.sub(r"(?is)\\thanks\{.*?\}", " ", s)
    s = re.sub(r"(?is)\\textsuperscript\{.*?\}", " ", s)
    s = re.sub(r"(?is)\\footnotemark\b(\[[^\]]*\])?", " ", s)
    s = re.sub(r"(?is)\\tt\b|\\texttt\{([^}]*)\}", r" \1 ", s)
    s = re.sub(r"(?is)\\url\{([^}]*)\}", " ", s)
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}]", " ", s)
    s = re.sub(r"[*$†‡§¶\\]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,;.|")


def _balanced_brace_arg(text: str, open_idx: int) -> str:
    """Return contents of {...} starting at open_idx which points at '{'."""
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return ""
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i]
        i += 1
    return ""


def _fullname_fullness_score(name: str) -> int:
    """Higher = more full given-name content (prefer over initials)."""
    if not name:
        return -1
    tokens = [t for t in re.split(r"[\s,]+", name) if t]
    if not tokens:
        return -1
    score = 0
    for t in tokens:
        core = t.strip(".")
        if not core:
            continue
        if _INITIAL_TOKEN_RE.match(t) or (len(core) == 1 and core.isalpha()):
            score += 0
        elif re.fullmatch(r"(?:[A-Za-z]\.){2,}", t.replace(" ", "")):
            # S.M.T.K. style
            score += 0
        else:
            score += max(2, len(core))
    return score


def _is_mostly_initials(name: str) -> bool:
    tokens = [t for t in re.split(r"[\s,]+", name or "") if t]
    if not tokens:
        return True
    full = 0
    for t in tokens[:-1]:  # ignore surname token for this check
        core = t.strip(".")
        if len(core) >= 2 and not re.fullmatch(r"(?:[A-Za-z]\.)+", t.replace(" ", "")):
            full += 1
    # Also treat "N.Sanjari" glued form as initial-ish given
    if tokens and re.match(r"^[A-Za-z]\.[A-Za-z]", tokens[0]):
        return full == 0
    return full == 0 and len(tokens) >= 1


def _expand_given_from_email(name: str, tex: str) -> str:
    """If given name is initials only, try firstname from matching email local-part."""
    if not name or not _is_mostly_initials(name):
        return name
    parts = name.split()
    if not parts:
        return name
    surname = parts[-1].strip(".,")
    if len(surname) < 3:
        return name
    emails = re.findall(
        r"([A-Za-z][A-Za-z0-9._-]{1,40})@[A-Za-z0-9.-]+", tex
    )
    sur_cf = surname.casefold().replace("-", "")
    for local in emails:
        local_cf = local.casefold().replace("-", "")
        if sur_cf not in local_cf and not local_cf.endswith(sur_cf[:5]):
            # require surname overlap in local part
            if sur_cf not in local_cf.replace(".", "").replace("_", ""):
                continue
        # firstname.lastname or firstname_lastname or firstnamelastname
        bits = re.split(r"[._]", local)
        bits = [b for b in bits if b]
        if len(bits) >= 2:
            given = bits[0]
            # skip if given is just an initial
            if len(given) >= 3 and given.isalpha():
                return f"{given[:1].upper() + given[1:].casefold()} {surname}"
        # camel or concatenated: mahdiroozbeh — skip
    return name


def _split_first_from_author_block(block: str) -> str:
    plain = _tex_strip_to_plain(block)
    plain = _NAME_JUNK_RE.sub("", plain).strip(" ,;.|")
    if not plain or _SKIP_NAME_LINE_RE.search(plain):
        return ""
    return toc_first_author(plain, "", "")


def extract_author_block_candidates(tex: str) -> list[str]:
    """
    Collect first-author candidates from title-page author markup in the TeX,
    including commented-out author lines (often hold the full names).
    """
    cands: list[str] = []
    # Work on a version where we also scan comment lines in the header area
    head = tex[:16000]

    def add_from_block(raw: str) -> None:
        name = _split_first_from_author_block(raw)
        if name and len(name) >= 2:
            cands.append(name)

    # 1) Live \author{...}
    for m in re.finditer(r"\\author\s*\{", head):
        arg = _balanced_brace_arg(head, m.end() - 1)
        if arg:
            add_from_block(arg)

    # 2) Title-page {\bf ...} blocks before Abstract (live)
    for m in re.finditer(r"\{\\bf\b", head):
        arg = _balanced_brace_arg(head, m.start())
        if not arg:
            continue
        if _SKIP_NAME_LINE_RE.search(arg) and "Corresponding" not in arg:
            # may still be author block containing nested Corresponding footnote
            if not re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", arg):
                continue
        # Skip pure title lines (long, few commas, has "of the" etc.)
        plain = _tex_strip_to_plain(arg)
        if len(plain) > 160:
            continue
        if re.search(r"(?i)\b(performance|analysis|estimation|approach|model|using)\b", plain):
            # likely a paper title in {\Large \bf ...} — require name-like pattern
            if not re.search(r"[A-Za-z]{2,}\s+[A-Za-z]{2,}", plain):
                continue
            # titles are usually longer sentences
            if plain.count(" ") >= 8 and "," not in plain:
                continue
        add_from_block(arg)

    # 3) Commented author lines near header (full names often parked here)
    for m in re.finditer(
        r"(?im)^%\s*(?:\{\\bf\s*)?([A-Za-z][^%\n]{2,120})",
        head,
    ):
        line = m.group(1)
        if _SKIP_NAME_LINE_RE.search(line):
            continue
        if not re.search(r"[A-Za-z]{2,}", line):
            continue
        # Likely an author if it has a name shape or footnote marker
        if re.search(
            r"(?:\\footnote|\$\^\d|,\s*[A-Z]\.|\\tt\b|@[A-Za-z]|Department)",
            line,
        ) or re.match(r"^[A-Z][a-zA-Z.\-]+\s+[A-Z]", line):
            add_from_block(line)

    # 4) Email-based expansion applied later on best candidate
    return cands


def extract_first_author_from_tex(tex: str) -> str:
    """Best full first-author name available in the LaTeX source."""
    cands = extract_author_block_candidates(tex)
    if not cands:
        return ""
    # Prefer fullest non-initial given names
    cands_sorted = sorted(
        cands,
        key=lambda n: (_fullname_fullness_score(n), len(n)),
        reverse=True,
    )
    best = cands_sorted[0]
    best = _expand_given_from_email(best, tex)
    # Also try expanding weaker cands if best is still initials
    if _is_mostly_initials(best):
        for c in cands_sorted:
            expanded = _expand_given_from_email(c, tex)
            if _fullname_fullness_score(expanded) > _fullname_fullness_score(best):
                best = expanded
    return best


def _first_author_chunk(authors: str, header_authors: str = "") -> str:
    """Isolate the first-author phrase from a multi-author string."""
    src = (authors or "").strip()
    if not src or len(src) < 2:
        src = (header_authors or "").strip()
    src = re.sub(r"(?is)\\thanks\{.*?\}", "", src)
    src = re.sub(r"(?is)\\textsuperscript\{.*?\}", "", src)
    src = re.sub(r"(?is)\\footnotemark\b", "", src)
    src = re.sub(r"[*$†‡§¶]+", "", src)
    src = re.sub(r"\s+", " ", src).strip(" ,;.|")
    src = _NAME_JUNK_RE.sub("", src).strip(" ,;.|")
    if not src:
        return ""

    # Prefer explicit coauthor separators before comma-splitting
    for sep in [
        r"\bet\s+al\.?\b",
        r"\s+and\s+",
        r"\s+\&\s+",
        r"\s*;\s*",
        r"\s*/\s*",
    ]:
        parts = re.split(sep, src, maxsplit=1, flags=re.I)
        if len(parts) > 1 and parts[0].strip():
            src = parts[0].strip(" ,;.|")
            break

    # "Surname, Initials" single-author style — keep whole phrase for now
    if re.match(r"^[^,]+,\s*([A-Z]\.?\s*)+$", src):
        return src.strip()

    # "Given Family, Given Family, ..." → first person only
    if "," in src:
        # Avoid splitting composite "Surname, Initials" of first author when more follow:
        # "Azizi Kouhanestani, M., Zamanzade, E." → take up through first initials group
        m = re.match(r"^([^,]+,\s*(?:[A-Z]\.?\s*)+)(?:,|$)", src)
        if m:
            return m.group(1).strip(" ,;.|")
        src = re.split(r",\s*", src, maxsplit=1)[0].strip(" ,;.|")

    return src


def toc_first_author(
    authors: str = "",
    header_authors: str = "",
    first_author: str = "",
    tex: str | None = None,
) -> str:
    """
    TOC display: first author only, given name + family name, no titles.
    Prefers full names extracted from the paper's LaTeX when available.
    """
    tex_name = extract_first_author_from_tex(tex) if tex else ""

    chunk = _first_author_chunk(authors, header_authors)
    if not chunk and first_author:
        chunk = first_author.strip()
    if not chunk:
        chunk = _first_author_chunk(header_authors, "")
    chunk = _strip_name_titles(chunk)
    chunk = re.sub(r"\s*:\s*\S*$", "", chunk)  # trailing junk like ":h"
    chunk = re.sub(r"\s+\d+\s*$", "", chunk)  # affiliation numbers
    chunk = re.sub(r"\s+", " ", chunk).strip(" ,;.|")

    # "Family, G." / "Family, G. H." → "G. H. Family" (preserve multi-word family)
    m = re.match(r"^(.+?),\s*((?:[A-Z]\.?\s*)+)$", chunk)
    if m:
        family = m.group(1).strip()
        given = re.sub(r"\s+", " ", m.group(2)).strip()
        given = re.sub(r"\b([A-Za-z])\.?\b", lambda x: x.group(1).upper() + ".", given)
        given = re.sub(r"\s+", " ", given).strip()
        chunk = f"{given} {family}".strip()

    # Token cleanup
    raw_tokens = chunk.replace(",", " ").split()
    tokens: list[str] = []
    for t in raw_tokens:
        tl = t.lower().strip(".")
        if tl in {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "eng", "sir", "phd"}:
            continue
        if re.fullmatch(r"\d+", t):
            continue
        tokens.append(t.strip(" ,;|"))
    while tokens and re.fullmatch(r"[A-Za-z]", tokens[-1]):
        tokens.pop()
    plain = [t for t in tokens if re.fullmatch(r"[A-Za-z]{2,}", t)]
    had_sep = bool(
        re.search(r"\b(?:and|&|;|,)\b", authors or "", re.I)
        or re.search(r"\b(?:and|&|;|,)\b", header_authors or "", re.I)
    )
    if not had_sep and len(tokens) == 4 and len(plain) == 4:
        tokens = tokens[:2]
    meta_name = " ".join(tokens).strip(" ,;.|") if tokens else ""
    if tex and meta_name and _is_mostly_initials(meta_name):
        meta_name = _expand_given_from_email(meta_name, tex)

    # Prefer LaTeX-extracted full name when it is fuller
    if tex_name and (
        not meta_name
        or _fullname_fullness_score(tex_name) > _fullname_fullness_score(meta_name)
        or (_is_mostly_initials(meta_name) and not _is_mostly_initials(tex_name))
        or (
            _name_fullness_score(tex_name) == _name_fullness_score(meta_name)
            and len(tex_name) >= len(meta_name)
        )
    ):
        return tex_name
    return meta_name or tex_name


def resolve_toc_author(paper_id: str, record: dict | None = None) -> str:
    """Resolve TOC author from the paper's main TeX, falling back to metadata."""
    import importlib.util

    toc_path = Path(__file__).with_name("_toc_authors.py")
    spec = importlib.util.spec_from_file_location("toc_authors", str(toc_path))
    toc_mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(toc_mod)
    return toc_mod.resolve(paper_id, MAIN, record or {})




def build_toc(records, page_map, out_pdf: Path) -> int:
    lines = [
        r"\documentclass[10pt,twoside]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{times}",
        rf"\usepackage[papersize={{{PAGE_W_CM}cm,{PAGE_H_CM}cm}},top=2cm,bottom=1.5cm,left=1.5cm,right=1.5cm]{{geometry}}",
        r"\usepackage{fancyhdr}",
        r"\pagestyle{fancy}\fancyhf{}",
        r"\renewcommand{\headrulewidth}{0.2pt}",
        r"\fancyhead[LE,RO]{\thepage}",
        r"\begin{document}",
        r"\pagenumbering{roman}",
        r"\begin{center}",
        r"{\Large\bfseries 18th Iranian Statistical Conference}\\[0.4em]",
        r"{\large Full Papers Booklet (English)}\\[0.8em]",
        r"{\huge\bfseries Table of Contents}",
        r"\end{center}\vspace{0.5em}",
        r"{\small\itshape Sorted by last name of first author}\\[0.7em]",
        "",
    ]
    for r in records:
        title = latex_escape(r.get("title") or r["paper_id"])
        author = resolve_toc_author(r["paper_id"], r)
        authors = latex_escape(author)
        pg = page_map[r["paper_id"]]
        lines += [
            rf"\noindent{{\bfseries {title}}}\dotfill {pg}\\",
            rf"{{\small\itshape {authors}}}\\[0.45em]",
            "",
        ]
    lines.append(r"\end{document}")
    toc_dir = BUILD / "toc"
    toc_dir.mkdir(parents=True, exist_ok=True)
    toc_tex = toc_dir / "toc_src.tex"
    toc_tex.write_text("\n".join(lines), encoding="utf-8")
    for _ in range(2):
        run_pdflatex(toc_tex.name, toc_dir, jobname="toc_out")
    shutil.copy2(toc_dir / "toc_out.pdf", out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def find_command_arg(text: str, cmd: str) -> str:
    m = re.search(rf"(?<!@)\\{cmd}\s*\{{", text)
    if not m:
        return ""
    i = m.end() - 1
    depth = 0
    start = i
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i]
        i += 1
    return ""


def is_isc18_tex(tex: str) -> bool:
    """True if source already uses the ISC18 conference template."""
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


def patch_for_booklet(tex: str, start_page: int = 1) -> str:
    """Keep ISC18 fancyhdr style (LO/RE + headrule); only set continuous page counter."""
    # Ensure EPS figures convert
    if "epstopdf" not in tex and r"\includegraphics" in tex:
        tex = re.sub(
            r"(\\documentclass(?:\[[^\]]*\])?\{[^}]*\}\s*)",
            r"\1\\usepackage{epstopdf}\n",
            tex,
            count=1,
        )
    # Plain articles need fancyhdr for ISC18 LO/RE + headrule
    if "fancyhdr" not in tex:
        tex = re.sub(
            r"(\\documentclass(?:\[[^\]]*\])?\{[^}]*\}\s*)",
            r"\1\\usepackage{fancyhdr}\n",
            tex,
            count=1,
        )
    # Drop author hard-coded page resets
    tex = re.sub(r"\\setcounter\{page\}\{\d+\}\s*", "", tex)

    inject_bits = [
        "\n% --- booklet: continuous ISC18 page numbers ---\n",
        rf"\setcounter{{page}}{{{start_page}}}" + "\n",
        r"\fancyhead[LO]{\thepage}" + "\n",
        r"\fancyhead[RE]{\thepage}" + "\n",
        r"\renewcommand{\headrulewidth}{0.4pt}" + "\n",
        r"\pagestyle{fancy}" + "\n",
    ]
    if r"\date{" not in tex and r"\date{}" not in tex:
        inject_bits.insert(1, r"\date{}" + "\n")
    inject_bits.append("% --- end booklet patch ---\n")
    inject = "".join(inject_bits)

    marker = "\\begin{document}"
    idx = tex.find(marker)
    if idx >= 0:
        at = idx + len(marker)
        tex = tex[:at] + inject + tex[at:]
    return tex


def neutralize_page_numbers(tex: str) -> str:
    """Backward-compatible alias — prefer patch_for_booklet with start page."""
    return patch_for_booklet(tex, start_page=1)


def ensure_assets(work: Path) -> None:
    if STY.exists():
        shutil.copy2(STY, work / "ISC18-english.sty")
    img_dir = work / "Images"
    img_dir.mkdir(parents=True, exist_ok=True)
    if BANNER.exists():
        # common names used by submissions
        for name in ("isc18E.jpg", "isc18E.jpeg", "isc18E.png", "isc18e.jpg", "logo.jpg"):
            dst = img_dir / name
            if not dst.exists():
                shutil.copy2(BANNER, dst)
        # bare stem for \includegraphics{Images/isc18E}
        stem = img_dir / "isc18E.jpg"
        if not stem.exists():
            shutil.copy2(BANNER, stem)


def run_bibtex(jobname: str, cwd: Path) -> None:
    bibtex = PDFLATEX.parent / "bibtex.exe"
    if not bibtex.exists():
        return
    subprocess.run(
        [str(bibtex), jobname],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def compile_native(
    paper_id: str, tex_path: Path, out_raw: Path, start_page: int = 1
) -> tuple[bool, str]:
    """Compile author TeX in its own folder so relative Images/ figures/ paths work."""
    folder = tex_path.parent
    ensure_assets(folder)

    build_name = "_booklet_build.tex"
    build_tex = folder / build_name
    patched = patch_for_booklet(read_text(tex_path), start_page=start_page)
    # Undo any leftover booklet path rewriting from earlier builds
    patched = re.sub(r"main/+p\.\d+/+", "", patched, flags=re.I)
    build_tex.write_text(patched, encoding="utf-8", newline="\n")

    try:
        for _ in range(2):
            run_pdflatex(build_name, folder)
        # Resolve citations when a .bib is present
        if any(folder.glob("*.bib")):
            run_bibtex("_booklet_build", folder)
            for _ in range(2):
                run_pdflatex(build_name, folder)

        produced = folder / "_booklet_build.pdf"
        if not produced.exists() or produced.stat().st_size < 5000:
            return False, f"native pdf missing ({produced})"
        try:
            n = len(PdfReader(str(produced)).pages)
        except Exception as e:
            return False, f"unreadable pdf: {e}"
        if n <= 0:
            return False, "zero pages"
        out_raw.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, out_raw)
        return True, f"native:{n}@p{start_page}"
    finally:
        for ext in (".aux", ".log", ".out", ".toc", ".bbl", ".blg", ".pdf", ".tex"):
            p = folder / f"_booklet_build{ext}"
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


ISC18_WRAPPER = r"""\documentclass[10pt,twoside]{article}
\input{ISC18-english.sty}
\usepackage{graphicx,amsmath,amssymb,amsthm,amsfonts,mathtools,booktabs,multirow,array,float,caption,subcaption,enumitem,xcolor,tikz,natbib,hyperref,algorithm,algpseudocode}
\geometry{papersize={16.5cm,23.5cm},top=2cm,bottom=1.6cm,left=1.5cm,right=1.5cm}
\hypersetup{colorlinks=true,linkcolor=blue,citecolor=magenta,urlcolor=cyan}
\fancyhead[LO]{\thepage}
\fancyhead[RE]{\thepage}
\pagestyle{fancy}
\graphicspath{{./}{Images/}{figures/}{Figures/}}
\begin{document}
\setcounter{page}{STARTPAGE}
BODY
\end{document}
"""


def _strip_brace_cmd(text: str, cmd: str) -> str:
    """Remove \\cmd{...} with balanced braces."""
    while True:
        m = re.search(rf"\\{cmd}\s*\{{", text)
        if not m:
            return text
        i = m.end() - 1
        depth = 0
        start = m.start()
        while i < len(text):
            if text[i] == "\\" and i + 1 < len(text):
                i += 2
                continue
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    text = text[:start] + text[i + 1 :]
                    break
            i += 1
        else:
            return text


def sanitize_for_wrapper(
    tex: str, title_override: str = "", author_override: str = ""
) -> str:
    """Build ISC18-style manual title block (no \\maketitle — avoids broken first pages)."""
    title = (title_override or find_command_arg(tex, "title") or "").strip()
    author = (author_override or find_command_arg(tex, "author") or "").strip()
    # Soften footnote[2] which becomes 'Imani22' in PDF text
    author = re.sub(
        r"\\footnote\s*\[[^\]]*\]\s*\{",
        r"\\footnote{",
        author,
    )
    # Drop empty author braces
    if re.fullmatch(r"\s*", author or ""):
        author = ""
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, re.S | re.I)
    body = m.group(1) if m else tex

    # Keep useful preamble macros/theorems (before \begin{document})
    pre = tex[: m.start()] if m else ""
    pre = re.sub(r"(?is)\\documentclass.*?(\n|$)", "\n", pre)
    pre = re.sub(r"(?is)\\usepackage(\[[^\]]*\])?\{[^}]*\}", "\n", pre)
    pre = re.sub(r"(?is)\\input\{[^}]*\}", "\n", pre)
    pre = re.sub(r"(?is)\\geometry\{[^}]*\}", "\n", pre)
    pre = re.sub(r"(?is)\\hypersetup\{[^}]*\}", "\n", pre)
    pre = re.sub(r"(?is)\\fancyhead.*?$", "", pre, flags=re.M)
    pre = re.sub(r"(?is)\\fancyfoot.*?$", "", pre, flags=re.M)
    pre = re.sub(r"(?is)\\pagestyle\{[^}]*\}", "\n", pre)
    pre = re.sub(r"(?is)\\setcounter\{page\}\{\d+\}", "\n", pre)
    # Drop custom title macros — wrapper supplies its own banner/title block
    pre = re.sub(r"(?is)\\makeatletter.*?\\makeatother", "\n", pre)
    pre = re.sub(r"(?is)\\def\\@maketitle\b.*?$(?:\n.*?)*?(?=\\makeatother|\n\\[a-zA-Z]|\Z)", "\n", pre)
    # Keep single-line theorem/macro defs only (never raw \def/\let — often multi-line)
    keep_lines = []
    for ln in pre.splitlines():
        s = ln.strip()
        if not s or s.startswith("%"):
            continue
        if re.match(
            r"\\(newtheorem|newcommand|renewcommand|providecommand|DeclareMathOperator)\b",
            s,
        ):
            # Skip header/footer width tweaks that fight ISC18 fancyhdr
            if "headrulewidth" in s or "footrulewidth" in s:
                continue
            # Skip incomplete multi-line macro starts
            if s.endswith("%") or (s.count("{") > s.count("}")):
                continue
            keep_lines.append(ln)
    preamble_keep = "\n".join(keep_lines)

    body = re.sub(r"\\input\{[^}]*ISC18[^}]*\}\s*", "", body, flags=re.I)
    body = re.sub(r"\\usepackage(\[[^\]]*\])?\{[^}]*ISC18[^}]*\}\s*", "", body, flags=re.I)
    body = re.sub(r"\\documentclass\b.*?\n", "", body)
    body = re.sub(r"\\maketitle\b", "", body)
    body = _strip_brace_cmd(body, "title")
    body = _strip_brace_cmd(body, "author")
    body = _strip_brace_cmd(body, "date")
    body = re.sub(r"\\fancyhead\[[^\]]*\]\{.*?\}\s*", "", body, flags=re.S)
    body = re.sub(r"\\fancyfoot\[[^\]]*\]\{.*?\}\s*", "", body, flags=re.S)
    body = re.sub(r"\\pagestyle\{.*?\}\s*", "", body)
    body = re.sub(r"\\thispagestyle\{.*?\}\s*", "", body)
    body = re.sub(
        r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*(?:isc18e|logo\.jpg)[^}]*\}\s*(?:\\\\)?\s*",
        "",
        body,
        flags=re.I,
    )
    body = re.sub(r"\\vspace\*\{-1\.8cm\}\s*", "", body)
    body = re.sub(r"main/+p\.\d+/+", "", body, flags=re.I)
    # Drop leftover custom \@maketitle / makeatletter title blocks already expanded away

    header = []
    if preamble_keep.strip():
        header.append(preamble_keep)
        header.append("")
    header.append(r"\begin{center}")
    header.append(r"{\includegraphics[height=25mm,width=150.3mm]{Images/isc18E}}\\")
    header.append(r"\vspace*{-1.8cm}")
    header.append(r"\vspace*{3.5cm}")
    if title:
        # title may contain \\ already
        header.append(rf"{{\Large\bf {title}}}\\[0.6em]")
    if author:
        header.append(rf"{{\bf {author}}}")
    header.append(r"\end{center}")
    header.append(r"\rule{\textwidth}{0.2mm}\\")
    return "\n".join(header) + "\n" + body.strip() + "\n"


def compile_wrapper(
    paper_id: str,
    tex_path: Path,
    out_raw: Path,
    start_page: int = 1,
    title_override: str = "",
    author_override: str = "",
) -> tuple[bool, str]:
    work = BUILD / "compile_wrap" / paper_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    if work.exists():
        work = BUILD / "compile_wrap" / f"{paper_id}_{int(time.time())}"

    # Full tree copy — keep vertopal/media and all figure folders/paths intact
    shutil.copytree(
        tex_path.parent,
        work,
        ignore=shutil.ignore_patterns(
            "*.aux", "*.log", "*.out", "*.toc", "*.synctex.gz", "__MACOSX",
            "_booklet_build.*",
        ),
    )
    ensure_assets(work)

    body = sanitize_for_wrapper(
        read_text(tex_path),
        title_override=title_override,
        author_override=author_override,
    )
    src = work / f"{paper_id}.tex"
    src.write_text(
        ISC18_WRAPPER.replace("BODY", body).replace("STARTPAGE", str(start_page)),
        encoding="utf-8",
        newline="\n",
    )

    for _ in range(2):
        run_pdflatex(src.name, work)
    if any(work.glob("*.bib")):
        run_bibtex(paper_id, work)
        for _ in range(2):
            run_pdflatex(src.name, work)

    produced = work / f"{paper_id}.pdf"
    if not produced.exists() or produced.stat().st_size < 5000:
        return False, "wrapper pdf missing"
    try:
        n = len(PdfReader(str(produced)).pages)
    except Exception as e:
        return False, f"unreadable: {e}"
    if n <= 0:
        return False, "zero pages"
    out_raw.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(produced, out_raw)
    return True, f"wrap:{n}@p{start_page}"


def find_submission_pdf(paper_id: str) -> Path | None:
    num = paper_id.split(".", 1)[1]
    cands: list[tuple[int, Path]] = []
    for root in (RAW / num, MAIN / paper_id):
        if not root.exists():
            continue
        for p in root.rglob("*.pdf"):
            low = str(p).lower().replace("\\", "/")
            if "__macosx" in low:
                continue
            size = p.stat().st_size
            if size < 80_000:
                continue
            name = p.name.lower()
            if name.endswith("-eps-converted-to.pdf"):
                continue
            if "template" in name:
                continue
            if re.search(
                r"(^|[_/-])(fig|figure|plot|image|graph|chart|preview)([_-]|\d|\.|$)",
                name,
            ):
                if not any(k in name for k in ("template", "isc18", "manuscript", "paper", "main")):
                    continue
            score = size
            if any(k in name for k in ("isc18", "manuscript", "paper", "main", "english", "revise", "final")):
                score += 8_000_000
            cands.append((score, p))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1]


def normalize_unnumbered(src: Path, out_pdf: Path, wipe_old_numbers: bool = False) -> int:
    """Scale paper to booklet size. Optionally wipe old page-number slots (PDF fallbacks only)."""
    work = BUILD / "normalize" / src.stem
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, work / "src.pdf")
    tex = work / "norm.tex"
    if wipe_old_numbers:
        # Wide corner wipes so original LO/RE digits (and any prior stamp) disappear
        pagecmd = r"""%
  \begin{tikzpicture}[remember picture,overlay]
    \fill[white] ([yshift=0.0cm]current page.south west) rectangle ([yshift=1.05cm]current page.south east);
    \fill[white] ([xshift=0.05cm,yshift=-0.05cm]current page.north west) rectangle ([xshift=3.4cm,yshift=-1.35cm]current page.north west);
    \fill[white] ([xshift=-0.05cm,yshift=-0.05cm]current page.north east) rectangle ([xshift=-3.4cm,yshift=-1.35cm]current page.north east);
  \end{tikzpicture}%
"""
    else:
        # Keep baked-in ISC18 fancyhdr numbers + headrule from TeX compile
        pagecmd = r""
    tex.write_text(
        rf"""\documentclass[10pt]{{article}}
\usepackage[papersize={{{PAGE_W_CM}cm,{PAGE_H_CM}cm}},margin=0pt]{{geometry}}
\usepackage{{pdfpages,tikz}}
\pagestyle{{empty}}
\begin{{document}}
\includepdf[pages=-,width=\paperwidth,height=\paperheight,pagecommand={{{pagecmd}}}]{{src.pdf}}
\end{{document}}
""",
        encoding="utf-8",
        newline="\n",
    )
    for _ in range(2):
        run_pdflatex(tex.name, work)
    produced = work / "norm.pdf"
    if produced.exists() and produced.stat().st_size > 5000:
        shutil.copy2(produced, out_pdf)
        return len(PdfReader(str(out_pdf)).pages)
    shutil.copy2(src, out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def stamp_continuous(src_pdf: Path, out_pdf: Path, start: int = 1) -> int:
    """Replace page digits in ISC18 LO/RE slots (PDF fallbacks only).

    Wipes header corners and paints the continuous number in the same band as
    ISC18 fancyhdr. Does NOT draw a new headrule — the included page already has one.
    """
    work = BUILD / "stamp" / f"s{start}_{src_pdf.stem}"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_pdf, work / "src.pdf")
    tex = work / "stamp.tex"
    tex.write_text(
        rf"""\documentclass[10pt,twoside]{{article}}
\usepackage[papersize={{{PAGE_W_CM}cm,{PAGE_H_CM}cm}},margin=0pt]{{geometry}}
\usepackage{{pdfpages,tikz,eso-pic}}
\pagestyle{{empty}}
\setcounter{{page}}{{{start}}}
\AddToShipoutPictureFG{{%
  \begin{{tikzpicture}}[remember picture,overlay]
    \fill[white]
      ([xshift=0.05cm,yshift=-0.05cm]current page.north west)
      rectangle ([xshift=3.6cm,yshift=-1.55cm]current page.north west);
    \fill[white]
      ([xshift=-0.05cm,yshift=-0.05cm]current page.north east)
      rectangle ([xshift=-3.6cm,yshift=-1.55cm]current page.north east);
    \ifodd\value{{page}}
      \node[anchor=west,inner sep=0pt,font=\fontsize{{10}}{{12}}\selectfont] at
        ([xshift=2.35cm,yshift=-1.32cm]current page.north west) {{\thepage}};
    \else
      \node[anchor=east,inner sep=0pt,font=\fontsize{{10}}{{12}}\selectfont] at
        ([xshift=-2.35cm,yshift=-1.32cm]current page.north east) {{\thepage}};
    \fi
  \end{{tikzpicture}}%
}}
\begin{{document}}
\includepdf[pages=-,width=\paperwidth,height=\paperheight,pagecommand={{}}]{{src.pdf}}
\end{{document}}
""",
        encoding="utf-8",
        newline="\n",
    )
    for _ in range(2):
        run_pdflatex(tex.name, work)
    produced = work / "stamp.pdf"
    shutil.copy2(produced, out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def merge_pdfs(paths: list[Path], out_pdf: Path) -> None:
    writer = PdfWriter()
    for p in paths:
        for page in PdfReader(str(p)).pages:
            writer.add_page(page)
    tmp = out_pdf.with_suffix(".tmp.pdf")
    with tmp.open("wb") as f:
        writer.write(f)
    try:
        if out_pdf.exists():
            out_pdf.unlink()
        tmp.replace(out_pdf)
    except PermissionError:
        alt = out_pdf.with_name(out_pdf.stem + "-new.pdf")
        tmp.replace(alt)
        print(f"NOTE: wrote {alt} (original locked)", flush=True)


def blank_page_pdf(out_pdf: Path) -> Path:
    """One blank booklet-sized page."""
    if out_pdf.exists() and out_pdf.stat().st_size > 500:
        return out_pdf
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    work = BUILD / "blank"
    work.mkdir(parents=True, exist_ok=True)
    tex = work / "blank.tex"
    tex.write_text(
        rf"""\documentclass[10pt]{{article}}
\usepackage[papersize={{{PAGE_W_CM}cm,{PAGE_H_CM}cm}},margin=0pt]{{geometry}}
\pagestyle{{empty}}
\begin{{document}}
\null
\end{{document}}
""",
        encoding="utf-8",
    )
    run_pdflatex(tex.name, work)
    shutil.copy2(work / "blank.pdf", out_pdf)
    return out_pdf


def resolve_main_tex(paper_id: str) -> Path | None:
    folder = MAIN / paper_id
    preferred = folder / f"{paper_id}.tex"
    if preferred.exists():
        return preferred
    if not folder.exists():
        return None
    texs = sorted(folder.rglob("*.tex"))
    texs = [t for t in texs if "__MACOSX" not in str(t)]
    return texs[0] if texs else None


def build_one_paper(paper_id: str) -> dict:
    """Compile + normalize one paper. Returns result dict."""
    out_final = PAPERS / f"{paper_id}.pdf"
    raw = BUILD / "raw" / f"{paper_id}.pdf"
    raw.parent.mkdir(parents=True, exist_ok=True)

    tex_path = resolve_main_tex(paper_id)
    method = ""
    ok = False
    detail = ""

    if tex_path is not None:
        # Always prefer author's own TeX (keeps figures, theorems, bib).
        # ISC18 wrap only as fallback — wrapping strips preamble and breaks images.
        ok, detail = compile_native(paper_id, tex_path, raw)
        method = "native"
        if not ok:
            ok, detail = compile_wrapper(paper_id, tex_path, raw)
            method = "wrap_fallback"

    if not ok:
        src = find_submission_pdf(paper_id)
        if src:
            shutil.copy2(src, raw)
            ok = True
            method = "pdf_fallback"
            detail = src.name
        else:
            return {
                "paper_id": paper_id,
                "ok": False,
                "method": method or "none",
                "detail": detail or "no tex/pdf",
                "pages": 0,
            }

    n = normalize_unnumbered(raw, out_final)
    return {
        "paper_id": paper_id,
        "ok": True,
        "method": method,
        "detail": detail,
        "pages": n,
        "pdf": str(out_final),
    }


def main() -> int:
    t0 = time.time()
    BUILD.mkdir(parents=True, exist_ok=True)
    PAPERS.mkdir(parents=True, exist_ok=True)
    (BUILD / "raw").mkdir(parents=True, exist_ok=True)

    records = json.loads((BOOK / "papers_metadata.json").read_text(encoding="utf-8"))
    papers = [r for r in records if r.get("status") in {"ok", "pdf_only"}]
    # also try no_tex if folder later gains tex — skip for now
    papers.sort(
        key=lambda r: (
            r.get("sort_key") or "zzz",
            (r.get("first_author") or "").casefold(),
            (r.get("title") or "").casefold(),
        )
    )

    print(f"Compiling {len(papers)} papers from TeX (workers={WORKERS})...", flush=True)
    results: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(build_one_paper, r["paper_id"]): r["paper_id"] for r in papers}
        done = 0
        for fut in as_completed(futs):
            pid = futs[fut]
            done += 1
            try:
                res = fut.result()
            except Exception as e:
                res = {"paper_id": pid, "ok": False, "method": "error", "detail": str(e), "pages": 0}
            results[pid] = res
            status = "OK" if res["ok"] else "FAIL"
            print(
                f"[{done}/{len(papers)}] {status} {pid} {res.get('method')} "
                f"pages={res.get('pages')} {res.get('detail','')}",
                flush=True,
            )

    built: list[tuple[dict, Path, int]] = []
    failed = []
    for r in papers:
        pid = r["paper_id"]
        res = results.get(pid) or {}
        pdf = PAPERS / f"{pid}.pdf"
        if res.get("ok") and pdf.exists():
            n = res["pages"] or len(PdfReader(str(pdf)).pages)
            built.append((r, pdf, n))
        else:
            failed.append(pid)

    print(f"Built {len(built)} / {len(papers)}; failed={failed}", flush=True)

    blank = blank_page_pdf(BUILD / "blank_page.pdf")

    # Merge with odd-page starts
    page_map: dict[str, int] = {}
    merge_list: list[Path] = []
    cursor = 1  # arabic page for next paper start (always odd)
    blanks_inserted = 0

    for i, (r, pdf, n) in enumerate(built):
        if cursor % 2 == 0:
            # should not happen if we pad correctly
            merge_list.append(blank)
            blanks_inserted += 1
            cursor += 1
        page_map[r["paper_id"]] = cursor
        merge_list.append(pdf)
        cursor += n
        # If another paper follows and next start would be even, pad
        if i < len(built) - 1 and (cursor % 2 == 0):
            merge_list.append(blank)
            blanks_inserted += 1
            cursor += 1

    unnumbered = BUILD / "papers_unnumbered.pdf"
    print(f"Merging {len(built)} papers + {blanks_inserted} blank pads...", flush=True)
    merge_pdfs(merge_list, unnumbered)

    numbered = BUILD / "papers_numbered.pdf"
    print("Stamping continuous page numbers (header RE/LO)...", flush=True)
    stamp_continuous(unnumbered, numbered, start=1)

    toc_pdf = BUILD / "toc.pdf"
    toc_pages = build_toc([r for r, _, _ in built], page_map, toc_pdf)
    # Pad TOC to even length so first paper starts on odd physical page of full booklet
    toc_parts = [toc_pdf]
    if toc_pages % 2 == 1:
        toc_parts.append(blank)
        toc_pages += 1
        print("Added blank after TOC so papers start on odd physical page", flush=True)

    full_candidates = [
        BOOK / "Full-English.pdf",
        BOOK / "Full-English-new.pdf",
        BOOK / "Full-English-from-tex.pdf",
    ]
    written = None
    for out in full_candidates:
        try:
            merge_pdfs(toc_parts + [numbered], out)
            # merge_pdfs may write -new on lock; check
            if out.exists():
                written = out
                break
        except Exception as e:
            print(f"write {out.name} failed: {e}", flush=True)

    # Prefer explicit from-tex name if primary locked oddly
    if written is None:
        written = BOOK / "Full-English-from-tex.pdf"
        merge_pdfs(toc_parts + [numbered], written)

    report = {
        "elapsed_sec": round(time.time() - t0, 1),
        "included": len(built),
        "failed": failed,
        "methods": {
            m: sum(1 for x in results.values() if x.get("method") == m)
            for m in sorted({x.get("method") for x in results.values()})
        },
        "blanks_between_papers": blanks_inserted,
        "arabic_pages": cursor - 1,
        "toc_pages": toc_pages,
        "full_pages": len(PdfReader(str(written)).pages) if written and written.exists() else None,
        "output": str(written),
        "page_map_sample": {k: page_map[k] for k in list(page_map)[:5]},
        "odd_starts_ok": all(page_map[r["paper_id"]] % 2 == 1 for r, _, _ in built),
    }
    (BOOK / "full_build_from_tex_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    (BUILD / "page_map.json").write_text(json.dumps(page_map, indent=2), encoding="utf-8")
    (BUILD / "compile_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
