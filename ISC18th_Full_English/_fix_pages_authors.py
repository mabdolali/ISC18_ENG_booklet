#!/usr/bin/env python3
"""
Rebuild Full-English.pdf + Abstract-Booklet-auto.pdf correctly:

Page numbers:
  - Compile each paper from LaTeX with EMPTY page style (no numbers)
  - PDF fallback: white-out old header/footer number strips
  - Measure lengths, build TOC by first-author last name
  - Stamp continuous arabic page numbers once on the merged papers PDF

Authors:
  - Extract full first-author name
  - Sort by first author's last name
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from pypdf import PdfReader, PdfWriter

ROOT = Path(r"i:\Booklet ENG")
BOOK = ROOT / "ISC18th_Full_English"
MAIN = BOOK / "main"
RAW = ROOT / "_extracted_raw"
BUILD = BOOK / "_build_final"
PAPERS_DIR = BUILD / "papers"
PDFLATEX = Path(r"C:\Users\Asus\AppData\Local\Programs\MiKTeX 2.9\miktex\bin\x64\pdflatex.exe")

PLACEHOLDER_AUTHORS = {
    "short authors' names", "first author name", "second author name",
    "author name", "authors", "short authors names",
}


def read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1256", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def soft_clean(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"%.*?$", "", s, flags=re.M)
    s = re.sub(r"\\footnote\*?(\[[^\]]*\])?\{.*?\}", "", s, flags=re.S)
    s = re.sub(r"\\thanks\*?(\[[^\]]*\])?\{.*?\}", "", s, flags=re.S)
    s = re.sub(r"\\textsuperscript\s*\{[^}]*\}", "", s)
    s = re.sub(r"\$\^?\{?[^}]*\}?\$", "", s)
    s = re.sub(r"\^\{[^}]*\}", "", s)
    s = re.sub(r"\^[a-zA-Z0-9*,\\]+", "", s)
    s = re.sub(r"\$[^$]*\$", "", s)
    s = re.sub(r"\\(?:mathscr|mathcal|mathrm|textbf|textit|emph|tt|small|em)\s*\{([^}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", s)
    s = s.replace("\\\\", " ").replace("\\", " ")
    s = re.sub(r"[{}$~]", " ", s)
    s = re.sub(r"\bCorresponding Authou?r\b.*", "", s, flags=re.I)
    s = re.sub(r"\bCorresponding Authur\b.*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,;.")
    s = re.sub(r"\s*\)\s*", " ", s).strip(" ,;.")
    return s


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


def brace_content(text: str, start: int) -> str | None:
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    i = start
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
    return None


def find_command_arg(text: str, cmd: str) -> str:
    for m in re.finditer(rf"(?<!@)\\{cmd}\s*\{{", text):
        arg = brace_content(text, m.end() - 1)
        if not arg or "@title" in arg or "@author" in arg or len(arg.strip()) < 3:
            continue
        return arg
    return ""


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
    meta = {k: "" for k in (
        "title", "authors", "abstract", "keywords", "msc",
        "header_title", "header_authors",
    )}
    hm = re.search(r"\\fancyhead\[LE\]\{(.*?)\}", text, re.S)
    if hm:
        meta["header_title"] = soft_clean(hm.group(1))
    hm = re.search(r"\\fancyhead\[RO\]\{(.*?)\}", text, re.S)
    if hm:
        ha = soft_clean(hm.group(1))
        if ha.lower() not in PLACEHOLDER_AUTHORS:
            # Reject title-like headers (no author separators, long phrase)
            if not (
                len(ha.split()) >= 4
                and "," not in ha
                and " and " not in ha.lower()
                and "et al" not in ha.lower()
            ):
                meta["header_authors"] = ha

    title = ""
    for m in re.finditer(
        r"\{\s*\\(?:Large|LARGE|large|huge)\s*\\(?:bf|bfseries)\s*(.*?)\}", text, re.S
    ):
        cand = soft_clean(m.group(1))
        if "@title" not in m.group(1) and len(cand) >= 8 and cand.lower() != "article title":
            title = m.group(1)
            break
    if not title:
        title = find_command_arg(text, "title")
    if not title:
        m = re.search(r"\\begin\{document\}(.*)$", text, re.S)
        body = m.group(1) if m else text
        sm = re.search(r"\\section\*?\{([^}]{15,200})\}", body)
        if sm:
            title = sm.group(1)
    meta["title"] = soft_clean(title or meta["header_title"])

    authors = find_command_arg(text, "author")
    if authors:
        authors = re.sub(r"\$\^\s*\{?\d+\}?\$", ", ", authors)
        authors = re.sub(r"\\textsuperscript\s*\{[^}]*\}", ", ", authors)
    # Working copy without footnotes so {\bf Name\footnote{...}} parses correctly
    text_nofn = re.sub(r"\\footnote\*?(\[[^\]]*\])?\{.*?\}", "", text, flags=re.S)
    text_nofn = re.sub(r"\\thanks\*?(\[[^\]]*\])?\{.*?\}", "", text_nofn, flags=re.S)
    if not authors:
        for m in re.finditer(r"\{\\bf\s*", text_nofn):
            arg = brace_content(text_nofn, m.start())  # full {\bf ...} content including braces start
            # brace_content expects start at '{', m.start() is '{'
            if arg is None:
                continue
            # drop leading \bf
            cand = re.sub(r"^\\bf\s*", "", arg)
            low = cand.lower()
            if any(k in low for k in (
                "abstract", "keyword", "mathematics subject", "classification",
                "corresponding author", "email",
            )):
                continue
            cleaned = soft_clean(cand)
            if cleaned.lower() in PLACEHOLDER_AUTHORS:
                continue
            if len(cleaned) >= 5 and re.search(r"[A-Za-z]{3,}", cleaned):
                if re.search(r"\b(dataset|introduction|theorem|lemma)\b", cleaned, re.I):
                    continue
                authors = re.sub(r"\$\^\s*\{?\d+\}?\$", ", ", cand)
                authors = re.sub(r"\\textsuperscript\s*\{[^}]*\}", ", ", authors)
                break
    if not authors:
        m = re.search(
            r"\\noindent\s*\\textbf\{[^}]{10,200}\}\s*\\noindent\s+([A-Za-z].{3,120}?)(?:\\footnote|\\noindent|\\par|\n\n)",
            text,
            re.S,
        )
        if m:
            authors = m.group(1)

    authors_clean = soft_clean(authors)
    authors_clean = re.sub(r"\bCorresponding Authou?r\b.*", "", authors_clean, flags=re.I)
    authors_clean = re.sub(r"\bCorresponding Authur\b.*", "", authors_clean, flags=re.I)
    authors_clean = re.sub(r"\s*,\s*,+", ", ", authors_clean).strip(" ,;")
    if len(authors_clean) > 240:
        authors_clean = re.split(
            r"\b(Department|Faculty|University|School|Institute|P\.O\.|College)\b",
            authors_clean, maxsplit=1,
        )[0].strip(" ,;")
    authors_clean = re.sub(r"\S+@\S+", "", authors_clean)
    authors_clean = re.sub(r"\b\w*_\w+\b", "", authors_clean)  # drop email-like n_moradi tokens
    authors_clean = re.sub(r"\s+", " ", authors_clean).strip(" ,;")
    # Keep only the name line (before department / affiliation leftovers)
    authors_clean = re.split(r"\b(Faculty of|Department of)\b", authors_clean, maxsplit=1)[0].strip(" ,;")

    header_ok = meta["header_authors"] and meta["header_authors"].lower() not in PLACEHOLDER_AUTHORS
    bad_body = (
        not authors_clean
        or authors_clean.lower() in PLACEHOLDER_AUTHORS
        or "Convergence" in authors_clean
        or "Mathematics Subject" in authors_clean
        or len(authors_clean) < 3
        or authors_clean.lower() in {"d", "dataset"}
    )
    if bad_body:
        authors_clean = meta["header_authors"] if header_ok else ""
    if "Mathematics Subject" in (authors_clean or ""):
        authors_clean = ""

    meta["authors"] = authors_clean
    abstract = ""
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", text, re.I | re.S)
    if m:
        abstract = m.group(1)
    if not abstract:
        abstract = extract_between(
            text,
            r"(?:\\noindent\s*\{\\bf\s*Abstract:?\s*\}|\\noindent\s*\\textbf\{Abstract\}|"
            r"\{\\bf\s*Abstract:?\s*\}|\\textbf\{Abstract\}|Abstract:)\s*(?:\\\\)?\s*",
            [
                r"Keywords?:", r"\\keyword\b", r"Mathematics Subject Classification",
                r"\\section\b", r"\\rule\{\\textwidth\}",
            ],
        )
    if not abstract:
        m = re.search(
            r"Article abstract[^\n]*\n+(.*?)\n\\noindent\{\\bf\s*Keywords",
            text, re.S | re.I,
        )
        if m:
            abstract = re.sub(r"^\\noindent\{\\bf\s*", "", m.group(1))
    meta["abstract"] = soft_clean(abstract)
    meta["keywords"] = soft_clean(extract_between(
        text,
        r"(?:Keywords?:\s*|\\keyword\s*|\\textbf\{Keywords?:\}|\{\\bf\s*Keywords?:\s*\})",
        [r"Mathematics Subject Classification", r"\\section\b", r"\\rule\{\\textwidth\}", r"\\begin\{"],
    ))
    msc = soft_clean(extract_between(
        text,
        r"Mathematics Subject Classification\s*\([^)]*\):\s*",
        [r"\\section\b", r"\\rule\{\\textwidth\}", r"\\newpage", r"\\begin\{"],
    ))
    meta["msc"] = re.sub(r"^rm\s*", "", msc, flags=re.I)
    return meta


def first_author_lastname(authors: str, header_authors: str = "") -> tuple[str, str]:
    """
    Returns (sort_key_lastname, full_first_author_display).
    Sort key = last name of the first author only.
    """
    src = soft_clean(authors or header_authors or "")
    src = re.sub(r"Corresponding Authou?r.*", "", src, flags=re.I)
    src = re.sub(r"Corresponding Authur.*", "", src, flags=re.I)
    src = src.strip(" ,;")
    if not src or src.lower() in PLACEHOLDER_AUTHORS:
        return ("zzz", "")
    if re.search(r"mathematics subject|classification \(|robust time series clustering", src, re.I):
        src = soft_clean(header_authors or "")
        if not src or src.lower() in PLACEHOLDER_AUTHORS:
            return ("zzz", "")

    chunk = src
    for sep in [r"\bet\s+al\.?\b", r"\s+and\s+", r"\s+\&\s+", r"\s*;\s*"]:
        parts = re.split(sep, chunk, maxsplit=1, flags=re.I)
        if len(parts) > 1:
            chunk = parts[0].strip(" ,;")
            break

    chunk = re.sub(r"\s+", " ", chunk).strip(" ,;")
    chunk = re.sub(r"[\d*,]+$", "", chunk).strip(" ,;")

    # Glued initial+surname: Z.Esfandiar / N.Sanjari → use surname after the dot
    glued = re.match(r"^([A-Za-z])\.([A-Za-z][A-Za-z\-']+)$", chunk)
    if glued:
        return (glued.group(2).casefold(), f"{glued.group(1).upper()}. {glued.group(2)}")

    # Surname, Initials  (keep together)
    if re.match(r"^[^,]+,\s*([A-Z]\.?\s*)+$", chunk):
        surname = chunk.split(",", 1)[0].strip()
        return (surname.casefold(), chunk)

    # "Morteza Amini, Mohammad Arashi" -> first person only
    if "," in chunk:
        chunk = re.split(r",\s*", chunk, maxsplit=1)[0].strip()

    tokens = [
        t for t in chunk.split()
        if t.lower() not in {"dr.", "dr", "prof.", "prof", "mr.", "ms.", "mrs."}
        and not re.fullmatch(r"[\d*,]+", t)
    ]
    if not tokens:
        return ("zzz", "")
    alpha_names = [t.strip(" .,") for t in tokens if re.search(r"[A-Za-z]{2,}", t)]
    if not alpha_names:
        return ("zzz", " ".join(tokens))
    surname = alpha_names[-1]
    display = " ".join(tokens)
    return (surname.casefold(), display)


# Papers compiled WITHOUT page numbers
WRAPPER = r"""\documentclass[10pt,twoside]{article}
\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{amsmath,amssymb,amsthm,amsfonts,mathrsfs,mathtools,bm}
\usepackage{graphicx,xcolor,tikz,booktabs,multirow,tabularx,array,float,colortbl}
\usepackage{caption,subcaption,enumitem,setspace,verbatim,rotating,lscape}
\usepackage{natbib,hyperref,geometry,xspace,pdflscape}
\usepackage{algorithm,algpseudocode,epstopdf}
\geometry{papersize={16.5cm,23.5cm},top=1.8cm,bottom=1.6cm,left=1.5cm,right=1.5cm}
\hypersetup{colorlinks=true,linkcolor=blue,citecolor=magenta,urlcolor=cyan}
\pagestyle{empty}
\newtheorem{dfn}{Definition}[section]
\newtheorem{thm}[dfn]{Theorem}
\newtheorem{pro}[dfn]{Proposition}
\newtheorem{rem}[dfn]{Remark}
\newtheorem{lem}[dfn]{Lemma}
\newtheorem{cor}[dfn]{Corollary}
\newtheorem{eg}[dfn]{Example}
\newtheorem{alg}{Algorithm}
\newtheorem{definition}[dfn]{Definition}
\newtheorem{theorem}[dfn]{Theorem}
\newtheorem{lemma}[dfn]{Lemma}
\newtheorem{proposition}[dfn]{Proposition}
\newtheorem{corollary}[dfn]{Corollary}
\newtheorem{remark}[dfn]{Remark}
\newtheorem{example}[dfn]{Example}
\renewenvironment{proof}{\noindent{\bf Proof. }}{\hfill{$\Box$}\\}
\renewcommand{\bibname}{References}
\numberwithin{equation}{section}
\graphicspath{{./}{main/PAPERID/}{main/PAPERID/Images/}{main/PAPERID/figures/}{Images/}}
\makeatletter
\def\@maketitle{%
  \begin{center}\vspace*{0.4cm}
  {\Large\bfseries \@title\par}\vspace{0.55em}
  {\bfseries \@author\par}\vspace{0.3em}
  \end{center}\noindent\rule{\textwidth}{0.2mm}\\[0.3em]}
