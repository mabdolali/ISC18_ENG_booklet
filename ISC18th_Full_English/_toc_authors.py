#!/usr/bin/env python3
"""Extract / resolve first-author names for the booklet TOC.

Hand edits
----------
Edit ``toc_author`` (and optionally ``sort_key``) in papers_metadata.json.
Set ``toc_manual`` to true so rebuilds never overwrite that name:

  {
    "paper_id": "p.1038",
    "toc_author": "Mohsen Mohammadzadeh",
    "sort_key": "mohammadzadeh",
    "toc_manual": true,
    ...
  }

Then rerun::

  python rebuild_all.py --skip-ingest
  # or only remesh TOC:
  python ISC18th_Full_English/_remesh_toc.py
"""

from __future__ import annotations

import re
from pathlib import Path

_TITLE_PREFIX_RE = re.compile(
    r"^(?:dr\.?|prof\.?|professor|mr\.?|mrs\.?|ms\.?|miss|eng\.?|engineer|sir|phd)\s+",
    re.I,
)
_NAME_JUNK_RE = re.compile(
    r"(?i)\b(?:orcid\s*:?\s*[\d-]+|corresponding\s+authou?r.*|email\s*:.*|"
    r"department\b.*|university\b.*|faculty\b.*)$"
)
_INITIAL_TOKEN_RE = re.compile(r"^[A-Za-z]\.?$")
_SKIP_RE = re.compile(
    r"(?i)\b(Abstract|Keywords?|Corresponding Author|Iranian Statistical|"
    r"Article title|MSC|Classification|Short article|Short authors|"
    r"Mathematics Subject|How to write|Proof\.|Theorem|Lemma|Modified LINEX)\b"
)


def balanced_arg(text: str, open_idx: int) -> str:
    if open_idx < 0 or open_idx >= len(text) or text[open_idx] != "{":
        return ""
    depth = 0
    i = open_idx
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i]
        i += 1
    return ""


def strip_titles(s: str) -> str:
    s = (s or "").strip(" ,;.|%")
    s = s.replace(r"\ ", " ").replace(r"\&", "&")
    s = re.sub(r"\s+", " ", s)
    while True:
        nxt = _TITLE_PREFIX_RE.sub("", s).strip(" ,;.|%")
        if nxt == s:
            break
        s = nxt
    return s


