#!/usr/bin/env python3
"""Restore double-spaced / corrupted main TeX files from staging zips."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

MAIN = Path(r"i:\Booklet ENG\ISC18th_Full_English\main")
STAGING = Path(r"i:\Booklet ENG\_staging_papers")
RAW = Path(r"i:\Booklet ENG\_extracted_raw")


def empty_ratio(text: str) -> float:
    lines = text.splitlines()
    if not lines:
        return 0.0
    empty = sum(1 for l in lines if not l.strip())
    return empty / len(lines)


def read_text_bytes(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "latin-1", "cp1256"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def score_tex_candidate(name: str, data: bytes) -> int:
    low = name.lower().replace("\\", "/")
    if "__macosx" in low or low.endswith("/"):
        return -1
    if not low.endswith(".tex"):
        return -1
    text = read_text_bytes(data)
    if r"\begin{document}" not in text:
        return -1
    score = len(data)
    base = Path(low).name
    if "conference" in base:
        score += 5_000_000
    if "template" in base:
        score -= 3_000_000
    if "won" in base:
        score -= 1_000_000
    # Prefer denser (not double-spaced) sources
    score -= int(empty_ratio(text) * 2_000_000)
    if r"\includegraphics" in text:
        score += 200_000
    return score


def best_tex_from_zip(zpath: Path) -> tuple[str, bytes] | None:
    try:
        with zipfile.ZipFile(zpath) as zf:
            cands = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                data = zf.read(info)
                sc = score_tex_candidate(info.filename, data)
                if sc > 0:
                    cands.append((sc, info.filename, data))
            if not cands:
                return None
            cands.sort(key=lambda t: t[0], reverse=True)
            return cands[0][1], cands[0][2]
    except Exception as e:
        print("zip fail", zpath, e)
        return None


def best_tex_from_raw(num: str) -> tuple[str, bytes] | None:
    root = RAW / num
    if not root.exists():
        return None
    cands = []
    for p in root.rglob("*.tex"):
        if "__MACOSX" in str(p):
            continue
        data = p.read_bytes()
        sc = score_tex_candidate(str(p), data)
        if sc > 0:
            cands.append((sc, str(p), data))
    if not cands:
        return None
    cands.sort(key=lambda t: t[0], reverse=True)
    return cands[0][1], cands[0][2]


def undouble_spaced(text: str) -> str:
    """If file is almost every-other-line blank, collapse those blanks."""
    lines = text.splitlines()
    if empty_ratio(text) < 0.45:
        return text
    # Collapse only when pattern is content/blank/content/blank...
    out = []
    i = 0
    while i < len(lines):
        out.append(lines[i])
        if (
            i + 1 < len(lines)
            and lines[i].strip()
            and not lines[i + 1].strip()
            and (i + 2 >= len(lines) or lines[i + 2].strip())
        ):
            i += 2
        else:
            i += 1
    return "\n".join(out) + "\n"


def main() -> None:
    restored = []
    collapsed = []
    still_bad = []

    for folder in sorted(MAIN.glob("p.*")):
        if not folder.is_dir():
            continue
        pid = folder.name
        num = pid.split(".", 1)[1]
        tex_path = folder / f"{pid}.tex"
        if not tex_path.exists():
            continue
        cur = read_text_bytes(tex_path.read_bytes()).replace("\r\n", "\n").replace("\r", "\n")
        ratio = empty_ratio(cur)
        if ratio < 0.45 and "Paragraph ended before" not in cur:
            # still strip main/p.XXX/ if any
            continue

        src = None
        zpath = STAGING / f"{num}.zip"
        if zpath.exists():
            src = best_tex_from_zip(zpath)
        if src is None:
            src = best_tex_from_raw(num)

        if src is not None:
            name, data = src
            text = read_text_bytes(data).replace("\r\n", "\n").replace("\r", "\n")
            text = re.sub(r"main/+p\.\d+/+", "", text, flags=re.I)
            # If archive copy itself is double-spaced, collapse
            if empty_ratio(text) >= 0.45:
                text = undouble_spaced(text)
            tex_path.write_text(text, encoding="utf-8", newline="\n")
            restored.append((pid, name, round(empty_ratio(text), 2), round(ratio, 2)))
        else:
            # last resort: collapse current file
            fixed = undouble_spaced(cur)
            fixed = re.sub(r"main/+p\.\d+/+", "", fixed, flags=re.I)
            if empty_ratio(fixed) < ratio:
                tex_path.write_text(fixed, encoding="utf-8", newline="\n")
                collapsed.append((pid, round(empty_ratio(fixed), 2), round(ratio, 2)))
            else:
                still_bad.append((pid, round(ratio, 2)))

    print("restored from zip/raw", len(restored))
    for x in restored[:30]:
        print(" ", x)
    print("collapsed only", len(collapsed))
    for x in collapsed[:20]:
        print(" ", x)
    print("still bad", still_bad)


if __name__ == "__main__":
    main()