\makeatother
\renewcommand{\thispagestyle}[1]{}
\renewcommand{\pagestyle}[1]{}
\begin{document}
\pagestyle{empty}
BODY
\end{document}
"""


def sanitize_body(tex: str, paper_id: str) -> str:
    title = find_command_arg(tex, "title")
    author = find_command_arg(tex, "author")
    m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", tex, re.S | re.I)
    body = m.group(1) if m else tex
    body = re.sub(r"\\input\{[^}]*ISC18[^}]*\}\s*", "", body, flags=re.I)
    body = re.sub(r"\\usepackage(\[[^\]]*\])?\{[^}]*ISC18[^}]*\}\s*", "", body, flags=re.I)
    body = re.sub(r"\\documentclass\b.*?\n", "", body)
    body = re.sub(r"\\hypertarget\{[^}]*\}\{%\s*", "", body)
    body = re.sub(r"\\label\{[^}]*\}\}", "", body)
    body = re.sub(r"\\fancyhead\[[^\]]*\]\{.*?\}\s*", "", body, flags=re.S)
    body = re.sub(r"\\pagestyle\{.*?\}\s*", "", body)
    body = re.sub(r"\\thispagestyle\{.*?\}\s*", "", body)
    body = re.sub(r"(?m)^\s*\\\\\s*$", "", body)

    def fix_inc(mm: re.Match) -> str:
        opts = mm.group(1) or ""
        path = mm.group(2).strip().replace("\\", "/")
        if path.startswith("main/"):
            return f"\\includegraphics{opts}{{{path}}}"
        if re.search(r"isc18e", path, re.I) or path.lower() in {
            "images/isc18e", "images/isc18e.jpg", "logo.jpg",
        }:
            return f"\\includegraphics{opts}{{Images/isc18E}}"
        return f"\\includegraphics{opts}{{main/{paper_id}/{path}}}"

    body = re.sub(r"\\includegraphics(\[[^\]]*\])?\{([^}]*)\}", fix_inc, body)
    # Ensure each first page has the conference header image.
    # If a submission omitted the header, prepend it here.
    header_present = re.search(r"isc18e|Images/isc18E|logo\.jpg", body, re.I) is not None
    if not header_present:
        header_block = (
            r"\begin{center}"
            r"{\includegraphics[height=25mm,width=150.3mm]{Images/isc18E}}"
            r"\vspace*{-1.8cm}"
            r"\end{center}"
        )
        body = header_block + "\n" + body.lstrip()
    bits = []
    if title:
        bits.append(f"\\title{{{title}}}")
    if author:
        bits.append(f"\\author{{{author}}}")
    return "\n".join(bits) + "\n" + body.strip() + "\n"


def run_pdflatex(args: list[str], cwd: Path) -> None:
    subprocess.run(
        [str(PDFLATEX), "-interaction=nonstopmode", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def compile_paper_unnumbered(paper_id: str, out_pdf: Path) -> tuple[bool, int]:
    tex_path = MAIN / paper_id / f"{paper_id}.tex"
    if not tex_path.exists():
        return False, 0
    work = BUILD / "compile" / paper_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    body = sanitize_body(read_text(tex_path), paper_id)
    src = work / f"{paper_id}.tex"
    src.write_text(WRAPPER.replace("PAPERID", paper_id).replace("BODY", body), encoding="utf-8")
    for _ in range(2):
        run_pdflatex([f"-output-directory={work}", str(src)], BOOK)
    produced = work / f"{paper_id}.pdf"
    if not produced.exists() or produced.stat().st_size < 4000:
        return False, 0
    try:
        n = len(PdfReader(str(produced)).pages)
    except Exception:
        return False, 0
    if n <= 0:
        return False, 0
    shutil.copy2(produced, out_pdf)
    return True, n


def find_submission_pdf(paper_id: str) -> Path | None:
    num = paper_id.split(".", 1)[1]
    cands: list[tuple[int, Path]] = []
    for root in [RAW / num, MAIN / paper_id]:
        if not root.exists():
            continue
        for p in root.rglob("*.pdf"):
            low = str(p).lower().replace("\\", "/")
            if "__macosx" in low:
                continue
            size = p.stat().st_size
            if size < 100_000:
                continue
            name = p.name.lower()
            if name.endswith("-eps-converted-to.pdf"):
                continue
            if re.search(r"(^|[_-])(fig|figure|plot|image|graph|chart|preview)([_-]|\d|\.|$)", name):
                if "template" not in name and "isc18" not in name:
                    continue
            score = size
            if any(k in name for k in ("template", "isc18", "manuscript", "paper", "main", "english")):
                score += 5_000_000
            cands.append((score, p))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1]


def normalize_pdf_unnumbered(src: Path, out_pdf: Path) -> int:
    """Include PDF pages, white-out old page-number strips, no new numbers yet."""
    work = BUILD / "normalize" / src.stem
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, work / "src.pdf")
    tex = work / "norm.tex"
    tex.write_text(
        r"""\documentclass[10pt]{article}