def tex_to_plain(s: str) -> str:
    # Footnotes with nested braces
    while True:
        m = re.search(r"\\footnote\*?(\[[^\]]*\])?\{", s)
        if not m:
            break
        start_brace = m.end() - 1
        arg = balanced_arg(s, start_brace)
        end = start_brace + len(arg) + 1 if arg else start_brace
        s = s[: m.start()] + " " + s[end + 1 :]
    s = re.sub(r"(?is)\\thanks\{.*?\}", " ", s)
    s = re.sub(r"(?is)\\textsuperscript\{.*?\}", " ", s)
    s = re.sub(r"(?is)\\footnotemark\b(\[[^\]]*\])?", " ", s)
    s = re.sub(r"(?is)\\tt\s*\{[^}]*\}", " ", s)
    s = re.sub(r"(?is)\\texttt\{[^}]*\}", " ", s)
    s = re.sub(r"(?is)\\url\{[^}]*\}", " ", s)
    s = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", s)
    s = s.replace(r"\_", " ").replace("_", " ")
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?", " ", s)
    s = re.sub(r"[{}]", " ", s)
    s = re.sub(r"[*$†‡§¶\\]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" ,;.|%")


def fullness(name: str) -> int:
    if not name or _SKIP_RE.search(name):
        return -1
    tokens = [t for t in re.split(r"[\s,]+", name) if t]
    score = 0
    for t in tokens:
        core = t.strip(".")
        if not core or not re.search(r"[A-Za-z]", core):
            continue
        if _INITIAL_TOKEN_RE.match(t) or len(core) == 1:
            continue
        if re.fullmatch(r"(?:[A-Za-z]\.){2,}", t.replace(" ", "")):
            continue
        if re.match(r"^[A-Za-z]\.[A-Za-z]", t):
            score += max(1, len(core) - 2)
        else:
            score += max(2, len(core))
    return score


def mostly_initials(name: str) -> bool:
    tokens = [t for t in re.split(r"[\s,]+", name or "") if t]
    if not tokens:
        return True
    body = tokens[:-1] if len(tokens) > 1 else tokens
    full = 0
    for t in body:
        core = t.strip(".")
        if len(core) >= 3 and core.isalpha() and not re.match(r"^[A-Za-z]\.", t):
            full += 1
    return full == 0


def looks_like_name(name: str) -> bool:
    if not name or not (3 <= len(name) <= 80):
        return False
    if _SKIP_RE.search(name):
        return False
    if "@" in name:
        return False
    if re.search(r"(?i)\b(department|departement|university|faculty|http|www)\b", name):
        return False
    if not re.search(r"[A-Za-z]{2,}", name):
        return False
    if re.search(
        r"(?i)\b(the|for|with|from|this|that|which|when|under|into|over|"
        r"but|lastly|finally|however|therefore|sampling scheme|in rss|in jps)\b",
        name,
    ):
        if re.match(r"(?i)^(but|lastly|in )\b", name) or re.search(
            r"(?i)\b(sampling scheme|in rss|in jps)\b", name
        ):
            return False
    if name.count(" ") >= 5 and "," not in name:
        return False
    if re.search(
        r"(?i)\b(performance|estimation|approach|clustering|regression|"
        r"distribution|algorithm|framework|analysis of|based on|"
        r"liu-type|elliptical|smooth estimation|cumulative)\b",
        name,
    ):
        return False
    if not re.fullmatch(r"[A-Za-z.\-]+(?:\s+[A-Za-z.\-]+){0,5}", name):
        return False
    return True


def first_chunk(authors: str) -> str:
    src = (authors or "").strip()
    src = re.sub(r"(?is)\\thanks\{.*?\}", "", src)
    src = re.sub(r"(?is)\\textsuperscript\{.*?\}", "", src)
    src = re.sub(r"[*$†‡§¶]+", "", src)
    src = re.sub(r"\s+", " ", src).strip(" ,;.|%")
    src = _NAME_JUNK_RE.sub("", src).strip(" ,;.|%")
    if not src:
        return ""
    for sep in [r"\bet\s+al\.?\b", r"\s+and\s+", r"\s+\&\s+", r"\s*;\s*"]:
        parts = re.split(sep, src, maxsplit=1, flags=re.I)
        if len(parts) > 1 and parts[0].strip():
            src = parts[0].strip(" ,;.|%")
            break
    if re.match(r"^[^,]+,\s*([A-Z]\.?\s*)+$", src):
        return src.strip()
    if "," in src:
        m = re.match(r"^([^,]+,\s*(?:[A-Z]\.?\s*)+)(?:,|$)", src)
        if m:
            return m.group(1).strip(" ,;.|%")
        src = re.split(r",\s*", src, maxsplit=1)[0].strip(" ,;.|%")
    return src


def expand_from_email(name: str, tex: str) -> str:
    if not name or not mostly_initials(name):
        return name
    parts = name.split()
    if not parts:
        return name
    surname = re.sub(r"[^A-Za-z\-]", "", parts[-1])
    if len(surname) < 3:
        return name
    sur_cf = surname.casefold().replace("-", "")
    for local in re.findall(r"([A-Za-z][A-Za-z0-9._-]{1,40})@[A-Za-z0-9.-]+", tex):
        compact = local.casefold().replace("-", "").replace(".", "").replace("_", "")
        if sur_cf not in compact:
            continue
        bits = [b for b in re.split(r"[._]", local) if b]
        if len(bits) >= 2 and len(bits[0]) >= 3 and bits[0].isalpha():
            g = bits[0]
            return f"{g[0].upper()}{g[1:].casefold()} {parts[-1]}"
    return name


def clean_person(block: str) -> str:
    plain = tex_to_plain(block)
    plain = plain.replace(")", " ").replace("(", " ")
    plain = _NAME_JUNK_RE.sub("", plain).strip(" ,;.|%")
    plain = re.split(
        r"(?i)\b(department|departement|faculty|university|college|school|"
        r"k\.?\s*n\.?\s*toosi|amirkabir|sharif)\b",
        plain,
        maxsplit=1,
    )[0].strip(" ,;.|%")
    chunk = first_chunk(plain)
    chunk = strip_titles(chunk)
    chunk = re.sub(r"\s*:\s*\S*$", "", chunk)
    chunk = re.sub(r"\s+\d+\s*$", "", chunk)
    chunk = re.sub(r"\s+", " ", chunk).strip(" ,;.|%")
    m = re.match(r"^(.+?),\s*((?:[A-Z]\.?\s*)+)$", chunk)
    if m:
        family = m.group(1).strip()
        given = re.sub(r"\s+", " ", m.group(2)).strip()
        given = re.sub(r"\b([A-Za-z])\.?\b", lambda x: x.group(1).upper() + ".", given)
        chunk = f"{given} {family}".strip()
    tokens = []
    for t in chunk.replace(",", " ").split():
        tl = t.lower().strip(".")
        if tl in {"dr", "prof", "professor", "mr", "mrs", "ms", "miss", "eng", "sir", "phd"}:
            continue
        if re.fullmatch(r"\d+", t):
            continue
        tokens.append(t.strip(" ,;|%"))
    while tokens and re.fullmatch(r"[A-Za-z]", tokens[-1]):
        tokens.pop()
    # "Mohsen Mohammadzadeh mohsen" ← email local leftover
    if len(tokens) >= 3 and tokens[-1].casefold() == tokens[0].casefold():
        tokens = tokens[:-1]
    elif len(tokens) >= 3 and tokens[-1][0].islower() and tokens[-1].casefold() in {
        t.casefold() for t in tokens[:-1]
    }:
        tokens = tokens[:-1]
    plain_toks = [t for t in tokens if re.fullmatch(r"[A-Za-z]{2,}", t)]
    if len(tokens) == 4 and len(plain_toks) == 4:
        tokens = tokens[:2]
    name = " ".join(tokens).strip(" ,;.|%")
    if looks_like_name(name) or (name and mostly_initials(name) and len(name.split()) >= 2):
        return name
    return ""


def candidates_from_tex(tex: str) -> list[str]:
    cands: list[tuple[int, str]] = []
    abs_m = re.search(r"(?i)\\noindent\{\\bf\s*Abstract|\\begin\{abstract\}", tex)
    head = tex[: abs_m.start()] if abs_m else tex[:12000]

    def add(raw: str, priority: int) -> None:
        n = clean_person(raw)
        if n:
            cands.append((priority, n))

    for m in re.finditer(
        r"(?is)Article authors and affiliations[^\n]*\n((?:[^\n]*\n){1,15})",
        head,
    ):
        lines = []
        for ln in m.group(1).splitlines():
            ln2 = re.sub(r"^%\s?", "", ln)
            if re.search(r"(?i)abstract|keyword", ln2):
                break
            lines.append(ln2)
        add("\n".join(lines), 3)

    for m in re.finditer(r"\{\\bf\b", head):
        pre = head[max(0, m.start() - 24) : m.start()]
        if re.search(r"\\Large|\\huge|\\LARGE|\\noindent", pre):
            continue
        arg = balanced_arg(head, m.start())
        if not arg or len(arg) > 600:
            continue
        plain0 = tex_to_plain(arg)[:48]
        if re.match(r"(?i)(abstract|keyword|msc|theorem|lemma|proof)", plain0):
            continue
        if re.search(
            r"(?i)Corresponding Author|\\footnote|\$\^\d|@[A-Za-z]",
            arg,
        ) or re.search(r"[A-Z][a-z]{2,}\s+[A-Z]", arg):
            add(arg, 2)

    for m in re.finditer(r"(?im)^%\s*(?:\{\\bf\s*)?([A-Z][^%\n]{2,140})", head):
        line = m.group(1)
        if _SKIP_RE.search(line):
            continue
        if re.search(r"(?:\\footnote|\$\^\d|@[A-Za-z]|,\s*[A-Z])", line) or re.match(
            r"^[A-Z][a-zA-Z.\-]+\s+[A-Z]", line
        ):
            add(line, 2)

    for m in re.finditer(r"\\author\s*\{", head):
        arg = balanced_arg(head, m.end() - 1)
        if arg:
            add(arg, 3)

    ranked = sorted(cands, key=lambda t: (t[0], fullness(t[1]), len(t[1])), reverse=True)
    out: list[str] = []
    seen = set()
    for _, c in ranked:
        k = c.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


def normalize_display(name: str) -> str:
    if not name:
        return name
    name = re.sub(r"\b([A-Za-z])\.([A-Za-z]{2,})", r"\1. \2", name)
    return re.sub(r"\s+", " ", name).strip()


def from_tex(tex: str) -> str:
    cands = candidates_from_tex(tex)
    if not cands:
        return ""
    ranked = sorted(cands, key=lambda n: (fullness(n), len(n)), reverse=True)
    best = expand_from_email(ranked[0], tex)
    if mostly_initials(best):
        for c in ranked:
            exp = expand_from_email(c, tex)
            if fullness(exp) > fullness(best):
                best = exp
    return normalize_display(best) if fullness(best) >= 0 else ""


def from_metadata(authors: str = "", header_authors: str = "", first_author: str = "") -> str:
    src = authors or first_author or header_authors or ""
    name = clean_person(src)
    if not name and first_author:
        name = clean_person(first_author)
    return normalize_display(name)


def resolve(
    paper_id: str,
    main_dir: Path,
    record: dict | None = None,
    refresh: bool = False,
) -> str:
    """
    TOC display name.

    If ``toc_manual`` is true, always use metadata ``toc_author``.
    If ``toc_author`` is set and ``refresh`` is false, keep it.
    Otherwise auto-extract from LaTeX / other fields.
    """
    record = record or {}
    existing = (record.get("toc_author") or "").strip()
    if record.get("toc_manual") and existing:
        return normalize_display(existing)
    if existing and not refresh:
        return normalize_display(existing)

    folder = main_dir / paper_id
    tex_path = folder / f"{paper_id}.tex"
    tex = ""
    if tex_path.exists():
        tex = tex_path.read_text(encoding="utf-8", errors="replace")
    else:
        for p in sorted(folder.rglob("*.tex")):
            if "booklet" in p.name.lower():
                continue
            tex = p.read_text(encoding="utf-8", errors="replace")
            break

    tex_name = from_tex(tex) if tex else ""
    meta_name = from_metadata(
        record.get("authors") or "",
        record.get("header_authors") or "",
        record.get("first_author") or "",
    )
    if tex and meta_name and mostly_initials(meta_name):
        meta_name = expand_from_email(meta_name, tex)

    if tex_name and (
        not meta_name
        or fullness(tex_name) > fullness(meta_name)
        or (mostly_initials(meta_name) and not mostly_initials(tex_name))
        or (
            fullness(tex_name) == fullness(meta_name)
            and len(tex_name) >= len(meta_name)
        )
    ):
        return normalize_display(tex_name)
    return normalize_display(meta_name or tex_name)