\usepackage[papersize={16.5cm,23.5cm},margin=0pt]{geometry}
\usepackage{graphicx}
\usepackage{pdfpages,tikz}
\pagestyle{empty}
\begin{document}
\includepdf[pages=-,fitpaper,pagecommand={%
  \begin{tikzpicture}[remember picture,overlay]
    \fill[white] ([yshift=0.15cm]current page.south west) rectangle ([yshift=1.15cm]current page.south east);
    \fill[white] ([yshift=-0.15cm]current page.north west) rectangle ([yshift=-1.05cm]current page.north east);
    \node[inner sep=0pt] at (current page.center) {\includegraphics[height=25mm,width=150.3mm]{../../../Images/isc18E}};
  \end{tikzpicture}%
}]{src.pdf}
\end{document}
""",
        encoding="utf-8",
    )
    for _ in range(2):
        run_pdflatex([f"-output-directory={work}", str(tex)], work)
    produced = work / "norm.pdf"
    if produced.exists() and produced.stat().st_size > 5000:
        shutil.copy2(produced, out_pdf)
        return len(PdfReader(str(out_pdf)).pages)
    shutil.copy2(src, out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def stamp_continuous_numbers(src_pdf: Path, out_pdf: Path, start: int = 1) -> int:
    """Add continuous arabic page numbers to an unnumbered papers PDF."""
    work = BUILD / "stamp_all"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_pdf, work / "src.pdf")
    tex = work / "stamp.tex"
    tex.write_text(
        rf"""\documentclass[10pt,twoside]{{article}}
\usepackage[papersize={{16.5cm,23.5cm}},margin=0pt]{{geometry}}
\usepackage{{pdfpages,fancyhdr}}
\pagestyle{{fancy}}
\fancyhf{{}}
\renewcommand{{\headrulewidth}}{{0pt}}
\fancyfoot[C]{{\thepage}}
\setcounter{{page}}{{{start}}}
\begin{{document}}
\includepdf[pages=-,fitpaper,pagecommand={{\thispagestyle{{fancy}}}}]{{src.pdf}}
\end{{document}}
""",
        encoding="utf-8",
    )
    for _ in range(2):
        run_pdflatex([f"-output-directory={work}", str(tex)], work)
    produced = work / "stamp.pdf"
    shutil.copy2(produced, out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def build_toc(records, page_map, out_pdf: Path) -> int:
    lines = [
        r"\documentclass[10pt,twoside]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{times}",
        r"\usepackage[papersize={16.5cm,23.5cm},top=2cm,bottom=1.5cm,left=1.5cm,right=1.5cm]{geometry}",
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
        authors = latex_escape(r.get("authors") or "")
        first = latex_escape(r.get("first_author") or "")
        pg = page_map[r["paper_id"]]
        lines += [
            rf"\noindent{{\bfseries {title}}}\dotfill {pg}\\",
            rf"{{\small\itshape {authors}}}\\",
            rf"{{\footnotesize First author: {first}}}\\[0.45em]",
            "",
        ]
    lines.append(r"\end{document}")
    toc_tex = BUILD / "toc_src.tex"
    toc_tex.write_text("\n".join(lines), encoding="utf-8")
    for _ in range(2):
        run_pdflatex(["-jobname=toc_out", toc_tex.name], BUILD)
    shutil.copy2(BUILD / "toc_out.pdf", out_pdf)
    return len(PdfReader(str(out_pdf)).pages)


def build_abstract(records, out_pdf: Path) -> int:
    # Write to a temp name first to avoid file locks on open PDF
    tmp_tex = BUILD / "abstract_build.tex"
    tmp_pdf = BUILD / "abstract_build.pdf"
    lines = [
        r"\documentclass[10pt,twoside]{article}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage[utf8]{inputenc}",
        r"\usepackage{times,microtype}",
        r"\usepackage[papersize={16.5cm,23.5cm},top=1.5cm,bottom=1.3cm,left=1.5cm,right=1.5cm]{geometry}",
        r"\usepackage{fancyhdr}",
        r"\pagestyle{fancy}\fancyhf{}",
        r"\renewcommand{\headrulewidth}{0.2pt}",
        r"\fancyhead[LE,RO]{\thepage}",
        r"\fancyhead[RE]{\footnotesize Abstract Booklet}",
        r"\fancyhead[LO]{\footnotesize ISC18}",
        r"\begin{document}",
        r"\begin{center}",
        r"{\Large\bfseries 18th Iranian Statistical Conference}\\[0.3em]",
        r"{\large Abstract Booklet (English)}\\[0.3em]",
        r"{\small\itshape Sorted by last name of first author}",
        r"\end{center}",
        r"\newpage",
        "",
    ]
    for r in records:
        title = latex_escape(r.get("title") or r["paper_id"])
        authors = latex_escape(r.get("authors") or "")
        first = latex_escape(r.get("first_author") or "")
        abstract = latex_escape(r.get("abstract") or "")
        keywords = latex_escape(r.get("keywords") or "")
        msc = latex_escape(r.get("msc") or "")
        lines += [
            f"% {r['paper_id']}",
            r"\noindent\begin{minipage}[t][\textheight]{\textwidth}",
            rf"\noindent{{\bfseries\large {title}}}\\[0.35em]",
            rf"{{\itshape {authors}}}\\[0.2em]",
            rf"{{\footnotesize First author: {first}}}\\[0.45em]",
            r"\noindent\rule{\textwidth}{0.3pt}\\[0.4em]",
            r"{\footnotesize " + abstract + r"}\\[0.4em]",
        ]
        if keywords:
            lines.append(r"{\footnotesize\bfseries Keywords:} {\footnotesize " + keywords + r"}\\[0.15em]")
        if msc:
            lines.append(r"{\footnotesize\bfseries MSC:} {\footnotesize " + msc + r"}\\")
        lines += [r"\vfill", r"\end{minipage}", r"\newpage", ""]
    lines.append(r"\end{document}")
    tmp_tex.write_text("\n".join(lines), encoding="utf-8")
    for _ in range(2):
        run_pdflatex([tmp_tex.name], BUILD)
    # also copy tex into booklet folder
    shutil.copy2(tmp_tex, BOOK / "Abstract-Booklet-auto.tex")
    # replace destination even if locked by writing temp then replace
    target = out_pdf
    try:
        if target.exists():
            target.unlink()
    except PermissionError:
        target = BOOK / "Abstract-Booklet-auto-new.pdf"
    shutil.copy2(tmp_pdf, target)
    if target != out_pdf:
        print(f"NOTE: wrote {target} because {out_pdf} was locked")
    return len(PdfReader(str(tmp_pdf)).pages)


def merge_pdfs(paths, out_pdf: Path) -> None:
    writer = PdfWriter()
    for p in paths:
        for page in PdfReader(str(p)).pages:
            writer.add_page(page)
    # write via temp to avoid locks
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
        print(f"NOTE: wrote {alt} because {out_pdf} was locked")


def refresh_metadata():
    meta_path = BOOK / "papers_metadata.json"
    records = json.loads(meta_path.read_text(encoding="utf-8"))
    for r in records:
        tex = MAIN / r["paper_id"] / f"{r['paper_id']}.tex"
        if tex.exists() and r.get("status") == "ok":
            parsed = parse_metadata(read_text(tex))
            for k, v in parsed.items():
                if not v:
                    continue
                old = (r.get(k) or "")
                if k == "authors":
                    if (
                        not old
                        or old.lower() in PLACEHOLDER_AUTHORS
                        or "Convergence" in old
                        or "^" in old
                        or "Corresponding Author" in old
                        or len(v) > 0
                    ):
                        if v.lower() not in PLACEHOLDER_AUTHORS:
                            r[k] = v
                elif k == "title" and len(v) >= 8:
                    r[k] = v
                elif k in {"abstract", "keywords", "msc"} and (not old or len(v) >= len(old)):
                    r[k] = v
                elif not old:
                    r[k] = v
        authors = r.get("authors") or ""
        if authors.lower() in PLACEHOLDER_AUTHORS or "^" in authors:
            authors = r.get("header_authors") or authors
            r["authors"] = authors
        key, first = first_author_lastname(authors, r.get("header_authors") or "")
        r["sort_key"] = key
        r["first_author"] = first
        r["first_author_lastname"] = key

    meta_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = [
        "paper_id", "status", "sort_key", "first_author_lastname", "first_author",
        "title", "authors", "abstract", "keywords", "msc",
        "header_title", "header_authors", "main_tex", "source_archive", "notes",
    ]
    with (BOOK / "papers_metadata.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in records:
            row = {k: r.get(k, "") for k in fields}
            notes = r.get("notes") or ""
            row["notes"] = " | ".join(notes) if isinstance(notes, list) else notes
            w.writerow(row)
    return records


def main() -> int:
    t0 = time.time()
    BUILD.mkdir(parents=True, exist_ok=True)
    # Header injection changed; regenerate cached per-paper PDFs too.
    if PAPERS_DIR.exists():
        shutil.rmtree(PAPERS_DIR, ignore_errors=True)
    PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    # Clean only derived outputs (paper cache cleared above)
    for name in ("stamp_all", "normalize", "toc_src.tex", "toc_out.pdf", "toc.pdf",
                 "papers_unnumbered.pdf", "papers_numbered.pdf", "abstract_build.tex",
                 "abstract_build.pdf"):
        p = BUILD / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink(missing_ok=True)

    img = BOOK / "Images"
    img.mkdir(exist_ok=True)
    if not any(img.glob("isc18E*")):
        for cand in MAIN.rglob("isc18E*"):
            shutil.copy2(cand, img / cand.name)
            break

    records = refresh_metadata()
    papers = [r for r in records if r.get("status") in {"ok", "pdf_only"}]
    papers.sort(key=lambda r: (r.get("sort_key") or "zzz", (r.get("first_author") or "").casefold(), (r.get("title") or "").casefold()))

    print("Sort preview (lastname | first author):")
    for r in papers[:12]:
        print(f"  {r.get('sort_key')!r:20} | {r.get('first_author')!r}")

    built = []
    sources = {}
    failed = []

    for i, r in enumerate(papers, 1):
        pid = r["paper_id"]
        out = PAPERS_DIR / f"{pid}.pdf"
        print(f"[{i}/{len(papers)}] {pid} ({r.get('first_author')})", flush=True)
        # Reuse previously compiled unnumbered PDF if present
        if out.exists() and out.stat().st_size > 4000:
            try:
                n = len(PdfReader(str(out)).pages)
                if n > 0:
                    sources[pid] = "cached"
                    built.append((r, out, n))
                    print(f"    cached pages={n}")
                    continue
            except Exception:
                pass
        tex = MAIN / pid / f"{pid}.tex"
        ok, n = False, 0
        if tex.exists() and r.get("status") == "ok":
            ok, n = compile_paper_unnumbered(pid, out)
            if ok:
                sources[pid] = "latex"
                print(f"    latex pages={n}")
        if not ok:
            src = find_submission_pdf(pid)
            if src:
                n = normalize_pdf_unnumbered(src, out)
                sources[pid] = f"pdf:{src.name}"
                print(f"    pdf-fallback {src.name} pages={n}")
                ok = True
            else:
                failed.append(pid)
                print("    FAILED")
                continue
        built.append((r, out, n))

    # Page map from measured lengths (arabic starts at 1)
    page_map = {}
    cursor = 1
    for r, _, n in built:
        page_map[r["paper_id"]] = cursor
        cursor += n
    print(f"Arabic pages total={cursor-1}, included={len(built)}, failed={failed}")

    # Merge unnumbered papers, then stamp continuous numbers
    unnumbered = BUILD / "papers_unnumbered.pdf"
    merge_pdfs([p for _, p, _ in built], unnumbered)
    numbered = BUILD / "papers_numbered.pdf"
    stamp_continuous_numbers(unnumbered, numbered, start=1)
    print(f"Stamped pages={len(PdfReader(str(numbered)).pages)}")

    toc_pdf = BUILD / "toc.pdf"
    toc_pages = build_toc([r for r, _, _ in built], page_map, toc_pdf)
    print(f"TOC pages={toc_pages}")

    full_out = BOOK / "Full-English.pdf"
    merge_pdfs([toc_pdf, numbered], full_out)

    abs_pages = build_abstract([r for r, _, _ in built], BOOK / "Abstract-Booklet-auto.pdf")

    report = {
        "elapsed_sec": round(time.time() - t0, 1),
        "included": len(built),
        "failed": failed,
        "from_latex": sum(1 for s in sources.values() if s == "latex"),
        "from_pdf_fallback": sum(1 for s in sources.values() if str(s).startswith("pdf:")),
        "arabic_pages": cursor - 1,
        "toc_pages": toc_pages,
        "full_pages": len(PdfReader(str(full_out)).pages) if full_out.exists() else None,
        "abstract_pages": abs_pages,
        "abstract_expected": 1 + len(built),
        "sort": "first_author_lastname",
        "page_numbering": "stamped_continuous_after_unnumbered_compile",
    }
    (BOOK / "full_build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    # verify sort sample in metadata
    print("\nFirst 8 by last name:")
    for r, _, _ in built[:8]:
        print(f"  {r['sort_key']}: {r['first_author']} — {r.get('title','')[:50]}")
    return 0 if built else 1


if __name__ == "__main__":
    raise SystemExit(main())
